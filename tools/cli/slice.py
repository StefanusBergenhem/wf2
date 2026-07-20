"""wf slice — mechanical checks over the design-slice (the SA→TL handover).

``slice check`` verifies the slice carries no assumption still marked UNCONFIRMED.
It is the same finding the sprint gate reports as A3, surfaced earlier: the SA runs
it before handing the slice over, so an unratified interpretive reading is caught
while the SA session (and the human) is still there, not after a full decomposition.
Exits non-zero on any error finding.
"""
from __future__ import annotations

import re

import common

_ASSUMPTIONS_HEADER = "Assumptions requiring confirmation"


def section(text, header):
    """The markdown block under `## <header>` up to the next `## ` heading (or EOF)."""
    m = re.search(rf"^##\s+{re.escape(header)}\s*$", text, re.MULTILINE)
    if not m:
        return ""
    rest = text[m.end():]
    nxt = re.search(r"^##\s", rest, re.MULTILINE)
    return rest[: nxt.start()] if nxt else rest


def unconfirmed_assumptions(text):
    """The A3 findings for a slice: one message per UNCONFIRMED assumption line."""
    msgs = []
    for line in section(text, _ASSUMPTIONS_HEADER).splitlines():
        if re.search(r"\bUNCONFIRMED\b", line):
            ident = re.search(r"\b(A-\d+)\b", line)
            who = ident.group(1) if ident else "an assumption"
            msgs.append(f"slice: {who} is UNCONFIRMED — close SA alignment before build")
    return msgs


def _check(rest):
    p = common.base_parser("slice check")
    p.add_argument("--slice", help="path to the design-slice (default: paths.design_slice)")
    args = p.parse_args(rest)

    path = common.resolve_path(args.config, "design_slice", args.slice)
    if not path.exists():
        common.die(f"design-slice not found: {path}")

    errors = [{"code": "A3", "msg": m} for m in unconfirmed_assumptions(path.read_text())]
    result = {
        "slice": str(path),
        "errors": errors,
        "verdict": "fail" if errors else "pass",
    }
    common.emit(result, args.format)
    return 1 if errors else 0


COMMANDS = {
    ("slice", "check"): _check,
}
