"""The increment loop — one increment at a time, JIT.

Per increment: the Tech Lead authors its contracts against the merged tree, the
pipeline layers them into sub-layers, each sub-layer's tasks build and review in
parallel worktrees and batch-merge at its end, and the increment boundary runs the
heavy checks and clears whatever design issues the round raised.

Every routing decision here comes from a `wf` verb's JSON or from an artifact on
disk. Nothing routes on what an agent said.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import config  # noqa: F401 — importing it puts the CLI package on sys.path
import issues
import procs
import slice as slice_reader  # the CLI's slice parser — one reader, not a copy
import stoprules
from runtime import Halt

# Bounds on the loops that are driven by external state. A well-behaved run never
# reaches them; hitting one means the pipeline is not converging and a human must look.
MAX_SUBLAYER_ITERATIONS = 400
MAX_INCREMENT_ROUNDS = 5

_CHECKPOINT_RE = re.compile(r"^\s*[-*]?\s*\**\s*Checkpoint\s*\**\s*:?\s*(.+)$",
                            re.IGNORECASE | re.MULTILINE)


# ── the increment ────────────────────────────────────────────────────────────


def run_increment_loop(rt, numbers) -> None:
    """Run each declared increment in order, resuming at the one the state names."""
    for number in numbers:
        if number < rt.state.increment:
            continue
        rt.state.increment = number
        rt.state.save()
        rt.tele.event("increment_start", sprint_id=rt.state.sprint_id,
                      increment=number)
        run_increment(rt, number)
    rt.state.increment = 1
    rt.state.save()


def run_increment(rt, number) -> None:
    prepare_contracts(rt, number)
    rt.cli.mutate("pipeline", "transition", "--to", "running_stage",
                  "--reason", f"increment {number}")
    force = False
    for _ in range(MAX_INCREMENT_ROUNDS):
        args = ["pipeline", "compute-stages", "--increment", str(number)]
        if force:
            args.append("--force")
        res = rt.cli.mutate(*args)
        if not res.ok:
            raise Halt("compute_stages", res.stderr.strip() or str(res.data))
        if not rt.dry_run and not res.data.get("stages"):
            raise Halt("no_sub_layers",
                       f"increment {number} layered into nothing — every one of its "
                       f"tasks is already terminal in the run state. The Tech Lead "
                       f"may have reused a task id an earlier increment merged.")
        sublayer_loop(rt, number)
        if boundary(rt, number) == "done":
            rt.tele.event("increment_done", sprint_id=rt.state.sprint_id,
                          increment=number)
            return
        force = True
    raise Halt("increment_not_converging", f"increment {number} kept reworking")


def prepare_contracts(rt, number) -> None:
    """Get a green ``sprint check`` for this increment: dispatch the Tech Lead, and
    route every rejection through the design role's repair mode."""
    needs_tl = not _has_increment(rt, number)
    for _ in range(rt.cfg.max_attempts + 1):
        if needs_tl:
            # The run state keys task states by id across the whole sprint, so an id an
            # earlier increment already merged would come back pre-completed. Name them.
            used = ", ".join(sorted(rt.worktrees)) or "none yet"
            rt.agents.launch("wf-tl",
                             {"Increment": number, "sprint_id": rt.state.sprint_id,
                              "task ids already used this sprint": used},
                             increment=number)
            needs_tl = False
        item = _first_open_issue(rt)
        if item:
            slice_scoped = issues.is_slice_scoped(item)
            if slice_scoped:
                # the contracts were decomposed from a cut the design role is about to
                # replace; leaving them on disk ships the rejected increment's tasks
                sprint_path = rt.cfg.path_opt("sprint")
                if sprint_path and sprint_path.exists():
                    sprint_path.unlink()
            issues.repair(rt, item)
            needs_tl = slice_scoped or not _has_increment(rt, number)
            continue
        if not _has_increment(rt, number):
            raise Halt("tl_no_contracts",
                       f"the Tech Lead left no tasks for increment {number} and "
                       f"raised no design issue")
        if _contracts_green(rt):
            return
        issues.record(rt, _gate_summary(rt, "sprint check"), scope="slice")
        item = _first_open_issue(rt)
        issues.repair(rt, item)
    raise Halt("contracts_not_ready", f"increment {number} never reached a green gate")


