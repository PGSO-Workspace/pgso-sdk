#!/usr/bin/env python3
"""Finite matched-policy dispatch comparison; no audio or human-utility claim."""
import argparse
import json
import os
from pathlib import Path
import sys
import subprocess
from datetime import datetime, timezone
from run import ROOT, HERE, command, require, save, sha, source_hashes


def fixtures():
    """Authored call expectations, separate from controller implementations."""
    def reading(t, v=.95, c=.9):
        return dict(kind="observation", timestamp_ms=t, value=v, confidence=c)
    high = [reading(t) for t in (400, 800, 1200)]
    cases = []
    def case(name, sections):
        events, labels = [], []
        for observations, t, blocked in sections:
            events.extend(observations)
            events.extend(dict(kind="call", tool=tool, timestamp_ms=t) for tool in ("quote", "human"))
            labels.extend(["block" if blocked else "allow", "allow"])
        cases.append(dict(id=name, events=events, expected=labels))
    case("nominal", [([reading(400, .5)],400,False)])
    case("cold_low_confidence", [([reading(400,.95,.49)],400,False)])
    case("sustained_and_recovery", [(high[:1],400,False),(high[1:2],800,False),(high[2:],1200,True),([reading(1600)],1600,True),([reading(2000,.5)],2000,False)])
    case("isolated_spike", [([reading(400)],400,False),([reading(800,.5)],800,False)])
    case("low_confidence_holds_active", [(high,1200,True),([reading(1600,.5,.49)],1600,True)])
    case("low_confidence_preserves_pending", [(high[:2],800,False),([reading(1000,.5,.49),reading(1200)],1200,True)])
    case("backward_holds_active", [(high,1200,True),([reading(1100,.5)],1300,True)])
    case("backward_preserves_pending", [(high[:2],800,False),([reading(700,.5),reading(1200)],1200,True)])
    case("silence_holds_active", [(high,1200,True),([dict(kind="silence")],5000,True)])
    case("gap_resets_pending", [(high[:2],800,False),([reading(2001)],2001,False),([reading(2401)],2401,False),([reading(2801)],2801,True)])
    case("gap_preserves_active", [(high,1200,True),([reading(2401)],2401,True),([reading(2801,.5)],2801,False)])
    case("gap_boundary_inclusive", [([reading(400),reading(1600),reading(2800)],2800,True)])
    case("expire_strict_boundary", [(high,1200,True),([dict(kind="expire",cutoff_ms=1200)],1201,True),([dict(kind="expire",cutoff_ms=1201)],1202,False)])
    case("expiry_retains_engine_counter", [(high,1200,True),([dict(kind="expire",cutoff_ms=1201)],1202,False),([reading(1600)],1600,True)])
    case("expiry_uses_contribution_age", [(high,1200,True),([reading(2401)],2401,True),([dict(kind="expire",cutoff_ms=1201)],2402,False)])
    case("falling_does_not_trigger_rising_rule", [([reading(t,.05) for t in (400,800,1200)],1200,False)])
    case("opposite_direction_pending_hold", [(high,1200,True),([reading(1600,.05)],1600,True),([reading(2000,.05)],2000,True),([reading(2400,.05)],2400,False)])
    case("confidence_boundary", [([reading(t,.95,.5) for t in (400,800,1200)],1200,True)])
    case("value_boundary", [([reading(t,.8) for t in (400,800,1200)],1200,True)])
    case("below_value_boundary", [([reading(t,.799999) for t in (400,800,1200)],1200,False)])
    case("equal_timestamps_count_as_readings", [([reading(400) for _ in range(3)],400,True)])
    case("expiry_without_policy", [([dict(kind="expire",cutoff_ms=5000)],5000,False)])
    case("sustained_long", [(high,1200,True),([reading(t) for t in range(1600,20400,400)],20400,True)])
    return cases


def projection(result, episodes):
    require(isinstance(result,list) and len(result)==len(episodes),"Missing episode outputs")
    by_id={e["id"]:e for e in episodes}
    seen,projected=set(),{}
    for item in result:
        ident=item["id"]
        require(ident in by_id and ident not in seen,"Unexpected/duplicate episode identity")
        seen.add(ident)
        calls=[e for e in by_id[ident]["events"] if e["kind"]=="call"]
        require(len(item["outputs"])==len(calls),"Missing/extra call output")
        count,rows=0,[]
        for actual,call in zip(item["outputs"],calls):
            require(actual["tool"]==call["tool"] and actual["timestamp_ms"]==call["timestamp_ms"],"Call identity mismatch")
            action=actual["action"]
            require(action in ("allow","block"),"Unknown action")
            delta=actual["callback_delta"]
            require(type(delta) is int and delta==int(action=="allow"),"Dispatch boundary violated")
            count+=delta
            receipt=dict(tool=call["tool"],ordinal=count) if delta else None
            require(actual["receipt"]==receipt,"Callback receipt mismatch")
            rows.append(dict(tool=call["tool"],timestamp_ms=call["timestamp_ms"],action=action,callback_delta=delta,receipt=receipt))
        require(type(item["callback_count"]) is int and item["callback_count"]==count,"Callback count mismatch")
        projected[ident]=rows
    return projected


