"""Scripted stand-ins for the driver's three outside edges — the CLI, git, and the
agent launcher — so the phase machine can be tested without an LLM or a repo.

wf2-source-only — never rendered into an install target.
"""
from __future__ import annotations

import types
from pathlib import Path

import cliverbs
import dispatch as driver_dispatch
import runtime as driver_runtime

# What ``support.CONFIG_TMPL`` sets ``paths.telemetry`` to. The sink is COMMITTED and
# every role, Stop hook and driver event appends to it, so a real working tree is
# dirty there almost all of the time — see ``FakeGit``.
TELEMETRY_PATH = ".wf/telemetry/sessions.jsonl"


class FakeCli:
    """Answers `wf` verbs from a script. ``responses[(noun, verb)]`` is either a list
    consumed one call at a time (the last entry repeats), a callable ``fn(cli, args)``
    answering from the calls made so far, or a single value; a value is a dict (rc 0)
    or an (rc, dict) pair."""

    def __init__(self, responses=None):
        self.responses = dict(responses or {})
        self.calls = []
        self.planned = []
        self.dry_run = False

    def _answer(self, args):
        self.calls.append(list(args))
        key = (args[0], args[1]) if len(args) > 1 else (args[0],)
        spec = self.responses.get(key)
        if callable(spec):
            spec = spec(self, list(args))
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
    """Git for the phase machine, with the REAL-WORLD default state: a tree whose only
    dirt is the committed telemetry sink. Every role and Stop hook appends a row to it
    after the last commit, so `is_clean()` is false almost everywhere the driver looks —
    a fake that defaults to pristine hides every predicate that gates on cleanliness.

    ``clean=False`` adds a foreign path on top; ``dirty=[...]`` replaces the set outright
    (``dirty=[]`` is the pristine tree, which a real run essentially never has)."""

    def __init__(self, *, clean=True, stack=None, sprint_id="s1", base="main",
                 dirty=None, fetch_ok=True, telemetry=TELEMETRY_PATH, absent=None,
                 changed=None, rebase_conflicts=()):
        self.telemetry = telemetry
        # {sha: [repo-relative paths changed since it]} — what the drill-cache prune
        # derives staleness from. A sha with no entry is one git does not have.
        self.changed = dict(changed or {})
        # Branches whose rebase onto the sprint tip conflicts, so the salvage falls back.
        self.rebase_conflicts = set(rebase_conflicts)
        self.salvaged = []
        # Branches git does NOT have. Real-world default is empty: a resumed run's sprint
        # branch is there, because a real sprint_start cut it. `absent=["sprint/s1"]`
        # models the one case where it is not — a position recorded but never reached.
        self.absent = set(absent or ())
        if dirty is None:
            dirty = [telemetry] if clean else [telemetry, "src/left-over.go"]
        self.dirty = list(dirty)
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
        self.worktree_add_rc = 0
        self.removed = []
        self.pushed = []
        self.prs = []
        self.commits = []
        self.dry_run = False

    def is_clean(self):
        return not self.dirty

    def dirty_paths(self):
        return list(self.dirty)

    def fetch_base(self):
        self.fetched.append(self.base)
        return (True, "") if self.fetch_ok else (False, "could not reach origin")

    def current_branch(self):
        return self.branches[-1] if self.branches else self.base

    def branch_exists(self, name):
        return name not in self.absent

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
        if self.worktree_add_rc != 0:
            # git leaves nothing behind when the add fails — the dir is not created.
            return types.SimpleNamespace(rc=self.worktree_add_rc)
        Path(path).mkdir(parents=True, exist_ok=True)
        return types.SimpleNamespace(rc=0)

    def worktree_from(self, path, branch, source, onto):
        """The salvage: cut the successor's worktree from a blocked attempt's branch and
        rebase it onto the sprint tip. False when the branch is gone or the rebase
        conflicted — the caller then falls back to a fresh worktree from the tip."""
        if source in self.absent or source in self.rebase_conflicts:
            return False
        self.salvaged.append((str(path), branch, source, onto))
        self.worktrees.append((str(path), branch, source))
        Path(path).mkdir(parents=True, exist_ok=True)
        return True

    def changed_since(self, sha):
        """The paths that changed since ``sha``, or None when git does not have it."""
        return self.changed.get(sha)

    def worktree_remove(self, path, branch=""):
        self.removed.append(str(path))

    def merge(self, branch, message):
        self.merges.append(branch)
        if branch in self.conflict_on:
            # a conflicted merge is LEFT in the tree for the repair role
            self.merging = branch
            self.dirty.append("shared.go")
            return __import__("gitops").MergeResult(ok=False, conflict=True,
                                                    files=["shared.go"])
        self.merged.add(branch)
        return __import__("gitops").MergeResult(ok=True, sha=f"merge-{branch}")

    def merge_abort(self):
        self.aborted.append(self.merging)
        self.merging = None
        self.dirty = [p for p in self.dirty if p != "shared.go"]

    def merge_in_progress(self):
        return self.merging is not None

    def is_merged_into(self, branch, base):
        return branch in self.merged

    def resolve_merge(self):
        """What a successful `wf-stage-repair` merge run leaves: the merge committed and
        the conflict gone — but the telemetry sink dirty, because the repair role's own
        last action is to append its session row to it."""
        self.merged.add(self.merging)
        self.merging = None
        self.dirty = [p for p in self.dirty if p != "shared.go"]

    def commit_paths(self, paths, message):
        self.commits.append((message, [str(p) for p in paths], self.current_branch()))
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
        self.logs = {}
        self.dry_run = False
        self.planned = []

    def on(self, role, fn):
        self.effects.setdefault(role, []).append(fn)

    def refuse(self, role, log="You've hit your session limit · resets 3:20pm", rc=1):
        """A harness that never ran the role: a non-zero exit, no artifacts, and its
        reason printed to the dispatch log in place of the work."""
        self.exit_codes[role] = rc
        self.logs[role] = log

    def launch(self, role, params, *, cwd=None, task_id=None, stage=None,
               mode=None):
        self.launches.append({"role": role, "params": params, "task_id": task_id,
                              "mode": mode, "stage": stage,
                              "cwd": str(cwd) if cwd else None})
        queue = self.effects.get(role)
        if queue:
            fn = queue.pop(0) if len(queue) > 1 else queue[0]
            fn(self, role, params, task_id)
        rc = self.exit_codes.get(role, 0)
        log_path = Path("/dev/null")
        if role in self.logs:
            log_path = self.cfg.path("transient") / f"{role}-fake.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(self.logs[role])
        return driver_dispatch.Launched(role, rc, 0, False, log_path, "fake", params)

    def roles(self):
        return [x["role"] for x in self.launches]


