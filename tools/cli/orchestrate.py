"""wf orchestrate — the orchestrator's mechanical helpers.

The judgment-free verdict scripts: the controller (the skill OR the Python driver)
shells out to these between sub-agent dispatches, then switches on their EXACT
stdout and exit codes. Verdicts come from on-disk artifacts, never from parsing a
sub-agent's prose — compliance by mechanism.

  inspect-build-return   Build Return Protocol detector
  inspect-review-return  Review Return Protocol detector
  preserve-uncommitted   commit in-scope uncommitted work before re-dispatch
  sweep-transients       session-start broom (resume safety)
  classify-amendment     scope amendment: feature / mechanical_follow_on / reject
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

import datetime
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

    p_build_blocked = _config_path_value(config_doc, "build_blocked") or \
        ".wf/transient/build-blocked.yaml"
    p_review_ready = _config_path_value(config_doc, "review_ready") or \
        ".wf/transient/review-ready.yaml"
    p_build_progress = _config_path_value(config_doc, "build_progress") or \
        ".wf/transient/build-progress.yaml"

    abs_build_blocked = worktree_path / p_build_blocked
    abs_review_ready = worktree_path / p_review_ready
    abs_build_progress = worktree_path / p_build_progress

    verdict = ""
    artifact_rel = ""
    last_step = ""

    if abs_build_blocked.is_file():
        verdict = "build_blocked"
        artifact_rel = p_build_blocked
    elif abs_review_ready.is_file():
        verdict = "ready_for_review"
        artifact_rel = p_review_ready
    elif abs_build_progress.is_file():
        artifact_rel = p_build_progress
        try:
            bp_doc = _load_yaml_file(abs_build_progress)
        except _MalformedYAML as exc:
            return _err(f"error: parse {abs_build_progress}: {exc}")
        ls = bp_doc.get("last_step")
        last_step = "" if ls is None else str(ls)
        if last_step == "review_ready_written":
            verdict = "recovered_lost_signal_review_ready_written"
        elif last_step == "committed":
            verdict = "recovered_lost_signal_committed"
        elif last_step == "all_gates_passed":
            verdict = "resume_after_gates"
        else:
            verdict = "restart"
    else:
        verdict = "escalate_no_artifacts"

    out = {
        "task_id": task_id,
        "verdict": verdict,
        "artifact": artifact_rel if artifact_rel else None,
        "last_step": last_step if last_step else None,
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
    p_review_ready = _config_path_value(config_doc, "review_ready") or \
        ".wf/transient/review-ready.yaml"

    abs_feedback = worktree_path / p_feedback
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

    if abs_feedback.is_file():
        verdict = "rejected"
        artifact_rel = p_feedback
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

    # Resolve paths.current_task from the worktree's config (canonical default).
    current_task_rel = ".wf/transient/current-task.yaml"
    worktree_config = worktree_path / ".wf" / "config.yaml"
    try:
        config_doc = _load_yaml_file(worktree_config)
    except _MalformedYAML:
        config_doc = {}
    resolved = _config_path_value(config_doc, "current_task")
    if resolved:
        current_task_rel = resolved

    current_task_file = worktree_path / current_task_rel

    # Parse files_to_touch into an in-scope set (empty if file missing/absent key).
    in_scope: set[str] = set()
    try:
        ct_doc = _load_yaml_file(current_task_file)
    except _MalformedYAML:
        ct_doc = {}
    for p in (ct_doc.get("files_to_touch") or []):
        if p:
            in_scope.add(str(p))

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
    new_in_scope_files: list[str] = []

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
            if path_field in in_scope:
                new_in_scope_files.append(path_field)
        elif x in "MADRC" or y in "MADRC":
            tracked_files.append(path_field)
        # `!!` (ignored) and other states fall through.

    total = len(tracked_files) + len(new_in_scope_files)
    if total == 0:
        print("clean")
        return 0

    files_to_stage = tracked_files + new_in_scope_files

    body_lines = ""
    if tracked_files:
        body_lines += f"Tracked (modified/staged): {len(tracked_files)} file(s)\n"
        for f in tracked_files:
            body_lines += f"  • {f}\n"
    if new_in_scope_files:
        body_lines += (
            f"New in scope (untracked, matched files_to_touch): "
            f"{len(new_in_scope_files)} file(s)\n"
        )
        for f in new_in_scope_files:
            body_lines += f"  • {f}\n"

    commit_message = (
        f"chore({task_id}): preserve uncommitted files from prior build_blocked halt\n"
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
    p_feedback = resolve("feedback", ".wf/transient/feedback.yaml")
    p_review_ready = resolve("review_ready", ".wf/transient/review-ready.yaml")
    p_build_blocked = resolve("build_blocked", ".wf/transient/build-blocked.yaml")
    p_build_progress = resolve("build_progress", ".wf/transient/build-progress.yaml")

    history = []
    if p_pipeline_state.exists():
        try:
            doc = yaml.safe_load(p_pipeline_state.read_text()) or {}
        except yaml.YAMLError as e:
            return _err(f"error: parse {p_pipeline_state}: {e}")
        history = doc.get("history", []) or []

    def parse_ts(s):
        if not isinstance(s, str):
            return None
        try:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return dt
        except ValueError:
            return None

    def mtime(p):
        try:
            return datetime.datetime.fromtimestamp(
                p.stat().st_mtime, tz=datetime.timezone.utc
            )
        except FileNotFoundError:
            return None

    def has_event_after(events, after):
        if after is None:
            return False
        for h in history:
            if not isinstance(h, dict):
                continue
            if h.get("event") not in events:
                continue
            ts = parse_ts(h.get("ts"))
            if ts is not None and ts > after:
                return True
        return False

    deleted = []
    skipped = []

    def consume(path, ok, reason_if_skipped):
        if not path.exists():
            return
        if ok:
            if dry_run:
                deleted.append(str(path))
            else:
                try:
                    path.unlink()
                    deleted.append(str(path))
                except OSError as e:
                    skipped.append({"path": str(path), "reason": f"unlink failed: {e}"})
        else:
            skipped.append({"path": str(path), "reason": reason_if_skipped})

    # A transient is stale iff a history event recording its consumption landed AFTER
    # the file's own mtime (the orchestrator returned past it). Otherwise it is a live
    # handoff awaiting its consumer — keep it.
    m = mtime(p_feedback)
    consume(
        p_feedback,
        has_event_after({"build_returned", "task_redispatched_after_di_resolution"}, m),
        "no build_returned or task_redispatched_after_di_resolution after mtime",
    )

    m = mtime(p_review_ready)
    consume(
        p_review_ready,
        has_event_after({"review_returned", "task_redispatched_after_di_resolution"}, m),
        "no review_returned or task_redispatched_after_di_resolution after mtime",
    )

    m = mtime(p_build_blocked)
    consume(
        p_build_blocked,
        has_event_after(
            {"scope_amendment_applied", "amendment_with_mechanical_follow_on", "escalated"}, m
        ),
        "no scope_amendment_applied / amendment_with_mechanical_follow_on / escalated after mtime",
    )

    m = mtime(p_build_progress)
    recovery_seen = has_event_after(
        {
            "recovered_lost_return_signal",
            "recovered_committed_without_handoff",
            "resuming_after_gate_pass",
        },
        m,
    )
    rr_m = mtime(p_review_ready)
    review_ready_newer = (m is not None and rr_m is not None and rr_m >= m)
    consume(
        p_build_progress,
        recovery_seen or review_ready_newer,
        "no recovery event after mtime and no newer review_ready.yaml",
    )

    print(json.dumps({"deleted": deleted, "skipped": skipped}, sort_keys=False))
    return 0


# ===========================================================================
# 5. classify-amendment
# ===========================================================================

_DEFAULT_TEST_PATTERNS = [
    r"_test\.go$",
    r"\.test\.(ts|tsx|js|jsx)$",
    r"\.spec\.(ts|tsx|js|jsx)$",
    r"_test\.py$",
    r"(^|/)test_[^/]+\.py$",
    r"(^|/)tests?/",
    r"(^|/)spec/",
]

_NEW_TEST_DECL_PATTERNS = [
    r"^\+\s*func\s+Test[A-Z_]",        # Go
    r"^\+\s*func\s+Benchmark[A-Z_]",   # Go benchmarks
    r"^\+\s*describe\s*\(",            # Jest/Vitest/Jasmine/Mocha JS
    r"^\+\s*it\s*\(",                  # JS test cases
    r"^\+\s*test\s*\(",               # JS test cases
    r"^\+\s*def\s+test_",              # Python pytest / unittest
    r"^\+\s*class\s+Test[A-Z_]",       # Python unittest TestCase class
]


def _classify_amendment(rest):
    task_id = ""
    diff_path = ""
    claim = ""
    extra_patterns: list[str] = []

    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--task-id":
            if i + 1 >= len(rest):
                return _err("unknown arg: --task-id")
            task_id = rest[i + 1]
            i += 2
        elif a == "--diff":
            if i + 1 >= len(rest):
                return _err("unknown arg: --diff")
            diff_path = rest[i + 1]
            i += 2
        elif a == "--test-pattern":
            if i + 1 >= len(rest):
                return _err("unknown arg: --test-pattern")
            extra_patterns.append(rest[i + 1])
            i += 2
        elif a == "--claim":
            if i + 1 >= len(rest):
                return _err("unknown arg: --claim")
            claim = rest[i + 1]
            i += 2
        else:
            return _err(f"unknown arg: {a}")

    if not task_id or not diff_path:
        return _err("error: --task-id and --diff are required")

    diff_file = Path(diff_path)
    if not diff_file.is_file():
        return _err(f"error: diff file not found at {diff_path}")

    diff_text = diff_file.read_text(errors="replace")

    all_patterns = _DEFAULT_TEST_PATTERNS + extra_patterns
    test_re = re.compile("|".join(all_patterns))

    # Extract files from the diff: `diff --git a/.. b/..` → the b/<path>.
    files_changed: list[str] = []
    b_re = re.compile(r" b/([^ ]+)$")
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            mm = b_re.search(line)
            if mm:
                files_changed.append(mm.group(1))

    if not files_changed:
        print("feature")
        return 0

    non_test_files = [f for f in files_changed if not test_re.search(f)]

    # Any non-test file touched → feature (or reject if claimed mechanical).
    if non_test_files:
        print("reject" if claim == "mechanical_follow_on" else "feature")
        return 0

    # All-test amendment: any new test declarations among the added (+) lines?
    decl_re = re.compile("|".join(_NEW_TEST_DECL_PATTERNS))
    has_new_decl = any(decl_re.search(line) for line in diff_text.splitlines())
    if has_new_decl:
        print("reject" if claim == "mechanical_follow_on" else "feature")
        return 0

    print("mechanical_follow_on")
    return 0


# ===========================================================================
# 6. dispatch-fix
# ===========================================================================

# fix_kind → (subagent, human_gate). Build/review classify a design issue from their
# task-scoped context; the orchestrator routes it at the stage boundary. Two autonomous
# kinds — the task contract is wrong (wf-swa), or a requirement/ADR is wrong (wf-sa, who
# also owns the slice cut, so re-cutting the slice folds into the spec fix) — plus
# unknown → human for anything bigger than one task. wf1's `recut → wf-po` is dropped:
# wf2's PO owns capabilities, not sprint cutting. (The prose taxonomy lands in
# wf-skill-spec-references with the build/review DI-writers; this table is its executable
# form — add a new fix_kind here.)
_ROUTING = {
    "contract_amendment": {"subagent_type": "wf-swa", "human_gate": False},
    "spec_amendment": {"subagent_type": "wf-sa", "human_gate": False},
    "unknown": {"subagent_type": None, "human_gate": True},
}


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
        "sprint_artifact": relpath(sprint_path),
    }

    result = {
        "di_id": di_id,
        "task_id": task_id,
        "fix_kind": fix_kind,
        "subagent_type": route["subagent_type"],
        "human_gate": route["human_gate"],
        "envelope": envelope,
    }

    print(json.dumps(result, indent=2, sort_keys=False))
    return 1 if route["human_gate"] else 0


COMMANDS = {
    ("orchestrate", "inspect-build-return"): _inspect_build_return,
    ("orchestrate", "inspect-review-return"): _inspect_review_return,
    ("orchestrate", "preserve-uncommitted"): _preserve_uncommitted,
    ("orchestrate", "sweep-transients"): _sweep_transients,
    ("orchestrate", "classify-amendment"): _classify_amendment,
    ("orchestrate", "dispatch-fix"): _dispatch_fix,
}
