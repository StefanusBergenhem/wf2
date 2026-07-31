"""The runtime bundle and the two control-flow signals.

Everything the phase machine touches the outside world with — the CLI, git, the
agent launcher, telemetry and the state file — arrives in one object, so a phase
function is testable against stand-ins and holds no globals.

``Halt`` ends the run: something is structurally wrong and no further sprint can
run. ``Pause`` ends the run cleanly: a stop rule fired (a ruling is pending, the
work-set is empty, a human asked it to stop).
"""
from __future__ import annotations

from dataclasses import dataclass, field


class Halt(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


class Pause(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass
class Runtime:
    cfg: object
    state: object
    tele: object
    cli: object
    git: object
    agents: object
    dry_run: bool = False
    # task id → the worktree the frontier named for it, so the merge step removes the
    # tree the CLI actually created rather than one it re-derives. In-memory only: a
    # restarted driver falls back to the derived path, and a stale tree is recreated.
    worktrees: dict = field(default_factory=dict)

    def log(self, message: str) -> None:
        print(f"[wf-driver] {message}", flush=True)
