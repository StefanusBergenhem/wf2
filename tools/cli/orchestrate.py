"""wf orchestrate — the orchestrator's mechanical helpers.

The judgment-free verdict scripts: the controller (the skill OR the Python driver)
shells out to these between sub-agent dispatches, then switches on their EXACT
stdout and exit codes. Verdicts come from on-disk artifacts, never from parsing a
sub-agent's prose — compliance by mechanism.

  inspect-build-return   Build Return Protocol detector
  inspect-review-return  Review Return Protocol detector
  preserve-uncommitted   commit uncommitted work before re-dispatch
  sweep-transients       session-start broom (resume safety)
  dispatch-fix           design-issue routing by fix_kind

Unlike the query verbs (which route through common.emit), every verb here prints
the raw shape its contract names — a compact JSON line, an indent=2 blob, or a bare
word — and returns the documented exit code.

Worktree discipline: the inspect/preserve verbs read the WORKTREE's OWN
``.wf/config.yaml`` (a positional ``<worktree-path>``), resolving artifact paths
against the worktree root — that is where the build/review agent wrote them. The
sweep/dispatch verbs take a cwd-relative ``--config`` (default ``.wf/config.yaml``).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - environment guard (common.py already guards)
    sys.stderr.write("wf: PyYAML is required (pip install pyyaml)\n")
    sys.exit(2)


def _err(msg: str, code: int = 2) -> int:
    sys.stderr.write(msg + "\n")
    return code


class _MalformedYAML(Exception):
    pass


def _load_yaml_file(path: Path) -> dict:
    """Parse a YAML file to a dict; {} if missing. Raises _MalformedYAML on a parse
    error so the caller can map it to exit 2."""
    if not path.is_file():
        return {}
    try:
        return yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise _MalformedYAML(str(exc)) from exc


def _config_path_value(config_doc: dict, key: str) -> str:
    """Read paths.<key> from an already-loaded config dict, '' if absent."""
    v = (config_doc.get("paths") or {}).get(key)
    return "" if v is None else str(v)


def _open_design_issue_for_task(path: Path, task_id: str) -> str:
    """Return the id of the first OPEN design issue whose task_id matches, else ''.
    A design issue is a build/review result artifact: its presence (an open entry for
    this task) is the routing signal — the inspectors surface it as a `design_issue`
    verdict carrying this id. A malformed file is treated as no signal (cascade falls
    through), not an error."""
    if not path.is_file():
        return ""
    try:
        doc = _load_yaml_file(path)
    except _MalformedYAML:
        return ""
    for issue in (doc.get("issues") or []):
        if not isinstance(issue, dict):
            continue
        if issue.get("task_id") != task_id:
            continue
        if (issue.get("status") or "open") == "open":
            iid = issue.get("id")
            if iid:
                return str(iid)
    return ""


# ===========================================================================
# 1. inspect-build-return
# ===========================================================================

def _inspect_build_return(rest):
    if len(rest) < 2 or not rest[0] or not rest[1]:
        return _err("usage: inspect-build-return <worktree-path> <task-id>")
    worktree_path = Path(rest[0])
    task_id = rest[1]

    if not worktree_path.is_dir():
        return _err(f"error: worktree path not found: {rest[0]}")

    config_file = worktree_path / ".wf" / "config.yaml"
    try:
        config_doc = _load_yaml_file(config_file)
    except _MalformedYAML as exc:
        return _err(f"error: parse {config_file}: {exc}")

    p_design_issues = _config_path_value(config_doc, "design_issues") or \
        ".wf/transient/design-issues.yaml"
    p_review_ready = _config_path_value(config_doc, "review_ready") or \
        ".wf/transient/review-ready.yaml"

    abs_design_issues = worktree_path / p_design_issues
    abs_review_ready = worktree_path / p_review_ready

    # Build writes exactly one result artifact; route on presence, in priority order.
    # A design issue parks the task (routes to a fix agent) — it never goes on to review.
    verdict = ""
    artifact_rel = ""
    di_id = ""

    di = _open_design_issue_for_task(abs_design_issues, task_id)
    if di:
        verdict = "design_issue"
        artifact_rel = p_design_issues
        di_id = di
    elif abs_review_ready.is_file():
        verdict = "ready_for_review"
        artifact_rel = p_review_ready
    else:
        verdict = "escalate_no_artifacts"

    out = {
        "task_id": task_id,
        "verdict": verdict,
        "artifact": artifact_rel if artifact_rel else None,
        "di_id": di_id if di_id else None,
    }
    print(json.dumps(out, separators=(",", ":"), sort_keys=False))
    return 0


# ===========================================================================
# 2. inspect-review-return
# ===========================================================================

def _is_git_worktree(worktree_path: Path) -> bool:
    git_marker = worktree_path / ".git"
    return git_marker.is_dir() or git_marker.is_file()


def _git_out(worktree_path: Path, *args: str):
    """Run a git command in the worktree. Returns (ok, stripped_stdout)."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(worktree_path), *args],
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False, ""
    return proc.returncode == 0, proc.stdout.strip()