def _has_increment(rt, number) -> bool:
    res = rt.cli.read("pipeline", "increments")
    if not res.ok:
        return False
    return any(str(entry.get("increment")) == str(number)
               for entry in (res.data.get("increments") or []))


def _contracts_green(rt) -> bool:
    if not rt.cli.mutate("sprint", "materialize").ok:
        return False
    return rt.cli.read("sprint", "check").ok


def _gate_summary(rt, gate: str) -> str:
    res = rt.cli.read(*gate.split())
    errors = res.data.get("errors") or []
    lines = [f"{e.get('code')}: {e.get('msg')}" if isinstance(e, dict) else str(e)
             for e in errors]
    return f"`wf {gate}` is red — " + ("; ".join(lines) or "no findings emitted")


def _first_open_issue(rt):
    open_items = issues.open_entries(rt)
    return open_items[0] if open_items else None


# ── the sub-layer loop ───────────────────────────────────────────────────────


def sublayer_loop(rt, number) -> None:
    started = set()
    for _ in range(MAX_SUBLAYER_ITERATIONS):
        res = rt.cli.read("pipeline", "next")
        if not res.ok:
            raise Halt("pipeline_next", res.stderr.strip() or "the frontier is unreadable")
        data = res.data
        terminal = data.get("terminal") or {}
        if terminal.get("halt"):
            raise Halt("pipeline_halt", str((terminal["halt"] or {}).get("reason")))

        index = int((data.get("stage") or {}).get("index") or 1)
        if index not in started:
            rt.cli.mutate("pipeline", "stage-start", "--stage", str(index))
            started.add(index)

        if not terminal.get("stage_done"):
            _run_frontier(rt, data, number)
            continue
        if data.get("repairing"):
            if _resolve_open_issues(rt):
                # a follow-up task or a re-cut changed the graph: re-layer before asking
                # for the frontier again, or the new work is never dispatched
                rt.cli.mutate("pipeline", "compute-stages", "--increment", str(number),
                              "--force")
                started.discard(index)
            continue

        rt.cli.mutate("pipeline", "propagate-blocks")
        if _merge_batch(rt, data.get("approved") or []):
            continue  # a conflict parked a task: let the frontier re-settle first
        rt.cli.mutate("pipeline", "stage-summary", "--stage", str(index))
        rt.cli.mutate("pipeline", "stage-end", "--stage", str(index))
        if rt.cfg.history_cap:
            rt.cli.mutate("pipeline", "archive-history", "--cap", str(rt.cfg.history_cap))
        if terminal.get("increment_done"):
            return
        rt.cli.mutate("pipeline", "advance-stage")
    raise Halt("sublayer_loop", f"increment {number} did not settle")


def _run_frontier(rt, data, number) -> None:
    entries = data.get("dispatch") or []
    if not entries:
        raise Halt("stalled_frontier",
                   "the sub-layer has pending work but nothing dispatchable")
    workers = max(1, min(int(rt.cfg.driver("max_parallel")), len(entries)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_run_task, rt, entry, number) for entry in entries]
        for future in futures:
            future.result()


