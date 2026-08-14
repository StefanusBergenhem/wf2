"""wf adequacy check — the form gate on an adequacy digest.

The digest is the only thing a `wf-adequacy` review leaves behind that another program
reads: the drain reads its verdict, and the convergence signal reads how many residual
paths it found. Both live in header lines the agent writes by hand, so the file is
gated at write time — the agent runs this before it finishes and fixes what it names.

Four lines are parsed; the rest of the file is prose for the human. The verdict and the
residual count must agree, because a review claiming a gap it cannot enumerate is the
one shape the convergence rule cannot act on.
"""
from __future__ import annotations

import re
from pathlib import Path

import common

QUESTIONS = ("full-promise", "proposed-set")
VERDICTS = ("adequate", "inadequate")

_HEAD_RE = re.compile(r"^#\s*Adequacy:\s*(\S+)\s*[—\-–:]+\s*(\S+)", re.MULTILINE)
_QUESTION_RE = re.compile(r"^\*\*Question:\*\*\s*(\S+)", re.MULTILINE)
_RESIDUALS_RE = re.compile(r"^\*\*Residuals:\*\*\s*(\S+)", re.MULTILINE)
# The enumeration is counted by LINE FORM, never by section heading — digests grow
# their own section names, and a count that depends on one is a count of nothing.
_RESIDUAL_LINE_RE = re.compile(r"^\s*-\s.*→\s*RESIDUAL:", re.MULTILINE)


def parse(text: str) -> dict:
    """The four machine-read fields. A field the file does not carry comes back None."""
    head = _HEAD_RE.search(text)
    question = _QUESTION_RE.search(text)
    residuals = _RESIDUALS_RE.search(text)
    return {
        "capability": head.group(1) if head else None,
        "verdict": head.group(2).lower() if head else None,
        "question": question.group(1) if question else None,
        "residuals_header": residuals.group(1) if residuals else None,
        "enumerated": len(_RESIDUAL_LINE_RE.findall(text)),
    }


def findings(parsed: dict) -> list:
    """Every rule the digest breaks, each naming what to change."""
    out = []
    if parsed["verdict"] is None:
        out.append("no `# Adequacy: <CAP-id> — <verdict>` heading")
    elif parsed["verdict"] not in VERDICTS:
        out.append(f"heading verdict is '{parsed['verdict']}' — expected one of "
                   f"{' | '.join(VERDICTS)}")
    if parsed["question"] is None:
        out.append("no `**Question:**` line")
    elif parsed["question"] not in QUESTIONS:
        out.append(f"`**Question:**` is '{parsed['question']}' — expected the dispatch "
                   f"token verbatim: {' | '.join(QUESTIONS)}")

    header = parsed["residuals_header"]
    count = None
    if header is None:
        out.append("no `**Residuals:** <n>` line — it is the only countable record of "
                   "whether this capability is converging")
    else:
        try:
            count = int(header)
        except ValueError:
            out.append(f"`**Residuals:**` is '{header}' — expected a plain integer")
    if count is not None and count != parsed["enumerated"]:
        out.append(f"`**Residuals:** {count}` but {parsed['enumerated']} line(s) match "
                   f"the `→ RESIDUAL:` form — the header and the enumeration disagree")

    verdict, enumerated = parsed["verdict"], parsed["enumerated"]
    if verdict == "inadequate" and enumerated == 0:
        out.append("verdict is inadequate with no `→ RESIDUAL:` line — a gap that "
                   "cannot be enumerated is a low-confidence adequate, not an "
                   "uncountable inadequate")
    if verdict == "adequate" and enumerated > 0:
        out.append(f"verdict is adequate but {enumerated} `→ RESIDUAL:` line(s) are "
                   f"listed")
    return out


def _check(rest):
    p = common.base_parser("adequacy check")
    p.add_argument("digest", help="path to the digest file to gate")
    args = p.parse_args(rest)

    path = Path(args.digest)
    if not path.is_file():
        common.die(f"error: digest not found: {args.digest}")

    parsed = parse(path.read_text(errors="replace"))
    errors = findings(parsed)
    common.emit({
        "digest": str(path),
        "capability": parsed["capability"],
        "verdict": parsed["verdict"],
        "question": parsed["question"],
        "residuals": parsed["enumerated"],
        "errors": errors,
    }, args.format)
    return 1 if errors else 0


COMMANDS = {
    ("adequacy", "check"): _check,
}
