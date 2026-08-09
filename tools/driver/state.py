"""Driver run state — the loop's position, on disk.

The pipeline's own state file tracks one stage's tasks and is reset at
``complete-sprint``; this file tracks the position that outlives it: which sprint
is open, which branch carries it, which phase the machine is in, which stage the
last cut produced, and how many stages the PR in flight already carries. A
restarted driver reads this plus git and resumes; nothing that matters lives only
in memory.
"""
from __future__ import annotations

import datetime
from pathlib import Path

import yaml

PHASES = ("sprint_start", "designing", "stage_run", "closeout",
          "awaiting_ruling", "stopped")

_FIELDS = ("phase", "sprint_id", "sprint_branch", "stage", "stages_shipped",
           "stop_reason", "stop_pending", "resume_phase", "closeout_done")


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class State:
    def __init__(self, path: Path, doc: dict, dry_run: bool = False):
        self.path = path
        # A dry run must leave no state behind: persisted position would make the next
        # real run resume into a phase whose sprint branch was never actually cut.
        self.dry_run = dry_run
        self.phase = str(doc.get("phase") or "sprint_start")
        self.sprint_id = doc.get("sprint_id")
        self.sprint_branch = doc.get("sprint_branch")
        # The stage the last cut produced — the repo-lifetime-monotonic id that keys the
        # task ids, the stage-check log and the stage-timing state.
        self.stage = doc.get("stage")
        # How many stages have merged into the PR in flight. The PR ships when a stage
        # lands an end-to-end checkpoint or when this reaches
        # ``driver.max_stages_per_sprint`` — there is no increment list to exhaust.
        self.stages_shipped = int(doc.get("stages_shipped") or 0)
        self.stop_reason = doc.get("stop_reason")
        # A stop signal seen mid-sprint: the loop finishes and ships the sprint it is
        # in, then exits. Carried on disk so a restart does not lose the signal.
        self.stop_pending = doc.get("stop_pending")
        # The phase a pending ruling suspended, so the resume returns to it rather than
        # re-running the phase machine from the top.
        self.resume_phase = doc.get("resume_phase")
        # The closeout steps this sprint already ran — a restart inside closeout must
        # not re-dispatch them (a second adequacy review would shift the park count).
        self.closeout_done = list(doc.get("closeout_done") or [])

    def as_doc(self) -> dict:
        return {"version": 1, **{k: getattr(self, k) for k in _FIELDS}}

    def save(self) -> None:
        """Write the state file. A render identical to what is on disk is skipped —
        a no-op rewrite churns mtimes for nothing (L-106)."""
        if self.dry_run:
            return
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
        self.stage = None
        self.stages_shipped = 0
        self.stop_reason = None
        self.resume_phase = None
        self.closeout_done = []

    def step_done(self, step: str) -> None:
        """Bank one finished closeout step, on disk, before the next one starts."""
        if step not in self.closeout_done:
            self.closeout_done.append(step)
            self.save()

    def suspend(self, phase: str) -> None:
        """Park the run for a human ruling, remembering the phase to come back to."""
        self.resume_phase = self.phase
        self.enter(phase)

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


def load(cfg, dry_run: bool = False) -> State:
    path = cfg.state_file
    return State(path, _read(path), dry_run=dry_run)