def _run_task(rt, entry, number) -> None:
    task_id = entry["task_id"]
    worktree = (rt.cfg.root / entry["worktree"]).resolve()
    rt.worktrees[task_id] = worktree
    branch = _task_branch(rt, task_id)
    rt.git.worktree_add(worktree, branch, rt.state.sprint_branch)
    contract = worktree / rt.cfg.rel("current_task")
    written = rt.cli.mutate("sprint", "task", task_id, "--write", str(contract))
    if not written.ok:
        rt.cli.mutate("pipeline", "block-task", task_id,
                      "--reason", "the build envelope could not be written")
        return

    for _ in range(rt.cfg.max_attempts):
        attempt = _attempt(rt, task_id)
        rt.cli.mutate("pipeline", "dispatch", "--agent", "wf-build",
                      "--task", task_id, "--attempt", str(attempt))
        rt.agents.launch("wf-build",
                         {"task_id": task_id, "worktree": str(worktree),
                          "contract": rt.cfg.rel("current_task"), "attempt": attempt},
                         cwd=worktree, task_id=task_id, increment=number)
        verdict = _inspect(rt, "inspect-build-return", worktree, task_id)
        kind = verdict.get("verdict")
        if kind == "design_issue":
            issues.promote(rt, worktree, verdict.get("di_id"), task_id)
            return
        if kind != "ready_for_review":
            rt.cli.mutate("pipeline", "block-task", task_id,
                          "--reason", f"build returned {kind}")
            return
        outcome = _review_chain(rt, worktree, task_id, verdict.get("build_commit_sha"),
                                number)
        if outcome != "rejected":
            return
    rt.cli.mutate("pipeline", "block-task", task_id,
                  "--reason", "review rejected the build at every allowed attempt")


def _review_chain(rt, worktree, task_id, build_sha, number) -> str:
    """Run the configured review passes over one build. Returns the outcome that
    ends the chain: approved, rejected, design_issue or blocked."""
    index = 0
    passes = rt.cfg.review_passes
    for _ in range(len(passes) * rt.cfg.max_attempts + 1):
        if index >= len(passes):
            rt.cli.mutate("pipeline", "approve-task", task_id, "--commit", build_sha)
            return "approved"
        agent = passes[index]
        rt.cli.mutate("pipeline", "dispatch", "--agent", agent, "--task", task_id,
                      "--attempt", str(_attempt(rt, task_id)), "--pass", str(index))
        rt.agents.launch(agent,
                         {"mode": "review", "task_id": task_id,
                          "worktree": str(worktree),
                          "sprint_branch": rt.state.sprint_branch, "pass": agent},
                         cwd=worktree, task_id=task_id, increment=number)
        verdict = _inspect(rt, "inspect-review-return", worktree, task_id, build_sha)
        kind = verdict.get("verdict")
        if kind == "approved":
            index += 1
            continue
        if kind == "redispatch_same_attempt":
            continue
        if kind == "design_issue":
            issues.promote(rt, worktree, verdict.get("di_id"), task_id)
            return "design_issue"
        if kind == "rejected":
            artifact = verdict.get("artifact") or rt.cfg.rel("feedback")
            rt.cli.mutate("pipeline", "reject-task", task_id,
                          "--feedback", str(worktree / artifact))
            return "rejected"
        if kind == "defer_to_build_inspector":
            build_verdict = _inspect(rt, "inspect-build-return", worktree, task_id)
            if build_verdict.get("verdict") == "design_issue":
                issues.promote(rt, worktree, build_verdict.get("di_id"), task_id)
                return "design_issue"
            rt.cli.mutate("pipeline", "block-task", task_id,
                          "--reason", "the review left no readable verdict")
            return "blocked"
        rt.cli.mutate("pipeline", "block-task", task_id,
                      "--reason", f"review returned {kind}")
        return "blocked"
    rt.cli.mutate("pipeline", "block-task", task_id,
                  "--reason", "the review chain did not settle")
    return "blocked"


def _inspect(rt, verb, worktree, task_id, *extra) -> dict:
    """The return-protocol helpers: a verdict read from the worktree's artifacts."""
    rt.cli.raw("orchestrate", "preserve-uncommitted", str(worktree), task_id,
               mutating=True)
    res = rt.cli.raw("orchestrate", verb, str(worktree), task_id, *extra)
    return res.data if isinstance(res.data, dict) else {}


def _attempt(rt, task_id) -> int:
    res = rt.cli.read("pipeline", "task-state", task_id)
    return int(res.data.get("attempt_counter") or 0)


def _task_branch(rt, task_id) -> str:
    return f"task/{rt.state.sprint_id}-{task_id}"


# ── merges and the boundary ──────────────────────────────────────────────────


