#!/usr/bin/env python3
"""Persistent NDJSON worker for lifecycle measurement."""
import json
import logging
import sys

from comparator_adapters import (
    MODES,
    _invariant_decider,
    _nemo_decider,
    evaluate,
    validate_payload,
)


WORKER_MODES = MODES | {"host_policy"}


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in WORKER_MODES:
        raise SystemExit(
            "usage: measurement_worker.py nemo|invariant|threshold|voice_agnostic|host_policy"
        )
    # This no-LLM policy workload intentionally has no main model. Avoid timing
    # the repeated expected warning; retain every other framework log message.
    class ExpectedNoModel(logging.Filter):
        def filter(self, record):
            return record.getMessage() != "No main LLM specified in the config and no LLM provided via constructor."
    logging.getLogger("nemoguardrails.rails.llm.llmrails").addFilter(ExpectedNoModel())
    requested_mode = sys.argv[1]
    mode = requested_mode
    decide = (
        _nemo_decider()
        if mode == "nemo"
        else _invariant_decider()
        if mode == "invariant"
        else lambda tool, governed: "block" if tool == "quote" and governed else "allow"
    )
    print('{"ready":true}', flush=True)
    for line in sys.stdin:
        try:
            validated = validate_payload(json.loads(line))
        except ValueError as error:
            print(f"INVALID_INPUT: {error}", file=sys.stderr)
            raise SystemExit(2) from None
        results = [evaluate(episode, mode, decide) for episode in validated]
        projected = [
            {key: result[key] for key in ("id", "outputs", "callback_count")}
            for result in results
        ]
        print(json.dumps(projected, separators=(",", ":")), flush=True)



if __name__ == "__main__":
    main()
