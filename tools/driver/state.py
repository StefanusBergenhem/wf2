"""Driver run state — the loop's position, on disk.

The pipeline's own state file tracks one sprint's tasks and is reset at
``complete-sprint``; this file tracks the position that outlives it: which sprint
is open, which branch carries it, which phase the machine is in, and which
increment the loop reached. A restarted driver reads this plus git and resumes;
nothing that matters lives only in memory.
"""
from __future__ import annotations

import datetime
from pathlib import Path

import yaml

PHASES = ("sprint_start", "designing", "increment_loop", "closeout",
          "awaiting_ruling", "stopped")

_FIELDS = ("phase", "sprint_id", "sprint_branch", "increment", "stop_reason",
           "stop_pending")


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class State:
    def __init__(self, path: Path, doc: dict):
        self.path = path
        self.phase = str(doc.get("phase") or "sprint_start")
        self.sprint_id = doc.get("sprint_id")
        self.sprint_branch = doc.get("sprint_branch")
        self.increment = int(doc.get("increment") or 1)
        self.stop_reason = doc.get("stop_reason")
        # A stop signal seen mid-sprint: the loop finishes and ships the sprint it is
        # in, then exits. Carried on disk so a restart does not lose the signal.
        self.stop_pending = doc.get("stop_pending")

    def as_doc(self) -> dict:
        return {"version": 1, **{k: getattr(self, k) for k in _FIELDS}}

    def save(self) -> None:
        """Write the state file. A render identical to what is on disk is skipped —
        a no-op rewrite churns mtimes for nothing (L-106)."""
        doc = self.as_doc()
        existing = _read(self.path)
        if existing and {k: existing.get(k) for k in _FIELDS} == \
                {k: doc[k] for k in _FIELDS}:
            return
        doc["updated_at"] = _now()
        rendered = yaml.safe_dump(doc, sort_keys=False, default_flow_style=False,
                                  allow_unicode=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(rendered)
        tmp.replace(self.path)

    def start_sprint(self, sprint_id: str, branch: str) -> None:
        self.phase = "sprint_start"
        self.sprint_id = sprint_id
        self.sprint_branch = branch
        self.increment = 1
        self.stop_reason = None

    def enter(self, phase: str) -> None:
        if phase not in PHASES:
            raise ValueError(f"unknown driver phase: {phase}")
        self.phase = phase
        self.save()


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        doc = yaml.safe_load(path.read_text())
    except yaml.YAMLError:
        return {}
    return doc if isinstance(doc, dict) else {}


def load(cfg) -> State:
    path = cfg.state_file
    return State(path, _read(path))