def _merge_batch(rt, approved) -> bool:
    """Merge the approved set into the sprint branch. Returns True when a conflict
    parked a task — the driver never resolves one: it records a design issue and lets
    the repair ladder decide, which is the mechanical successor to the old write-set
    overlap check."""
    if not approved:
        return False
    rt.git.checkout(rt.state.sprint_branch)
    conflicted = False
    for task_id in approved:
        branch = _task_branch(rt, task_id)
        build_sha = rt.cli.read("pipeline", "task-state", task_id).data.get("build_commit")
        result = rt.git.merge(branch, f"{task_id}: merge")
        if result.ok:
            rt.cli.mutate("pipeline", "complete-task", task_id,
                          "--commit", str(build_sha), "--merge", result.sha)
            rt.git.worktree_remove(_worktree_of(rt, task_id), branch)
            rt.tele.event("merge", task_id=task_id, sprint_id=rt.state.sprint_id,
                          merge_commit=result.sha)
            continue
        conflicted = True
        files = ", ".join(result.files) or "unknown paths"
        issues.record(rt, f"merging {branch} into {rt.state.sprint_branch} conflicted "
                          f"in {files} — two parallel tasks changed the same lines; "
                          f"the merge was left unapplied",
                      task_id=task_id, severity="high")
        rt.tele.event("merge_conflict", task_id=task_id, sprint_id=rt.state.sprint_id,
                      files=files)
    return conflicted


def _worktree_of(rt, task_id) -> Path:
    return rt.worktrees.get(task_id) or (
        rt.cfg.worktree_base / f"{rt.state.sprint_id}-{task_id}")


def boundary(rt, number) -> str:
    """The increment boundary: heavy checks, design-issue repair, and the stop
    signals a running sprint honours. Returns 'done' or 'rework'."""
    command = rt.cfg.command("stage_check")
    if command:
        attempts = 0
        while True:
            done = procs.run(command, timeout=int(rt.cfg.driver("command_timeout_s")),
                             cwd=rt.cfg.root, shell=True,
                             stdout_path=rt.cfg.path("transient") /
                             f"stage-check-{rt.state.sprint_id}-{number}.log")
            rt.tele.event("stage_check", increment=number, exit_code=done.rc,
                          duration_s=done.duration_s,
                          sprint_id=rt.state.sprint_id)
            if done.rc == 0:
                break
            attempts += 1
            if attempts > rt.cfg.max_attempts:
                raise Halt("stage_check_red",
                           f"increment {number}'s heavy checks stayed red after "
                           f"{rt.cfg.max_attempts} repair rounds")
            rt.agents.launch("wf-stage-repair",
                             {"mode": "repair",
                              "sprint_branch": rt.state.sprint_branch,
                              "Increment": number,
                              "Checkpoint": checkpoint(rt, number)},
                             increment=number, mode="repair")
            if issues.open_entries(rt):
                _resolve_open_issues(rt)
                return "rework"

    if _resolve_open_issues(rt):
        return "rework"

    stop = stoprules.at_boundary(rt.cfg, rt.git)
    if stop:
        rt.state.stop_pending = stop.reason
        rt.state.save()
        rt.tele.event("stop_pending", reason=stop.reason, detail=stop.detail,
                      sprint_id=rt.state.sprint_id)
    return "done"


def _resolve_open_issues(rt) -> bool:
    """Repair every open design issue. True when one needed a re-layer of the tasks."""
    relayer = False
    for item in issues.open_entries(rt):
        fix_kind = issues.repair(rt, item)
        if fix_kind in ("component_defect", "slice_recut"):
            relayer = True
    return relayer


def checkpoint(rt, number) -> str:
    """The increment's observable checkpoint, from the slice — what must demonstrably
    work now. Falls back to the increment's whole section when it names none."""
    path = rt.cfg.path_opt("design_slice")
    if not path or not path.exists():
        return ""
    section = slice_reader.increment_section(path.read_text(), number)
    found = _CHECKPOINT_RE.search(section)
    return found.group(1).strip() if found else section.strip()[:600]