def _inspect_review_return(rest):
    if len(rest) < 3 or not rest[0] or not rest[1] or not rest[2]:
        return _err("usage: inspect-review-return <worktree-path> <task-id> <build-commit-sha>")
    worktree_path = Path(rest[0])
    task_id = rest[1]
    build_commit_sha = rest[2]

    if not worktree_path.is_dir():
        return _err(f"error: worktree path not found: {rest[0]}")
    if not _is_git_worktree(worktree_path):
        return _err(f"error: {rest[0]} is not a git worktree")

    config_file = worktree_path / ".wf" / "config.yaml"
    try:
        config_doc = _load_yaml_file(config_file)
    except _MalformedYAML as exc:
        return _err(f"error: parse {config_file}: {exc}")

    p_feedback = _config_path_value(config_doc, "feedback") or \
        ".wf/transient/feedback.yaml"
    p_design_issues = _config_path_value(config_doc, "design_issues") or \
        ".wf/transient/design-issues.yaml"
    p_review_ready = _config_path_value(config_doc, "review_ready") or \
        ".wf/transient/review-ready.yaml"

    abs_feedback = worktree_path / p_feedback
    abs_design_issues = worktree_path / p_design_issues
    abs_review_ready = worktree_path / p_review_ready

    # --- Git state ---
    ok, head_sha = _git_out(worktree_path, "rev-parse", "--short", "HEAD")
    if not ok:
        return _err(f"error: failed to read HEAD from {rest[0]}")

    # Normalise the build sha to short form; fall back to the raw input.
    ok_b, build_short = _git_out(worktree_path, "rev-parse", "--short", build_commit_sha)
    if not ok_b:
        build_short = build_commit_sha

    head_advanced = False
    if head_sha != build_short:
        is_anc, _ = _git_out(
            worktree_path, "merge-base", "--is-ancestor", build_commit_sha, "HEAD"
        )
        if is_anc:
            head_advanced = True

    _, head_subject = _git_out(worktree_path, "log", "-1", "--format=%s", "HEAD")

    # --- Approval-marker match: ^\s*<task-id>\s+review:?\s+approved (case-insensitive) ---
    subject_is_approval = False
    if head_subject:
        approval_re = re.compile(
            r"^\s*" + re.escape(task_id) + r"\s+review:?\s+approved",
            re.IGNORECASE,
        )
        if approval_re.search(head_subject):
            subject_is_approval = True

    verdict = ""
    artifact_rel = ""
    di_id = ""

    di = _open_design_issue_for_task(abs_design_issues, task_id)
    if abs_feedback.is_file():
        verdict = "rejected"
        artifact_rel = p_feedback
    elif di:
        verdict = "design_issue"
        artifact_rel = p_design_issues
        di_id = di
    elif head_advanced and subject_is_approval:
        verdict = "approved"
        artifact_rel = ""
    elif abs_review_ready.is_file() and not abs_feedback.is_file() and head_sha == build_short:
        verdict = "redispatch_same_attempt"
        artifact_rel = p_review_ready
    elif not abs_review_ready.is_file():
        verdict = "defer_to_build_inspector"
        artifact_rel = ""
    else:
        verdict = "escalate_ambiguous"
        artifact_rel = ""

    out = {
        "task_id": task_id,
        "verdict": verdict,
        "head_sha": head_sha,
        "build_commit_sha": build_commit_sha,
        "artifact": artifact_rel if artifact_rel else None,
        "di_id": di_id if di_id else None,
    }
    print(json.dumps(out, separators=(",", ":"), sort_keys=False))
    return 0