def resolve_issues(cfg, fix_kind="contract_amendment", task=None):
    """The side effect a real design cut leaves behind: every open entry in the host
    design-issues file comes back resolved, carrying the kind of fix it made and — the
    mechanical half of the drain — the successor task that answers it."""
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
                if task:
                    item["task"] = task
        path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return effect


def raise_design_issue(cfg, di_id="DI-9", task_id=None, summary="cannot be built",
                       **fields):
    """The side effect a role that rejects its input leaves: an open entry in the host
    design-issues file. ``fields`` carries the rest of the entry."""
    import yaml

    def effect(agents, role, params, task_id_arg):
        write_design_issue(cfg, di_id=di_id, task_id=task_id, summary=summary, **fields)
    return effect


def write_design_issue(cfg, *, di_id="DI-9", task_id=None, summary="cannot be built",
                       status="open", **fields) -> None:
    """Append one entry to the host design-issues file — what the driver's own
    ``issues.record`` leaves when a task blocks, and what the design role resolves."""
    import yaml

    path = cfg.path("design_issues")
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = (yaml.safe_load(path.read_text()) if path.exists() else {}) or {}
    doc.setdefault("issues", []).append(
        {"id": di_id, "task_id": task_id, "severity": "high", "status": status,
         "summary": summary, **fields})
    path.write_text(yaml.safe_dump(doc, sort_keys=False))


def runtime(cfg, cli=None, git=None, agents=None, state=None, telemetry=None,
            report=None):
    """The runtime under test. Its reporter writes nowhere unless a test asks for one:
    the driver's own default is a live reporter (going dark is the failure it exists to
    prevent), which would otherwise spray the suite's output."""
    import events
    import progress
    import state as driver_state
    return driver_runtime.Runtime(
        cfg=cfg,
        state=state or driver_state.load(cfg),
        tele=telemetry or events.Telemetry(cfg, dry_run=True),
        cli=cli or FakeCli(),
        git=git or FakeGit(),
        agents=agents or FakeAgents(cfg),
        report=report or progress.silent(),
    )
