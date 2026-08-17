"""The stop rules — the four conditions that end or pause a continuous run.

1. **Escalation** — the design role wrote ``paths.decision_prep``: pause for a human
   ruling.
2. **Work exhaustion** — no open, unparked capability or learning is left.
3. **Manual stop** — ``driver.stop_file`` exists: finish the sprint in flight, ship
   it, exit.
4. **Stack depth** — the shipped-but-unmerged sprint branches reached
   ``driver.max_unmerged_sprints``: pause until PRs merge.

A request-changes review on any stacked PR acts as (3); it is read at stage closes
with the config-keyed ``driver.review_state_cmd``.

Every rule is a mechanical read of a file, of git, or of a command's exit — never a
judgement, and never an agent's prose.
"""
from __future__ import annotations

from dataclasses import dataclass

import procs
import yaml

_CHANGES_REQUESTED = "CHANGES_REQUESTED"


@dataclass
class Stop:
    reason: str
    detail: str = ""


def _entries(path, field):
    if not path or not path.exists():
        return []
    try:
        doc = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError:
        return []
    items = doc.get(field) if isinstance(doc, dict) else None
    return [e for e in (items or []) if isinstance(e, dict)]


# Intent the loop cannot act on without a human first. `parked` is waiting on a PO session
# to re-word a promise nobody could prove; `proposed` was minted by the residue exit from a
# defect residual and carries machine-written words, which a PO session has to own before
# anything is designed against them. With only these left, work exhaustion is the right
# stop: it says the loop is out of work a human has agreed to, which is true.
NEEDS_A_HUMAN = ("parked", "proposed")


def open_work(cfg) -> dict:
    """The ids still in play: every capability and learning entry the loop could act on."""
    def actionable(path, field):
        return [str(e["id"]) for e in _entries(path, field)
                if e.get("id")
                and str(e.get("status") or "").strip() not in NEEDS_A_HUMAN]

    return {
        "capabilities": actionable(cfg.path_opt("capabilities"), "capabilities"),
        "learnings": actionable(cfg.path_opt("learnings"), "learnings"),
    }


def review_state(cfg, git):
    """The first stacked sprint branch whose PR review requested changes, or None.
    An unset ``driver.review_state_cmd`` skips the check entirely."""
    template = str(cfg.driver_opt("review_state_cmd", "") or "").strip()
    if not template:
        return None
    timeout = int(cfg.driver_opt("command_timeout_s", 300))
    for branch in git.stack():
        done = procs.run(template.replace("{branch}", branch), timeout=timeout,
                         cwd=cfg.root, shell=True)
        if _CHANGES_REQUESTED in (done.stdout or "").upper():
            return branch
    return None


def pre_sprint(cfg, git):
    """Checked before a new sprint is started. Returns the first rule that fires."""
    decision_prep = cfg.path_opt("decision_prep")
    if decision_prep and decision_prep.exists():
        return Stop("escalation", f"a ruling is pending in {decision_prep}")
    if cfg.stop_file.exists():
        return Stop("manual_stop", f"{cfg.stop_file} exists")
    work = open_work(cfg)
    if not work["capabilities"] and not work["learnings"]:
        return Stop("work_exhaustion",
                    "no open, unparked capability or learning remains")
    branch = review_state(cfg, git)
    if branch:
        return Stop("review_changes_requested", f"{branch} has changes requested")
    depth = len(git.stack())
    cap = int(cfg.driver("max_unmerged_sprints"))
    if depth >= cap:
        return Stop("stack_depth",
                    f"{depth} shipped sprint branches are unmerged (cap {cap})")
    return None


def at_boundary(cfg, git):
    """Checked at every stage close. Only the two signals that let the sprint in flight
    finish first: a manual stop, and a request-changes review."""
    if cfg.stop_file.exists():
        return Stop("manual_stop", f"{cfg.stop_file} exists")
    branch = review_state(cfg, git)
    if branch:
        return Stop("review_changes_requested", f"{branch} has changes requested")
    return None
