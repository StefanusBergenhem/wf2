"""Scripted stand-ins for the driver's three outside edges — the CLI, git, and the
agent launcher — so the phase machine can be tested without an LLM or a repo.

wf2-source-only — never rendered into an install target.
"""
from __future__ import annotations

from pathlib import Path

import cliverbs
import dispatch as driver_dispatch
import runtime as driver_runtime


class FakeCli:
    """Answers `wf` verbs from a script. ``responses[(noun, verb)]`` is either a list
    consumed one call at a time (the last entry repeats) or a single value; a value is
    a dict (rc 0) or an (rc, dict) pair."""

    def __init__(self, responses=None):
        self.responses = dict(responses or {})
        self.calls = []
        self.planned = []
        self.dry_run = False

    def _answer(self, args):
        self.calls.append(list(args))
        key = (args[0], args[1]) if len(args) > 1 else (args[0],)
        spec = self.responses.get(key)
        if isinstance(spec, list):
            spec = spec[0] if len(spec) == 1 else spec.pop(0)
        if spec is None:
            return cliverbs.Result(argv=list(args), rc=0, data={})
        if isinstance(spec, tuple):
            rc, data = spec
        else:
            rc, data = 0, spec
        return cliverbs.Result(argv=list(args), rc=rc, data=dict(data))

    def read(self, *args, timeout=None):
        return self._answer(args)

    def mutate(self, *args, timeout=None):
        return self._answer(args)

    def raw(self, *args, timeout=None, mutating=False):
        return self._answer(args)

    def verbs(self):
        return [" ".join(str(a) for a in c[:2]) for c in self.calls]


class FakeGit:
    def __init__(self, *, clean=True, stack=None, sprint_id="s1", base="main"):
        self.clean = clean
        self._stack = list(stack or [])
        self._sprint_id = sprint_id
        self.base = base
        self.branches = []
        self.merges = []
        self.conflict_on = set()
        self.worktrees = []
        self.removed = []
        self.pushed = []
        self.prs = []
        self.commits = []
        self.dry_run = False

    def is_clean(self):
        return self.clean

    def current_branch(self):
        return self.branches[-1] if self.branches else self.base

    def next_sprint_id(self):
        return self._sprint_id

    def stack(self):
        return list(self._stack)

    def stack_tip(self):
        return self._stack[-1] if self._stack else self.base

    def pr_base(self, branch):
        below = [b for b in self._stack if b != branch]
        return below[-1] if below else self.base

    def start_branch(self, name, base):
        self.branches.append(name)

    def checkout(self, name):
        self.branches.append(name)

    def head_sha(self, ref="HEAD", cwd=None):
        return "abc1234"

    def worktree_add(self, path, branch, base):
        self.worktrees.append((str(path), branch, base))
        Path(path).mkdir(parents=True, exist_ok=True)

    def worktree_remove(self, path, branch=""):
        self.removed.append(str(path))

    def merge(self, branch, message):
        self.merges.append(branch)
        if branch in self.conflict_on:
            return __import__("gitops").MergeResult(ok=False, conflict=True,
                                                    files=["shared.go"])
        return __import__("gitops").MergeResult(ok=True, sha=f"merge-{branch}")

    def commit_paths(self, paths, message):
        self.commits.append(message)
        return "commit1"

    def push(self, branch):
        self.pushed.append(branch)

    def pr_create(self, base, head, title, body_file):
        self.prs.append({"base": base, "head": head, "title": title,
                         "body": Path(body_file).read_text()})
        return 0, "https://example.test/pr/1"


class FakeAgents:
    """Records every dispatch; ``on(role)`` registers a side effect that writes the
    artifacts a real role would leave behind."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.launches = []
        self.effects = {}
        self.exit_codes = {}
        self.dry_run = False
        self.planned = []

    def on(self, role, fn):
        self.effects.setdefault(role, []).append(fn)

    def launch(self, role, params, *, cwd=None, task_id=None, increment=None,
               mode=None):
        self.launches.append({"role": role, "params": params, "task_id": task_id,
                              "mode": mode, "increment": increment,
                              "cwd": str(cwd) if cwd else None})
        queue = self.effects.get(role)
        if queue:
            fn = queue.pop(0) if len(queue) > 1 else queue[0]
            fn(self, role, params, task_id)
        rc = self.exit_codes.get(role, 0)
        return driver_dispatch.Launched(role, rc, 0, False, Path("/dev/null"), "fake",
                                        params)

    def roles(self):
        return [x["role"] for x in self.launches]


def resolve_issues(cfg, fix_kind="contract_amendment"):
    """The side effect a real design-role repair leaves: every open entry in the host
    design-issues file comes back resolved, carrying the kind of fix it made."""
    import yaml

    def effect(agents, role, params, task_id):
        path = cfg.path("design_issues")
        if not path.exists():
            return
        doc = yaml.safe_load(path.read_text()) or {}
        for item in doc.get("issues") or []:
            if isinstance(item, dict) and str(item.get("status") or "open") == "open":
                item["status"] = "resolved"
                item["fix_kind"] = fix_kind
        path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return effect


def raise_design_issue(cfg, di_id="DI-9", task_id=None, summary="cannot be built",
                       **fields):
    """The side effect a role that rejects its input leaves: an open entry in the host
    design-issues file. ``fields`` carries the rest of the entry (e.g. the ``increment``
    a Tech Lead's slice rejection names)."""
    import yaml

    def effect(agents, role, params, task_id_arg):
        path = cfg.path("design_issues")
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = yaml.safe_load(path.read_text()) if path.exists() else {}
        doc = doc or {}
        doc.setdefault("issues", []).append(
            {"id": di_id, "task_id": task_id, "severity": "high", "status": "open",
             "summary": summary, **fields})
        path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return effect


def runtime(cfg, cli=None, git=None, agents=None, state=None, telemetry=None):
    import events
    import state as driver_state
    return driver_runtime.Runtime(
        cfg=cfg,
        state=state or driver_state.load(cfg),
        tele=telemetry or events.Telemetry(cfg, dry_run=True),
        cli=cli or FakeCli(),
        git=git or FakeGit(),
        agents=agents or FakeAgents(cfg),
    )
