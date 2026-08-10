"""Reads over the transient run state and the stage artifact.

The two files a running sprint keeps on disk — ``paths.pipeline_state`` (live task
state) and ``paths.stage`` (the cut being built) — are read by the pipeline brain and
by the close-time drain alike. The readers live here so neither of those modules has
to import the other: they used to, in both directions, and the cycle forced every
shared helper to be reached through a private name across the seam.

Reads only. A mutation belongs to whichever module owns that transition.
"""
from __future__ import annotations

from pathlib import Path

import common

# Effective task status → bucket. Live state (pipeline_state.task_states[id].status)
# wins; otherwise the stage-authored status; otherwise "pending".
COMPLETED = {"completed", "done"}          # done = the stage's authored terminal-OK
OCCUPIES_SLOT = {"building", "reviewing", "dispatching"}  # active; counts against the cap
PARKED = {"design_issue"}                  # hit a design issue; re-enters at the next cut
ESCALATED = {"escalated"}                  # terminal failure for this task
BLOCKED = {"blocked"}                      # terminal failure for this task
APPROVED = {"approved"}                    # passed every review pass; awaiting batch merge


def effective_status(task_id, authored_status, task_states):
    """Live state wins; fall back to the stage-authored status; default pending."""
    live = (task_states.get(task_id) or {}).get("status")
    return live or authored_status or "pending"


def covers_of(entry):
    """A ``covers`` field may be a scalar or a list; normalise to a list of strings."""
    cov = (entry or {}).get("covers")
    if cov is None:
        return []
    return [str(c) for c in (cov if isinstance(cov, list) else [cov])]


# ── The stage artifact ───────────────────────────────────────────────────────


def stage_doc(args, paths=None):
    """The stage artifact, or {}. It is archived and deleted the moment its stage merges,
    so every close-time read has to tolerate its absence."""
    paths = paths if paths is not None else (common.config_doc(args.config).get("paths") or {})
    if not paths.get("stage"):
        return {}
    return common.load_yaml(common.resolve_path(args.config, "stage", None), optional=True)


def stage_tasks(doc):
    return [t for t in (doc.get("tasks") or []) if isinstance(t, dict) and t.get("id")]


def serves_of(doc):
    raw = doc.get("serves") or []
    return [str(s).strip() for s in (raw if isinstance(raw, list) else [raw]) if str(s).strip()]


# ── Run-state reads ──────────────────────────────────────────────────────────


def state_path(args) -> Path:
    return common.resolve_path(args.config, "pipeline_state", None)


def load_state(args) -> dict:
    """Read pipeline_state.yaml — empty doc if it does not exist yet (a fresh sprint
    has no state file; the first mutation creates it)."""
    return common.load_yaml(state_path(args), optional=True)


def save_state(args, doc) -> None:
    common.write_yaml(state_path(args), doc)
