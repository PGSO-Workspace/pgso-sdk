"""Regression checks for framed measurement transport and process-block summaries."""
import subprocess
import sys
import unittest
from measure_operations import Pipe, interval, quantile


class MeasurementTests(unittest.TestCase):
    def test_partial_pipe_payload_is_fully_exchanged(self):
        code='import sys\nprint("ready",flush=True)\nfor line in sys.stdin:\n sys.stdout.write(line); sys.stdout.flush()'
        process=subprocess.Popen([sys.executable,'-u','-c',code],stdin=subprocess.PIPE,stdout=subprocess.PIPE)
        try:
            pipe=Pipe(process)
            self.assertEqual(pipe.exchange(timeout=2),b'ready')
            payload=b'x'*200000+b'\n'
            self.assertEqual(pipe.exchange(payload,timeout=2),payload[:-1])
            process.stdin.close()
            self.assertEqual(process.wait(timeout=2),0)
        finally:
            if process.poll() is None:
                process.kill();process.wait(timeout=2)
            for stream in (process.stdin,process.stdout):
                stream.close()

    def test_stalled_worker_times_out(self):
        process=subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)'],stdin=subprocess.PIPE,stdout=subprocess.PIPE)
        try:
            with self.assertRaises(TimeoutError):
                Pipe(process).exchange(timeout=.02)
        finally:
            process.kill();process.wait(timeout=2)
            process.stdin.close();process.stdout.close()

    def test_quantile_and_bootstrap_contract(self):
        self.assertEqual(quantile([4,1,3,2],.5),2.5)
        self.assertEqual(interval([7,7,7],123,100),[7,7])
        with self.assertRaises(ValueError):
            quantile([],.5)


if __name__=='__main__':
    unittest.main()