# ===========================================================================
# 3. preserve-uncommitted
# ===========================================================================

def _preserve_uncommitted(rest):
    if len(rest) < 2 or not rest[0] or not rest[1]:
        return _err("usage: preserve-uncommitted <worktree-path> <task-id>")
    worktree_path = Path(rest[0])
    task_id = rest[1]

    if not _is_git_worktree(worktree_path):
        return _err(f"error: {rest[0]} is not a git worktree")

    # git status --porcelain=v1 — read RAW stdout (not stripped): the porcelain format
    # is column-sensitive (X = index status at col 0, Y = worktree status at col 1), so
    # stripping would corrupt a leading-space column like " M path".
    try:
        st = subprocess.run(
            ["git", "-C", str(worktree_path), "status", "--porcelain=v1"],
            capture_output=True, text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return _err("error: git status failed", 1)
    if st.returncode != 0:
        return _err("error: git status failed", 1)
    status_output = st.stdout

    tracked_files: list[str] = []
    new_untracked_files: list[str] = []

    for line in status_output.splitlines():
        if not line:
            continue
        x = line[0:1]
        y = line[1:2]
        path_field = line[3:]

        # Rename entries include "old -> new"; keep the new name.
        if " -> " in path_field:
            path_field = path_field.split(" -> ")[-1]
        # Strip surrounding quotes git emits for special-char paths.
        if path_field.endswith('"'):
            path_field = path_field[:-1]
        if path_field.startswith('"'):
            path_field = path_field[1:]

        if x == "?" and y == "?":
            # Untracked = task work in flight (ignored files never reach porcelain
            # v1 without --ignored). All of it is preserved — the expected write
            # set is a planning signal, never a filter on what survives a halt.
            new_untracked_files.append(path_field)
        elif x in "MADRC" or y in "MADRC":
            tracked_files.append(path_field)

    total = len(tracked_files) + len(new_untracked_files)
    if total == 0:
        print("clean")
        return 0

    files_to_stage = tracked_files + new_untracked_files

    body_lines = ""
    if tracked_files:
        body_lines += f"Tracked (modified/staged): {len(tracked_files)} file(s)\n"
        for f in tracked_files:
            body_lines += f"  • {f}\n"
    if new_untracked_files:
        body_lines += f"New (untracked): {len(new_untracked_files)} file(s)\n"
        for f in new_untracked_files:
            body_lines += f"  • {f}\n"

    commit_message = (
        f"chore({task_id}): preserve uncommitted files from a prior halted dispatch\n"
        "\n"
        "Auto-committed by `wf orchestrate preserve-uncommitted` before re-dispatch\n"
        "to prevent a silent merge drop.\n"
        "\n"
        f"{body_lines}"
    )

    # Stage and commit. NEVER --no-verify / git add -A.
    # `-f` (force) is deliberate, not optional: the files in `files_to_stage` were
    # already selected as in-scope work to preserve, and one may match a project
    # gitignore rule — plain `git add <ignored-pathspec>` aborts and takes the whole
    # preserve down with it. `-f` overrides the ignore guard for THIS explicit list
    # only — it is not `git add -A` and does not bypass hooks.
    try:
        add = subprocess.run(
            ["git", "-C", str(worktree_path), "add", "-f", "--", *files_to_stage],
            capture_output=True, text=True,
        )
        if add.returncode != 0:
            return _err("error: git add failed", 1)
        commit = subprocess.run(
            ["git", "-C", str(worktree_path), "commit", "-m", commit_message],
            capture_output=True, text=True,
        )
        if commit.returncode != 0:
            return _err("error: git commit failed", 1)
    except (OSError, subprocess.SubprocessError) as exc:
        return _err(f"error: git operation failed: {exc}", 1)

    ok_sha, sha = _git_out(worktree_path, "rev-parse", "--short", "HEAD")
    if not ok_sha:
        return _err("error: git rev-parse failed", 1)
    print(f"committed {sha}")
    return 0


# ===========================================================================
# 4. sweep-transients
# ===========================================================================

# A host-side handoff artifact is stale iff the task it names has already left the
# build→review loop — its consumer can never come back for it.
_SWEEP_STALE_STATUSES = {"approved", "completed", "blocked", "escalated", "design_issue"}


def _sweep_transients(rest):
    config_path_str = ".wf/config.yaml"
    dry_run = False
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--config":
            if i + 1 >= len(rest):
                return _err("unknown arg: --config")
            config_path_str = rest[i + 1]
            i += 2
        elif a == "--dry-run":
            dry_run = True
            i += 1
        else:
            return _err(f"unknown arg: {a}")

    config_path = Path(config_path_str)
    if not config_path.is_file():
        return _err(f"error: config not found at {config_path_str}")

    config_path = config_path.resolve()
    project_root = (
        config_path.parent.parent if config_path.name == "config.yaml" else Path.cwd()
    ).resolve()

    try:
        config = yaml.safe_load(config_path.read_text()) or {}
    except yaml.YAMLError as e:
        return _err(f"error: parse {config_path}: {e}")

    paths_cfg = config.get("paths", {}) or {}

    def resolve(key, default):
        rel = paths_cfg.get(key, default)
        return (project_root / rel).resolve()

    p_pipeline_state = resolve("pipeline_state", ".wf/transient/pipeline-state.yaml")

    task_states = None  # None → no pipeline-state file → keep everything
    in_preparing = False
    if p_pipeline_state.is_file():
        try:
            doc = yaml.safe_load(p_pipeline_state.read_text()) or {}
        except yaml.YAMLError as e:
            return _err(f"error: parse {p_pipeline_state}: {e}")
        task_states = doc.get("task_states") or {}
        in_preparing = doc.get("current_phase") == "preparing"

    deleted = []
    skipped = []
    pruned = []

    def sweep(path):
        if not path.exists():
            return
        if task_states is None:
            skipped.append({"path": str(path), "reason": "pipeline-state file not found"})
            return
        try:
            artifact = _load_yaml_file(path)
        except _MalformedYAML:
            artifact = {}
        task_id = artifact.get("task_id") if isinstance(artifact, dict) else None
        if not task_id:
            skipped.append({"path": str(path), "reason": "no readable task_id in artifact"})
            return
        task_id = str(task_id)
        ts = task_states.get(task_id)
        if not isinstance(ts, dict):
            skipped.append({"path": str(path),
                            "reason": f"task {task_id} not in task_states"})
            return
        status = ts.get("status") or "pending"
        if status not in _SWEEP_STALE_STATUSES:
            skipped.append({"path": str(path),
                            "reason": f"task {task_id} is {status} — live handoff"})
            return
        if dry_run:
            deleted.append(str(path))
            return
        try:
            path.unlink()
            deleted.append(str(path))
        except OSError as e:
            skipped.append({"path": str(path), "reason": f"unlink failed: {e}"})

    def sweep_design_issues(path):
        """design-issues.yaml holds a LIST of entries, each with its own task_id — the
        top-level-task_id rule above cannot read it, so prune per entry instead.

        An entry is a live handoff iff `status: open` (the same test both readers already
        apply: `_open_design_issue_for_task` here, and `dispatch-fix`, which refuses to
        route anything already resolved/overridden). A closed entry is pruned regardless
        of its task_id — deliberately NOT keyed on task_states, because complete-sprint
        resets pipeline_state to a bare `idle`: by the next run the shipped sprint's task
        states are gone, and a task_states-keyed rule would keep the file forever.
        An open entry prunes only when its task is provably terminal.

        A task-less (slice-scoped) entry is the `preparing` re-design loop's own history:
        dispatch-fix counts those entries to bound the rounds, so while preparing runs they
        survive whatever their status — pruning one under-counts the round on a resume and
        hands wf-sa a lap it should not get. Once preparing is over they are residue.

        Without a pipeline-state file the phase is UNKNOWN, not "not preparing" — keep
        everything, as `sweep` above does. Guessing here prunes the round count and
        silently lifts the re-design bound."""
        if not path.exists():
            return
        if task_states is None:
            skipped.append({"path": str(path), "reason": "pipeline-state file not found"})
            return
        try:
            doc = _load_yaml_file(path)
        except _MalformedYAML:
            skipped.append({"path": str(path), "reason": "malformed YAML — left for a human"})
            return
        issues = doc.get("issues") if isinstance(doc, dict) else None
        if not isinstance(issues, list):
            skipped.append({"path": str(path), "reason": "no readable issues list in artifact"})
            return

        survivors = []
        prune_mark = len(pruned)
        for issue in issues:
            if not isinstance(issue, dict):
                survivors.append(issue)  # unreadable entry — never destroy it
                continue
            iid = str(issue.get("id") or "?")
            status = str(issue.get("status") or "open")
            task_id = issue.get("task_id")
            task_id = "" if task_id is None else str(task_id)
            if not task_id:
                if in_preparing or status == "open":
                    survivors.append(issue)  # live handoff, or this loop's round count
                    continue
                pruned.append({"path": str(path), "id": iid,
                               "reason": f"slice issue is {status} and preparing is over "
                                         "— residue"})
                continue
            if status != "open":
                pruned.append({"path": str(path), "id": iid,
                               "reason": f"issue is {status} — not a live handoff"})
                continue
            ts = task_states.get(task_id)
            if isinstance(ts, dict):
                ts_status = ts.get("status") or "pending"
                if ts_status in _SWEEP_STALE_STATUSES:
                    pruned.append({"path": str(path), "id": iid,
                                   "reason": f"open issue for task {task_id}, which is "
                                             f"{ts_status} — consumer can never return"})
                    continue
            survivors.append(issue)

        if survivors:
            if len(pruned) > prune_mark and not dry_run:
                doc["issues"] = survivors
                try:
                    path.write_text(yaml.safe_dump(doc, sort_keys=False))
                except OSError as e:
                    skipped.append({"path": str(path), "reason": f"rewrite failed: {e}"})
                    return
            skipped.append({"path": str(path),
                            "reason": f"{len(survivors)} live issue(s) retained"})
            return

        # Nothing live left — the file itself is residue.
        if not dry_run:
            try:
                path.unlink()
            except OSError as e:
                skipped.append({"path": str(path), "reason": f"unlink failed: {e}"})
                return
        deleted.append(str(path))

    sweep(resolve("feedback", ".wf/transient/feedback.yaml"))
    sweep(resolve("review_ready", ".wf/transient/review-ready.yaml"))
    sweep_design_issues(resolve("design_issues", ".wf/transient/design-issues.yaml"))

    print(json.dumps({"deleted": deleted, "pruned": pruned, "skipped": skipped},
                     sort_keys=False))
    return 0


# ===========================================================================
# 5. dispatch-fix
# ===========================================================================

# fix_kind → (subagent, human_gate). Build/review classify a design issue from their
# task-scoped context; the orchestrator routes it at the stage boundary. Three autonomous
# kinds — the task contract is wrong (wf-swa amends it), a requirement/ADR is wrong
# (wf-sa, who also owns the slice cut, so re-cutting the slice folds into the spec fix),
# or ALREADY-MERGED component code violates a correct contract+spec (wf-swa authors a
# follow-up task) — plus unknown → human for anything bigger than one task. wf1's
# `recut → wf-po` is dropped: wf2's PO owns capabilities, not sprint cutting. (The prose
# taxonomy lands in wf-skill-spec-references with the build/review DI-writers; this table
# is its executable form — add a new fix_kind here.)
#
# slice_defect is the one kind raised in `preparing`, by wf-swa, against the design-slice
# itself — before any task or sprint exists. It routes to wf-sa like spec_amendment, but
# its envelope carries no task and no sprint, and it is bounded (below).
_ROUTING = {
    "contract_amendment": {"subagent_type": "wf-swa", "human_gate": False},
    "spec_amendment": {"subagent_type": "wf-sa", "human_gate": False},
    "component_defect": {"subagent_type": "wf-swa", "human_gate": False},
    "slice_defect": {"subagent_type": "wf-sa", "human_gate": False},
    "unknown": {"subagent_type": None, "human_gate": True},
}

# How many times wf-sa may re-design a rejected slice before a human rules on it. Each
# lap is autonomous and cheap, but the SA's re-cut is only tested by the NEXT decomposition
# — so a slice still failing after this many laps is one the SA is not converging on.
_MAX_REDESIGN_ROUNDS = 2


def _dispatch_fix(rest):
    config_path_str = ".wf/config.yaml"
    di_id = ""

    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--config":
            if i + 1 >= len(rest):
                return _err("unknown arg: --config")
            config_path_str = rest[i + 1]
            i += 2
        elif a.startswith("-"):
            return _err(f"unknown arg: {a}")
        else:
            di_id = a
            i += 1

    if not di_id:
        return _err("error: DI id is required (positional arg)")

    config_path = Path(config_path_str)
    if not config_path.is_file():
        return _err(f"error: config not found at {config_path_str}")

    config_path = config_path.resolve()
    project_root = (
        config_path.parent.parent if config_path.name == "config.yaml" else Path.cwd()
    ).resolve()

    try:
        config = yaml.safe_load(config_path.read_text()) or {}
    except yaml.YAMLError as exc:
        return _err(f"error: failed to parse {config_path}: {exc}")

    paths = config.get("paths", {}) or {}
    di_path = (project_root / paths.get("design_issues", ".wf/transient/design-issues.yaml")).resolve()
    sprint_path = (project_root / paths.get("sprint", ".wf/transient/sprint.yaml")).resolve()

    if not di_path.exists():
        return _err(f"error: design issues file not found at {di_path}")

    try:
        doc = yaml.safe_load(di_path.read_text()) or {}
    except yaml.YAMLError as exc:
        return _err(f"error: failed to parse {di_path}: {exc}")

    issues = doc.get("issues", []) or []
    entry = next((it for it in issues if isinstance(it, dict) and it.get("id") == di_id), None)
    if entry is None:
        return _err(f"error: DI {di_id} not found in {di_path}")

    status = entry.get("status", "open")
    if status in ("resolved", "overridden"):
        return _err(f"error: DI {di_id} status is '{status}'; no routing decision needed")

    fix_kind = entry.get("fix_kind", "")
    task_id = entry.get("task_id", "")

    route = _ROUTING.get(fix_kind)
    if route is None:
        recognised = sorted(k for k in _ROUTING if k)
        sys.stderr.write(
            f"warning: DI {di_id} has unrecognised fix_kind '{fix_kind}'. "
            f"Treating as 'unknown' (human gate). Recognised values: {recognised}.\n"
        )
        route = _ROUTING["unknown"]
        fix_kind = fix_kind or "unknown"

    # A rejected slice re-designed past the round limit stops being an autonomous repair:
    # every lap so far is on disk, so count them rather than tracking the loop's position.
    reason = ""
    if fix_kind == "slice_defect":
        rounds = sum(1 for it in issues
                     if isinstance(it, dict) and it.get("fix_kind") == "slice_defect")
        if rounds > _MAX_REDESIGN_ROUNDS:
            route = _ROUTING["unknown"]
            # Every re-design past the limit is a human's, so do not name wf-sa as the one
            # failing to converge — the count cannot tell whose cut was rejected, and a
            # human gate whose reason misattributes the cause is the misdescription this
            # route exists to end.
            reason = (
                f"{rounds} slice rejections on this sprint (limit {_MAX_REDESIGN_ROUNDS}) — "
                "the slice is not converging on a decomposable cut. Run /wf-sa interactively."
            )

    def relpath(p):
        try:
            return str(p.relative_to(project_root))
        except ValueError:
            return str(p)

    envelope = {
        "mode": "fix",
        "di_id": di_id,
        "task_id": task_id,
        "di_artifact": relpath(di_path),
    }
    # Name the sprint only when there is one to read: a slice_defect is raised in
    # `preparing`, where wf-swa may have halted before ever writing it.
    if sprint_path.exists():
        envelope["sprint_artifact"] = relpath(sprint_path)

    result = {
        "di_id": di_id,
        "task_id": task_id,
        "fix_kind": fix_kind,
        "subagent_type": route["subagent_type"],
        "human_gate": route["human_gate"],
        "envelope": envelope,
    }
    if reason:
        result["reason"] = reason

    print(json.dumps(result, indent=2, sort_keys=False))
    return 1 if route["human_gate"] else 0


COMMANDS = {
    ("orchestrate", "inspect-build-return"): _inspect_build_return,
    ("orchestrate", "inspect-review-return"): _inspect_review_return,
    ("orchestrate", "preserve-uncommitted"): _preserve_uncommitted,
    ("orchestrate", "sweep-transients"): _sweep_transients,
    ("orchestrate", "dispatch-fix"): _dispatch_fix,
}
