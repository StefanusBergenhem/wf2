"""Git for the loop — branching the stack, per-task worktrees, batch merges, ship.

The driver owns exactly these git operations: it cuts the sprint branch, creates
and removes worktrees, merges approved task branches at a sub-layer boundary, and
publishes the sprint. Roles commit their own artifacts inside their own trees; the
driver never rewrites a shipped branch.

Stack depth is derived here, never stored: a shipped sprint branch that is not yet
an ancestor of the base branch is still on the stack.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import procs

SPRINT_PREFIX = "sprint/"
_SPRINT_ORDINAL_RE = re.compile(r"^sprint/s(\d+)$")


@dataclass
class MergeResult:
    ok: bool
    sha: str = ""
    conflict: bool = False
    files: list = field(default_factory=list)
    detail: str = ""


class Git:
    def __init__(self, cfg, dry_run: bool = False):
        self.cfg = cfg
        self.root = cfg.root
        self.dry_run = dry_run

    # ── plumbing ─────────────────────────────────────────────────────────────

    def _run(self, *args, cwd=None, timeout=procs.GIT_TIMEOUT_S):
        return procs.run(["git", "-C", str(cwd or self.root), *args], timeout=timeout)

    def _out(self, *args, cwd=None) -> str:
        done = self._run(*args, cwd=cwd)
        return done.stdout.strip() if done.rc == 0 else ""

    def _write(self, *args, cwd=None, timeout=procs.GIT_WRITE_TIMEOUT_S):
        """A mutating git call. Printed, not run, in dry-run."""
        if self.dry_run:
            print(f"[dry-run] git {' '.join(str(a) for a in args)}")
            return procs.Completed(argv=list(args), rc=0, stdout="", stderr="",
                                   duration_s=0)
        return self._run(*args, cwd=cwd, timeout=timeout)

    # ── branch state ─────────────────────────────────────────────────────────

    def current_branch(self) -> str:
        return self._out("branch", "--show-current")

    def is_clean(self) -> bool:
        done = self._run("status", "--porcelain")
        return done.rc == 0 and not done.stdout.strip()

    def head_sha(self, ref="HEAD", cwd=None) -> str:
        return self._out("rev-parse", "--short", ref, cwd=cwd)

    def branch_exists(self, name: str) -> bool:
        return self._run("rev-parse", "--verify", "--quiet",
                         f"refs/heads/{name}").rc == 0

    def sprint_branches(self) -> list:
        """Every sprint branch in the repo, in ordinal order."""
        out = self._out("for-each-ref", "--format=%(refname:short)",
                        f"refs/heads/{SPRINT_PREFIX}")
        names = [n for n in out.splitlines() if n.strip()]
        return sorted(names, key=_ordinal_key)

    def next_sprint_id(self) -> str:
        """The next sprint's id, derived from the branches that exist — no counter."""
        highest = 0
        for name in self.sprint_branches():
            m = _SPRINT_ORDINAL_RE.match(name)
            if m:
                highest = max(highest, int(m.group(1)))
        return f"s{highest + 1}"

    def is_merged_into(self, branch: str, base: str) -> bool:
        return self._run("merge-base", "--is-ancestor", branch, base).rc == 0

    def stack(self) -> list:
        """The shipped sprint branches not yet merged into the base branch, bottom
        first — the stack whose depth bounds the loop."""
        base = self.cfg.base_branch
        return [b for b in self.sprint_branches() if not self.is_merged_into(b, base)]

    def stack_tip(self) -> str:
        """What the next sprint branches from: the top of the stack, else the base."""
        stack = self.stack()
        return stack[-1] if stack else self.cfg.base_branch

    def pr_base(self, branch: str) -> str:
        """A sprint PR targets the unmerged sprint branch below it; once that merges,
        GitHub retargets to the base branch on its own."""
        below = [b for b in self.stack() if _ordinal_key(b) < _ordinal_key(branch)]
        return below[-1] if below else self.cfg.base_branch

    def start_branch(self, name: str, base: str):
        """Cut the branch (or check it out again on resume) and stand on it."""
        if self.branch_exists(name):
            return self._write("checkout", name)
        return self._write("checkout", "-b", name, base)

    def checkout(self, name: str):
        return self._write("checkout", name)

    # ── worktrees ────────────────────────────────────────────────────────────

    def worktree_add(self, path: Path, branch: str, base: str):
        """A task worktree cut from the CURRENT sprint-branch tip, so the task sees
        every earlier sub-layer's merged work. A leftover worktree whose base has moved
        on is destroyed and recreated — a re-dispatched build restarts from zero, so
        nothing in it is worth keeping."""
        path = Path(path)
        if path.exists():
            if self._run("merge-base", "--is-ancestor", base, "HEAD", cwd=path).rc == 0:
                return None
            self.worktree_remove(path, branch)
        if self.dry_run:
            return self._write("worktree", "add", str(path), "-b", branch, base)
        path.parent.mkdir(parents=True, exist_ok=True)
        done = self._write("worktree", "add", str(path), "-b", branch, base)
        if done.rc != 0 and self.branch_exists(branch):
            # the branch survived an interrupted run — reuse it rather than colliding
            self._write("branch", "-D", branch)
            done = self._write("worktree", "add", str(path), "-b", branch, base)
        return done

    def worktree_remove(self, path: Path, branch: str = ""):
        done = self._write("worktree", "remove", "--force", str(path))
        if branch:
            self._write("branch", "-D", branch)
        return done

    # ── merges ───────────────────────────────────────────────────────────────

    def merge(self, branch: str, message: str) -> MergeResult:
        """Merge one approved task branch into the branch checked out here. On a
        conflict the merge is aborted and the conflicting paths are reported: the
        driver records a design issue and routes the repair, never auto-resolves."""
        done = self._write("merge", "--no-ff", branch, "-m", message)
        if done.rc == 0:
            return MergeResult(ok=True, sha=self.head_sha())
        conflicted = [line.strip() for line in
                      self._out("diff", "--name-only", "--diff-filter=U").splitlines()
                      if line.strip()]
        self._write("merge", "--abort")
        return MergeResult(ok=False, conflict=bool(conflicted), files=conflicted,
                           detail=(done.stderr or done.stdout).strip()[:2000])

    # ── ship ─────────────────────────────────────────────────────────────────

    def commit_paths(self, paths, message: str):
        """Stage the named paths and commit them. Returns the commit sha, or None
        when nothing was staged (an empty close leaves no empty commit behind)."""
        existing = [str(p) for p in paths if p and (self.root / str(p)).exists()]
        if not existing:
            return None
        self._write("add", "--", *existing)
        if self.dry_run:
            return "dry-run"
        if self._run("diff", "--cached", "--quiet").rc == 0:
            return None
        done = self._write("commit", "-m", message)
        return self.head_sha() if done.rc == 0 else None

    def push(self, branch: str):
        return self._write("push", "-u", "origin", branch,
                           timeout=procs.NETWORK_TIMEOUT_S)

    def pr_create(self, base: str, head: str, title: str, body_file: Path):
        """Open the sprint's PR against its stack base. Returns (rc, url-or-error)."""
        argv = ["gh", "pr", "create", "--base", base, "--head", head,
                "--title", title, "--body-file", str(body_file)]
        if self.dry_run:
            print(f"[dry-run] {' '.join(argv)}")
            return 0, ""
        done = procs.run(argv, timeout=procs.NETWORK_TIMEOUT_S, cwd=self.root)
        return done.rc, (done.stdout or done.stderr).strip()


def _ordinal_key(name: str):
    m = _SPRINT_ORDINAL_RE.match(name)
    return (0, int(m.group(1)), "") if m else (1, 0, name)
