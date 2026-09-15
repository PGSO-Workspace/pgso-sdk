#!/usr/bin/env python3
"""Linux exploratory integration-path measurements with persistent real workers."""
import argparse
import json
import math
import os
from pathlib import Path
import random
import selectors
import signal
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from compare_lifecycle import fixtures, projection, environment
from run import ROOT, HERE, command, require, save, sha, source_hashes


class Pipe:
    def __init__(self, process):
        self.process=process
        self.pending=b""
        os.set_blocking(process.stdin.fileno(),False)
        os.set_blocking(process.stdout.fileno(),False)

    def exchange(self, payload=b"", timeout=90):
        deadline=time.monotonic()+timeout
        sent=0
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout,selectors.EVENT_READ)
            if payload:
                selector.register(self.process.stdin,selectors.EVENT_WRITE)
            while True:
                if b"\n" in self.pending and sent==len(payload):
                    line,self.pending=self.pending.split(b"\n",1)
                    return line
                remaining=deadline-time.monotonic()
                if remaining<=0:
                    raise TimeoutError("worker exchange timed out")
                for key,event in selector.select(remaining):
                    if event & selectors.EVENT_WRITE:
                        sent+=os.write(key.fd,payload[sent:])
                        if sent==len(payload):
                            selector.unregister(self.process.stdin)
                    if event & selectors.EVENT_READ:
                        chunk=os.read(key.fd,65536)
                        if not chunk:
                            raise RuntimeError("worker closed stdout before a complete response")
                        self.pending+=chunk
                        require(len(self.pending)<16*1024*1024,"oversized worker response")


def snapshot(pid, cpu):
    base=Path('/proc')/str(pid)
    stat=(base/'stat').read_text().rsplit(')',1)[1].split()
    info={}
    for line in (base/'status').read_text().splitlines():
        if line.startswith(('VmRSS:','VmHWM:','Threads:')):
            key,value,*_=line.split()
            info[key[:-1]]=int(value)
    tids=[int(p.name) for p in (base/'task').iterdir()]
    require(all(os.sched_getaffinity(tid)=={cpu} for tid in tids),"worker thread affinity changed")
    sched_ns={str(tid):int((base/'task'/str(tid)/'schedstat').read_text().split()[0]) for tid in tids}
    require(sum(sched_ns.values())>0,'scheduler CPU accounting unavailable')
    children=[]
    for tid in tids:
        children.extend((base/'task'/str(tid)/'children').read_text().split())
    require(not children,"worker child processes require a process-tree measurement protocol")
    return dict(cpu_ticks=sum(int(stat[i]) for i in (11,12)),
                waited_child_cpu_ticks=sum(int(stat[i]) for i in (13,14)),
                rss_bytes=info['VmRSS']*1024,peak_rss_bytes=info['VmHWM']*1024,
                threads=info['Threads'],thread_sched_ns=sched_ns,affinity=[cpu],observed_children=children)


def quantile(values,p):
    require(bool(values),"empty quantile")
    seq=sorted(values)
    at=(len(seq)-1)*p
    low=math.floor(at)
    return seq[low]+(seq[math.ceil(at)]-seq[low])*(at-low)


def interval(values,seed,draws=2000):
    rng=random.Random(seed)
    samples=[statistics.median(rng.choices(values,k=len(values))) for _ in range(draws)]
    return [quantile(samples,.025),quantile(samples,.975)]


def metadata():
    paths=['/proc/cpuinfo','/proc/loadavg','/proc/self/cgroup','/sys/fs/cgroup/cpu.max',
           '/sys/fs/cgroup/cpu/cpu.cfs_quota_us','/sys/fs/cgroup/cpu/cpu.cfs_period_us',
           '/sys/devices/system/cpu/cpufreq/boost']
    paths += [str(p) for p in Path('/sys/devices/system/cpu').glob('cpu*/cpufreq/scaling_governor')]
    result={}
    for path in paths:
        try:
            result[path]=Path(path).read_text()
        except OSError:
            result[path]=None
    return result


