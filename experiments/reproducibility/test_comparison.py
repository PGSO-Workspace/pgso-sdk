#!/usr/bin/env python3
"""Dependency-free regression checks for comparator validation and evidence gates."""
import copy
import unittest
from comparator_adapters import evaluate, validate_payload
from compare_lifecycle import fixtures, projection


class ComparisonTests(unittest.TestCase):
    def test_receipts_and_missing_outputs_cannot_pass(self):
        episode={k:v for k,v in fixtures()[0].items() if k != "expected"}
        result=evaluate(validate_payload([episode])[0],"voice_agnostic",lambda *_:"allow")
        projection([result],[episode])
        for corrupt in ([], [dict(result, outputs=[])], [result,result]):
            with self.assertRaises(ValueError):
                projection(corrupt,[episode])
        forged=copy.deepcopy(result)
        forged["outputs"][0]["callback_delta"]=0
        with self.assertRaises(ValueError):
            projection([forged],[episode])
        forged=copy.deepcopy(result)
        forged["outputs"][0]["receipt"]["ordinal"]=99
        with self.assertRaises(ValueError):
            projection([forged],[episode])

    def test_labels_and_invalid_units_never_reach_policy(self):
        episode={k:v for k,v in fixtures()[0].items() if k != "expected"}
        for payload in ([],[episode,episode],[dict(episode,expected=[])],
                        [dict(episode,id=" ")],
                        [dict(id="bad",events=[dict(kind="observation",value=1.00000000001,confidence=.9,timestamp_ms=0)])],
                        [dict(id="bad",events=[dict(kind="call",tool="quote",timestamp_ms=2**64)])]):
            with self.assertRaises(ValueError):
                validate_payload(payload)


if __name__=="__main__":
    unittest.main()
