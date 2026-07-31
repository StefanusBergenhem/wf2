"""The `wf` CLI, as the driver calls it.

Every verdict the loop routes on comes from here: the CLI reads the artifacts on
disk and answers in JSON, and the driver switches on that answer plus the exit
code. Mutations are serialized behind one lock — the pipeline state file is a
read-modify-write document and the loop calls these verbs from parallel task
threads.
"""
from __future__ import annotations

import json
import sys
import threading
from dataclasses import dataclass, field

import procs


@dataclass
class Result:
    argv: list
    rc: int
    data: dict = field(default_factory=dict)
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.rc == 0


class Cli:
    def __init__(self, cfg, dry_run: bool = False, timeout: int = procs.CLI_TIMEOUT_S):
        self.cfg = cfg
        self.dry_run = dry_run
        self.timeout = timeout
        self.lock = threading.Lock()
        self.planned: list = []

    def read(self, *args, timeout=None) -> Result:
        """A query verb — always runs, even in dry-run: reads have no side effects."""
        return self._run(list(args), timeout=timeout)

    def mutate(self, *args, timeout=None) -> Result:
        """A state-changing verb. Serialized, and skipped (printed) in dry-run."""
        argv = list(args)
        if self._skipped(argv):
            return Result(argv=argv, rc=0)
        with self.lock:
            return self._run(argv, timeout=timeout)

    def raw(self, *args, timeout=None, mutating: bool = False) -> Result:
        """A verb with its own argument shape — the orchestrate helpers take positional
        worktree paths and print their own JSON, so nothing is appended to them."""
        argv = list(args)
        if mutating and self._skipped(argv):
            return Result(argv=argv, rc=0)
        return self._run(argv, timeout=timeout, decorate=False)

    def _skipped(self, argv) -> bool:
        if not self.dry_run:
            return False
        self.planned.append(argv)
        print(f"[dry-run] wf {' '.join(str(a) for a in argv)}")
        return True

    def _run(self, args, timeout=None, decorate=True) -> Result:
        argv = [sys.executable, str(self.cfg.cli), *[str(a) for a in args]]
        if decorate:
            argv += ["--config", self.cfg.config_path, "--format", "json"]
        done = procs.run(argv, timeout=timeout or self.timeout, cwd=self.cfg.root)
        data = {}
        if done.stdout.strip():
            try:
                parsed = json.loads(done.stdout.strip().splitlines()[-1])
                data = parsed if isinstance(parsed, dict) else {"value": parsed}
            except (ValueError, IndexError):
                data = {}
        return Result(argv=argv, rc=done.rc, data=data, stderr=done.stderr)