def validate_response(raw,episodes,cases):
    result=projection(json.loads(raw),episodes)
    for case in cases:
        require([r['action'] for r in result[case['id']]]==case['expected'],"worker policy mismatch")
    return result


def measure(cmd,name,block,episodes,cases,args,out):
    payload=(json.dumps(episodes,separators=(',',':'))+'\n').encode()
    result=dict(controller=name,block=block,status='failed',latency_ns=[],warmup_batches=args.warmup,
                requested_batches=args.batches,scenario_order=[e['id'] for e in episodes],request_bytes=len(payload))
    process=None
    pipe=None
    raw=None
    phase='startup_ready'
    batch_index=None
    try:
        with (out/f'{block:02d}-{name}.stderr.txt').open('wb') as errors:
            started=time.perf_counter_ns()
            process=subprocess.Popen(['taskset','-c',str(args.cpu),*cmd],cwd=ROOT,stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,stderr=errors,start_new_session=True,
                env=dict(os.environ,HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1'))
            pipe=Pipe(process)
            require(json.loads(pipe.exchange(timeout=args.timeout))=={'ready':True},"bad readiness message")
            result['ready_ns']=time.perf_counter_ns()-started
            phase='first_batch'
            first_started=time.perf_counter_ns()
            first=pipe.exchange(payload,args.timeout)
            raw=first
            result['first_batch_ns']=time.perf_counter_ns()-first_started
            result['startup_through_first_batch_ns']=time.perf_counter_ns()-started
            expected=validate_response(first,episodes,cases)
            (out/f'{block:02d}-{name}.response.json').write_bytes(first+b'\n')
            result['response_bytes']=len(first)+1
            phase='warmup'
            for batch_index in range(args.warmup):
                raw=None
                raw=pipe.exchange(payload,args.timeout)
                require(validate_response(raw,episodes,cases)==expected,"warmup changed outcomes")
            result['before']=snapshot(process.pid,args.cpu)
            phase='measured'
            for batch_index in range(args.batches):
                raw=None
                begin=time.perf_counter_ns()
                raw=pipe.exchange(payload,args.timeout)
                elapsed=time.perf_counter_ns()-begin
                result['latency_ns'].append(elapsed)
                require(validate_response(raw,episodes,cases)==expected,"measured batch changed outcomes")
                require(len(raw)+1==result['response_bytes'],"response size changed")
            result['after']=snapshot(process.pid,args.cpu)
            ticks=result['after']['cpu_ticks']-result['before']['cpu_ticks']
            before_threads=result['before']['thread_sched_ns']
            after_threads=result['after']['thread_sched_ns']
            require(set(before_threads)==set(after_threads),'thread population changed during CPU window')
            require(all(after_threads[t]>=before_threads[t] for t in before_threads),'CPU accounting moved backward')
            result['cpu_window_ns']=sum(after_threads[t]-before_threads[t] for t in before_threads)
            result['cpu_stat_window_ns']=ticks*1e9/os.sysconf('SC_CLK_TCK')
            result['cpu_source']='sum of stable Linux thread schedstat runtimes; snapshot thread population checks'
            result['cpu_tick_ns']=1e9/os.sysconf('SC_CLK_TCK')
            result['cpu_ns_per_batch']=result['cpu_window_ns']/args.batches
            result['p50_batch_ns']=quantile(result['latency_ns'],.5)
            result['p95_batch_ns']=quantile(result['latency_ns'],.95)
            result['p99_batch_ns']=quantile(result['latency_ns'],.99)
            result['max_batch_ns']=max(result['latency_ns'])
            process.stdin.close()
            require(process.wait(timeout=args.timeout)==0,"worker exit failed")
            result['status']='passed'
    except Exception as error:
        result['error']=f'{type(error).__name__}: {error}'
        result['failure_phase']=phase
        result['failure_batch_index']=batch_index
        (out/f'{block:02d}-{name}.failure-response.bin').write_bytes(raw if raw is not None else (pipe.pending if pipe else b''))
    finally:
        if process is not None:
            try:
                os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:
                pass
            if process.poll() is None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)
            try:
                os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:
                pass
            for stream in (process.stdin,process.stdout):
                if stream and not stream.closed:
                    stream.close()
        save(out,f'{block:02d}-{name}.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nemo-python',required=True)
    parser.add_argument('--invariant-python',required=True)
    parser.add_argument('--runs',type=int,default=12)
    parser.add_argument('--batches',type=int,default=100)
    parser.add_argument('--warmup',type=int,default=5)
    parser.add_argument('--seed',type=int,default=20260915)
    parser.add_argument('--cpu',type=int,default=max(os.sched_getaffinity(0)))
    parser.add_argument('--parent-cpu',type=int,default=min(os.sched_getaffinity(0)))
    parser.add_argument('--timeout',type=float,default=90)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    require(args.runs>=2 and args.batches>=2 and args.warmup>=1 and math.isfinite(args.timeout) and args.timeout>0,'invalid measurement parameters')
    initial_affinity=os.sched_getaffinity(0)
    require(args.cpu in initial_affinity and args.parent_cpu in initial_affinity and args.cpu!=args.parent_cpu,'choose two allowed distinct CPUs')
    out=(args.output or ROOT/'target/operational-measurement'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')).resolve()
    out.mkdir(parents=True,exist_ok=False)
    manifest=dict(git_commit=command(['git','rev-parse','HEAD']).strip(),git_status=command(['git','status','--porcelain']),
        source_sha256=source_hashes(),rustc=command(['rustc','-vV']),python=sys.version,kernel=command(['uname','-a']),
        cpu=command(['lscpu']),host_before=metadata(),params={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()},
        compiler_environment={k:os.environ[k] for k in ('RUSTFLAGS','CARGO_ENCODED_RUSTFLAGS','CARGO_BUILD_TARGET','CARGO_TARGET_DIR') if k in os.environ})
    results=dict(status='failed',scope='Exploratory single-host persistent integration-path measurement; fresh logical sessions per batch, not per-call latency or human productivity')
    records=[]
    try:
        build=['cargo','build','--locked','--release','-p','pgso-actuator-http','--example','lifecycle_comparison','--message-format=json']
        artifacts=[json.loads(line) for line in command(build,timeout=600).splitlines()]
        binaries=[a['executable'] for a in artifacts if a.get('reason')=='compiler-artifact' and a.get('executable')]
        require(len(binaries)==1,'missing executable')
        manifest.update(build_command=build,binary_sha256=sha(Path(binaries[0])))
        manifest['environments']={name:environment(python,module) for name,python,module in
            [('nemo',args.nemo_python,'nemoguardrails'),('invariant',args.invariant_python,'invariant')]}
        manifest['interpreter_sha256']={str(Path(p).resolve()):sha(Path(p).resolve()) for p in (sys.executable,args.nemo_python,args.invariant_python)}
        manifest['measurement_logging']={'logger':'nemoguardrails.rails.llm.llmrails','filtered_exact_message':'No main LLM specified in the config and no LLM provided via constructor.','other_messages':'preserved in per-process stderr'}
        worker=str(HERE/'measurement_worker.py')
        engines={'pgso':[binaries[0],'--serve'],'nemo':[args.nemo_python,worker,'nemo'],
            'invariant':[args.invariant_python,worker,'invariant'],'host_policy':[args.invariant_python,worker,'host_policy']}
        require(args.runs % len(engines)==0,'runs must be divisible by four for order balance')
        cases=fixtures()
        save(out,'contract-cases.json',cases)
        rng=random.Random(args.seed)
        names=list(engines);rng.shuffle(names)
        schedule=[]
        for block in range(args.runs):
            order=names[block%len(names):]+names[:block%len(names)]
            indexes=list(range(len(cases)));rng.shuffle(indexes)
            schedule.append(dict(block=block,controllers=order,scenarios=indexes))
        manifest['schedule']=schedule
        manifest['clock_info']=dict(implementation=time.get_clock_info('perf_counter').implementation,resolution=time.get_clock_info('perf_counter').resolution)
        save(out,'manifest.json',manifest)
        os.sched_setaffinity(0,{args.parent_cpu})
        for block in schedule:
            episodes=[{k:v for k,v in cases[i].items() if k!='expected'} for i in block['scenarios']]
            for name in block['controllers']:
                item=measure(engines[name],name,block['block'],episodes,cases,args,out)
                records.append(item)
                print(json.dumps(dict(block=block['block'],controller=name,status=item['status'])),flush=True)
        require(all(r['status']=='passed' for r in records),'a process block failed; preserved outputs')
        summary={}
        for index,name in enumerate(engines):
            rows=[r for r in records if r['controller']==name]
            values=[r['p50_batch_ns'] for r in rows]
            summary[name]=dict(process_blocks=len(rows),measured_batches=len(rows)*args.batches,
                median_block_p50_batch_ms=statistics.median(values)/1e6,
                block_bootstrap_p50_median_ci95_ms=[v/1e6 for v in interval(values,args.seed+index)],
                median_block_p95_batch_ms=statistics.median(r['p95_batch_ns'] for r in rows)/1e6,
                median_block_p99_batch_ms=statistics.median(r['p99_batch_ns'] for r in rows)/1e6,
                median_startup_through_first_batch_ms=statistics.median(r['startup_through_first_batch_ns'] for r in rows)/1e6,
                median_cpu_ms_per_batch=statistics.median(r['cpu_ns_per_batch'] for r in rows)/1e6,
                median_warm_rss_mib=statistics.median(r['before']['rss_bytes'] for r in rows)/2**20,
                median_end_rss_mib=statistics.median(r['after']['rss_bytes'] for r in rows)/2**20,
                median_process_hwm_mib=statistics.median(r['after']['peak_rss_bytes'] for r in rows)/2**20)
        paired={}
        by={(r['controller'],r['block']):r for r in records}
        for index,name in enumerate(engines):
            if name=='pgso': continue
            ratios=[by[('pgso',b)]['p50_batch_ns']/by[(name,b)]['p50_batch_ns'] for b in range(args.runs)]
            paired[name]=dict(median_paired_pgso_over_comparator_ratio=statistics.median(ratios),
                paired_block_bootstrap_ci95=interval(ratios,args.seed+100+index))
        results.update(controllers=summary,paired_ratios=paired,episodes_per_batch=len(cases),calls_per_batch=sum(len(c['expected']) for c in cases),failed_blocks=0)
        require(source_hashes()==manifest['source_sha256'],'sources changed during measurement')
        require(manifest['environments']=={name:environment(python,module) for name,python,module in [('nemo',args.nemo_python,'nemoguardrails'),('invariant',args.invariant_python,'invariant')]},'framework installation changed')
        require(all(sha(Path(p))==digest for p,digest in manifest['interpreter_sha256'].items()),'Python interpreter changed during measurement')
        require(sha(Path(binaries[0]))==manifest['binary_sha256'],'worker binary changed during measurement')
        results['status']='passed'
    finally:
        os.sched_setaffinity(0,initial_affinity)
        manifest['host_after']=metadata()
        results['attempted_blocks']=len(records)
        results['failed_blocks']=sum(r['status']!='passed' for r in records)
        save(out,'results.json',results)
        manifest['output_sha256']={p.name:sha(p) for p in sorted(out.iterdir()) if p.is_file() and p.name!='manifest.json'}
        save(out,'manifest.json',manifest)
    print(json.dumps(dict(output=str(out),**results),indent=2))


if __name__=='__main__':
    main()
