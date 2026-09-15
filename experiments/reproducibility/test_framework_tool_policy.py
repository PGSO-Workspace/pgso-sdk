#!/usr/bin/env python3
"""Focused real-framework checks for configured comparator tool sets."""
import sys

from comparator_adapters import _invariant_decider, _nemo_decider


GOVERNED_TOOLS = {
    "suspend_line",
    "resume_line",
    "send_payment_request",
    "enable_roaming",
    "disable_roaming",
    "refuel_data",
}


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"nemo", "invariant"}:
        raise SystemExit("usage: test_framework_tool_policy.py nemo|invariant")
    factory = _nemo_decider if sys.argv[1] == "nemo" else _invariant_decider

    decide = factory(GOVERNED_TOOLS)
    for tool in GOVERNED_TOOLS:
        assert decide(tool, True) == "block"
        assert decide(tool, False) == "allow"
    for tool in ("get_customer_by_phone", "transfer_to_human_agents"):
        assert decide(tool, True) == "allow"
        assert decide(tool, False) == "allow"

    legacy = factory()
    assert legacy("quote", True) == "block"
    assert legacy("quote", False) == "allow"
    assert legacy("human", True) == "allow"

    for bad in ("quote\nraise injected", "two words", "", 7):
        try:
            factory({bad})
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted malformed configured tool: {bad!r}")
        try:
            decide(bad, True)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted malformed candidate tool: {bad!r}")
    for bad in (0, 1, "true", None):
        try:
            decide("enable_roaming", bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted non-boolean governed state: {bad!r}")

    print(f"{sys.argv[1]} configured-tool policy checks passed")


if __name__ == "__main__":
    main()
