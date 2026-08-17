"""wf adequacy check — the form gate on an adequacy digest.

The digest is the only thing a `wf-adequacy` review leaves behind that another program
reads: the drain reads its verdict, and the convergence signal reads how many blocking
paths it found. Both live in header lines the agent writes by hand, so the file is
gated at write time — the agent runs this before it finishes and fixes what it names.

**Every residual carries its class, and only one class gates.** A `breaks` residual is a
path where a user gets a wrong answer today; it is bounded by what is actually wrong, so
a drain can wait on it and still terminate. An `unproven` residual is a path where the
code is right and no scenario pins it; that set is bounded by CODE SIZE, which grows every
time a residual is closed, so a gate waiting on it has no fixed point — dems held one
capability open for 22 reviews on exactly this. So `adequate` means "no breaks", not "no
findings": an adequate digest listing unproven residuals is the normal, correct shape.

Five lines are parsed; the rest is prose for the human. The verdict and the *breaks* count
must agree, because a review claiming a defect it cannot enumerate is the one shape the
convergence rule cannot act on.
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
_BREAKS_RE = re.compile(r"^\*\*Breaks:\*\*\s*(\S+)", re.MULTILINE)
# The enumeration is counted by LINE FORM, never by section heading — digests grow
# their own section names, and a count that depends on one is a count of nothing. The
# parenthesised class is required: the verdict keys on it, and so does where the residual
# is re-homed when it outlives the review, so a bare `RESIDUAL:` leaves both undecided.
CLASSES = ("breaks", "unproven")
_RESIDUAL_LINE_RE = re.compile(r"^\s*-\s.*→\s*RESIDUAL\s*(?:\(([^)]*)\))?:",
                               re.MULTILINE)


def classes(text: str) -> list:
    """The class marker on every residual line, in file order. An unmarked line comes
    back as None so the gate can name it rather than guess a class for it."""
    return [(m.group(1) or "").strip().lower() or None
            for m in _RESIDUAL_LINE_RE.finditer(text)]


def parse(text: str) -> dict:
    """The machine-read fields. A field the file does not carry comes back None."""
    head = _HEAD_RE.search(text)
    question = _QUESTION_RE.search(text)
    residuals = _RESIDUALS_RE.search(text)
    breaks = _BREAKS_RE.search(text)
    found = classes(text)
    return {
        "capability": head.group(1) if head else None,
        "verdict": head.group(2).lower() if head else None,
        "question": question.group(1) if question else None,
        "residuals_header": residuals.group(1) if residuals else None,
        "breaks_header": breaks.group(1) if breaks else None,
        "enumerated": len(found),
        "classes": found,
        "breaks": sum(1 for c in found if c == "breaks"),
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

    verdict, breaks = parsed["verdict"], parsed["breaks"]
    unclassified = sum(1 for c in parsed["classes"] if c is None)
    unknown = sorted({c for c in parsed["classes"] if c and c not in CLASSES})
    if unclassified:
        out.append(f"{unclassified} residual line(s) carry no class — write "
                   f"`→ RESIDUAL(breaks):` when a user gets a wrong answer today, "
                   f"`→ RESIDUAL(unproven):` when the code is right and no scenario "
                   f"pins it. The verdict and the re-homing both key on it")
    if unknown:
        out.append(f"residual class {', '.join(unknown)} is not one of "
                   f"{' | '.join(CLASSES)}")

    # The Breaks header is required only once a breaks residual exists — a digest that
    # found none is complete without it, and demanding it everywhere would put a `0` line
    # on every clean review for no reader.
    if parsed["breaks_header"] is None:
        if breaks:
            out.append(f"{breaks} `→ RESIDUAL(breaks):` line(s) but no `**Breaks:** <n>` "
                       f"line — it is the count the drain and the convergence rule read")
    else:
        try:
            declared = int(parsed["breaks_header"])
        except ValueError:
            out.append(f"`**Breaks:**` is '{parsed['breaks_header']}' — expected a plain "
                       f"integer")
        else:
            if declared != breaks:
                out.append(f"`**Breaks:** {declared}` but {breaks} line(s) match the "
                           f"`→ RESIDUAL(breaks):` form — the header and the "
                           f"enumeration disagree")

    # The verdict tracks BREAKS alone. An adequate digest listing unproven residuals is
    # the normal shape: that class is bounded by code size, so gating on it never ends.
    if verdict == "inadequate" and breaks == 0:
        out.append("verdict is inadequate with no `→ RESIDUAL(breaks):` line — unproven "
                   "residuals are test debt, not an unfulfilled promise; they are "
                   "re-homed as work and the verdict is adequate")
    if verdict == "adequate" and breaks > 0:
        out.append(f"verdict is adequate but {breaks} `→ RESIDUAL(breaks):` line(s) are "
                   f"listed — a path where a user gets a wrong answer today holds the "
                   f"capability open")
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
        "breaks": parsed["breaks"],
        "errors": errors,
    }, args.format)
    return 1 if errors else 0


COMMANDS = {
    ("adequacy", "check"): _check,
}
