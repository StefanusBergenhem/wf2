#!/usr/bin/env python3
"""_contract.py — the scout-output (subsystems.json) contract, checked in one place.

render.py and brief.py both consume subsystems.json. This validates that the producing
scout met the contract, so a malformed map fails with a precise, actionable message
instead of a KeyError or a silent "undefined" deep inside rendering. See
subsystems.example.json for the full, copy-pasteable shape.
"""
import sys


def _fail(msg):
    sys.exit(
        f"subsystems.json contract error: {msg}\n"
        f"  fix the scout output; see subsystems.example.json for the required shape."
    )


def validate_subsystems(sub):
    """Exit with a precise message if subsystems.json is malformed; return on success."""
    if not isinstance(sub.get("subsystems"), list):
        _fail("top-level `subsystems` must be a list")
    for i, s in enumerate(sub["subsystems"]):
        if not s.get("name"):
            _fail(f"subsystems[{i}] is missing `name`")
        if not isinstance(s.get("members"), list):
            _fail(f"subsystem {s['name']!r} is missing a `members` list")

    cd = sub.get("component_descriptions", {})
    if not isinstance(cd, dict):
        _fail("`component_descriptions` must be a map of uid -> description")

    dis = sub.get("disagreements", [])
    if not isinstance(dis, list):
        _fail("`disagreements` must be a list")
    for i, d in enumerate(dis):
        if not d.get("finding"):
            _fail(f"disagreements[{i}] is missing `finding` (a one-line description)")
        if not isinstance(d.get("components", []), list):
            _fail(f"disagreements[{i}].components must be a list of uids")