def environment(python,module):
    code="""import importlib,importlib.metadata as m,hashlib,json,pathlib,sys
root=pathlib.Path(importlib.import_module(sys.argv[1]).__file__).parent
files={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob('*')) if p.is_file() and p.suffix in ('.py','.co','.yaml','.yml','.json')}
print(json.dumps(dict(python=sys.version,distributions=sorted((d.metadata['Name'],d.version) for d in m.distributions()),source_sha256=files),sort_keys=True))
"""
    return json.loads(command([python,"-c",code,module],timeout=90))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nemo-python",required=True)
    parser.add_argument("--invariant-python",required=True)
    parser.add_argument("--output",type=Path)
    parser.add_argument("--profile",choices=("dev","release"),default="release")
    args=parser.parse_args()
    out=(args.output or ROOT/"target/lifecycle-comparison"/datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")).resolve()
    out.mkdir(parents=True,exist_ok=False)
    manifest=dict(git_commit=command(["git","rev-parse","HEAD"]).strip(),git_status=command(["git","status","--porcelain"]),source_sha256=source_hashes(),rustc=command(["rustc","-vV"]),profile=args.profile,compiler_environment={k:os.environ[k] for k in ("RUSTFLAGS","CARGO_ENCODED_RUSTFLAGS","CARGO_BUILD_TARGET","CARGO_TARGET_DIR") if k in os.environ})
    results=dict(status="failed",scope="Authored fixed-baseline lifecycle and real in-memory dispatch; no conversational utility or native-platform superiority inference")
    try:
        build=["cargo","build","--locked","--profile",args.profile,"-p","pgso-actuator-http","--example","lifecycle_comparison","--message-format=json"]
        artifacts=[json.loads(line) for line in command(build,timeout=600).splitlines()]
        bins=[a["executable"] for a in artifacts if a.get("reason")=="compiler-artifact" and a.get("executable")]
        require(len(bins)==1,"Expected one comparison executable")
        manifest.update(build_command=build,binary_sha256=sha(Path(bins[0])))
        cases=fixtures()
        episodes=[dict(id=c["id"],events=c["events"]) for c in cases]
        require(episodes and len({e['id'] for e in episodes})==len(episodes),"Empty/duplicate fixture")
        save(out,"contract-cases.json",cases)
        save(out,"episodes.json",episodes)
        save(out,"episodes-reversed.json",list(reversed(episodes)))
        adapter=str(HERE/"comparator_adapters.py")
        engines={"pgso":[bins[0]],"nemo":[args.nemo_python,adapter,"nemo"],"invariant":[args.invariant_python,adapter,"invariant"],"threshold":[sys.executable,adapter,"threshold"],"voice_agnostic":[sys.executable,adapter,"voice_agnostic"]}
        manifest["environments"]={name:environment(python,module) for name,python,module in (("nemo",args.nemo_python,"nemoguardrails"),("invariant",args.invariant_python,"invariant"))}
        summaries,differences={},[]
        def execute(cmd, name, expected_success=True):
            completed=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,timeout=480,
                env=dict(os.environ,HF_HUB_OFFLINE="1",TRANSFORMERS_OFFLINE="1",HF_HUB_DISABLE_TELEMETRY="1"))
            (out/(name+".stderr.txt")).write_text(completed.stderr)
            require((completed.returncode==0)==expected_success,
                    f"Unexpected process status for {name}: {completed.returncode}; see saved stderr")
            return completed.stdout
        invalid={
            "empty":[], "duplicate":[episodes[0],episodes[0]],
            "blank_id":[dict(id=" ",events=[dict(kind="silence")])],
            "labels":[dict(**episodes[0],expected=[])],
            "event_fields":[dict(id="bad",events=[dict(kind="silence",expected="allow")])],
            "range":[dict(id="bad",events=[dict(kind="observation",timestamp_ms=0,value=1.00000000001,confidence=.9)])],
            "timestamp":[dict(id="bad",events=[dict(kind="call",tool="quote",timestamp_ms=2**64)])],
        }
        for label,payload in invalid.items():
            save(out,"invalid-"+label+".json",payload)
        for name,cmd in engines.items():
            raw=json.loads(execute(cmd+[str(out/"episodes.json")],name+"-forward"))
            save(out,name+"-outputs.json",raw)
            rows=projection(raw,episodes)
            reverse=json.loads(execute(cmd+[str(out/"episodes-reversed.json")],name+"-reverse"))
            require(rows==projection(reverse,list(reversed(episodes))),name+" replay/order mismatch")
            for label in invalid:
                execute(cmd+[str(out/("invalid-"+label+".json"))],name+"-reject-"+label,False)
            missed=extra=allowed=blocked=0
            for case in cases:
                require(len(rows[case["id"]])==len(case["expected"]),"Oracle count mismatch")
                for index,(row,expected) in enumerate(zip(rows[case["id"]],case["expected"])):
                    actual=row["action"]
                    missed+=expected=="block" and actual=="allow"
                    extra+=expected=="allow" and actual=="block"
                    allowed+=actual=="allow"
                    blocked+=actual=="block"
                    if actual!=expected:
                        differences.append(dict(controller=name,episode=case["id"],call=index,expected=expected,actual=actual))
            summaries[name]=dict(episodes=len(cases),calls=allowed+blocked,callbacks=allowed,blocked=blocked,missed_blocks=missed,unnecessary_blocks=extra,fresh_process_replays=2,rejected_invalid_inputs=len(invalid))
        save(out,"differences.json",differences)
        results["controllers"]=summaries
        require(all(summaries[n]["missed_blocks"]==summaries[n]["unnecessary_blocks"]==0 for n in ("pgso","nemo","invariant")),"Matched contract failure; inspect differences")
        require(source_hashes()==manifest["source_sha256"],"Source changed during run")
        require(manifest["environments"]=={name:environment(python,module) for name,python,module in (("nemo",args.nemo_python,"nemoguardrails"),("invariant",args.invariant_python,"invariant"))},"Vendor environment changed during run")
        results["status"]="passed"
    finally:
        save(out,"results.json",results)
        manifest["output_sha256"]={p.name:sha(p) for p in sorted(out.iterdir()) if p.is_file()}
        save(out,"manifest.json",manifest)
    print(json.dumps(dict(output=str(out),**results),indent=2))


if __name__=="__main__":
    main()
