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
    def __init__(self, *, clean=True, stack=None, sprint_id="s1", base="main",
                 dirty=None, fetch_ok=True):
        self.clean = clean and not dirty
        self.dirty = list(dirty or ([] if clean else ["src/left-over.go"]))
        self._stack = list(stack or [])
        self._sprint_id = sprint_id
        self.base = base
        self.fetch_ok = fetch_ok
        self.fetched = []
        self.branches = []
        self.merges = []
        self.merged = set()
        self.merging = None
        self.aborted = []
        self.conflict_on = set()
        self.worktrees = []
        self.removed = []
        self.pushed = []
        self.prs = []
        self.commits = []
        self.dry_run = False

    def is_clean(self):
        return self.clean and not self.dirty

    def dirty_paths(self):
        return list(self.dirty)

    def fetch_base(self):
        self.fetched.append(self.base)
        return (True, "") if self.fetch_ok else (False, "could not reach origin")

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
            # a conflicted merge is LEFT in the tree for the repair role
            self.merging = branch
            self.clean = False
            return __import__("gitops").MergeResult(ok=False, conflict=True,
                                                    files=["shared.go"])
        self.merged.add(branch)
        return __import__("gitops").MergeResult(ok=True, sha=f"merge-{branch}")

    def merge_abort(self):
        self.aborted.append(self.merging)
        self.merging = None
        self.clean = True

    def merge_in_progress(self):
        return self.merging is not None

    def is_merged_into(self, branch, base):
        return branch in self.merged

    def resolve_merge(self):
        """What a successful `wf-stage-repair` merge run leaves: the merge committed,
        the tree clean."""
        self.merged.add(self.merging)
        self.merging = None
        self.clean = True

    def commit_paths(self, paths, message):
        self.commits.append((message, [str(p) for p in paths]))
        self.dirty = [p for p in self.dirty if p not in {str(x) for x in paths}]
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
