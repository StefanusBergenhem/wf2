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
import dispatch
import issues
import procs
import progress
import slice as slice_reader  # the CLI's slice parser — one reader, not a copy
import stoprules
import yaml
from runtime import Halt

# Bounds on the loops that are driven by external state. A well-behaved run never
# reaches them; hitting one means the pipeline is not converging and a human must look.
MAX_SUBLAYER_ITERATIONS = 400
MAX_INCREMENT_ROUNDS = 5
# How many times the Tech Lead's output is re-cut before the increment gives up, and
# how many stage-repair rounds an increment boundary gets. Each loop owns its own
# budget: `review.max_attempts` bounds one task's build→review chain and nothing else.
CONTRACT_PREP_ATTEMPTS = 3
STAGE_REPAIR_ATTEMPTS = 3
# How many times a build that left no artifact at all is sent back in. Its own budget,
# for the same reason: a dispatch that produced nothing to judge is a different failure
# from a build the review judged and rejected, and must not spend that one's fix cycles.
REDISPATCH_ATTEMPTS = 2

# The slice template writes `- **Checkpoint:** <what>`; a plain `Checkpoint: <what>` is
# accepted too. The bold markers close AFTER the colon, so they are consumed on both
# sides of it — capturing them leaves the envelope's checkpoint starting with `**`.
_CHECKPOINT_RE = re.compile(
    r"^\s*[-*]?\s*\*{0,2}\s*Checkpoint\s*\*{0,2}\s*[:—–]\s*\*{0,2}\s*(.+?)\s*\*{0,2}\s*$",
    re.IGNORECASE | re.MULTILINE)


# ── the increment ────────────────────────────────────────────────────────────


def run_increment_loop(rt, numbers) -> None:
    """Run each declared increment in order, resuming at the one the state names."""
    rt.report.line(f"increments to build: "
                   f"{', '.join(str(n) for n in numbers) or 'none declared'}")
    for number in numbers:
        if number < rt.state.increment:
            continue
        rt.state.increment = number
        rt.state.save()
        rt.tele.event("increment_start", sprint=rt.state.sprint_id, increment=number)
        run_increment(rt, number)
    rt.state.increment = 1
    rt.state.save()


def run_increment(rt, number) -> None:
    rt.report.phase(f"increment {number} · authoring contracts")
    prepare_contracts(rt, number)
    rt.cli.mutate("pipeline", "transition", "--to", "running_stage",
                  "--reason", f"increment {number}")
    force = False
    for round_no in range(1, MAX_INCREMENT_ROUNDS + 1):
        rt.report.phase(f"increment {number} · building"
                        + (f" · rework round {round_no}" if round_no > 1 else ""))
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
        stages = res.data.get("stages") or []
        rt.report.line(f"layered into {len(stages)} sub-layer(s)", indent=1)
        sublayer_loop(rt, number)
        if boundary(rt, number) == "done":
            rt.tele.event("increment_done", sprint=rt.state.sprint_id,
                          increment=number)
            return
        force = True
    raise Halt("increment_not_converging", f"increment {number} kept reworking")


def prepare_contracts(rt, number) -> None:
    """Get a green ``sprint check`` for this increment: dispatch the Tech Lead, and
    route every rejection through the design role's repair mode."""
    needs_tl = not _has_increment(rt, number)
    launched = None
    for _ in range(CONTRACT_PREP_ATTEMPTS + 1):
        if needs_tl:
            # The run state keys task states by id across the whole sprint, so an id an
            # earlier increment already merged would come back pre-completed. Name them
            # from the sprint file — the durable record, so a restart names them too.
            used = ", ".join(_task_ids_so_far(rt)) or "none yet"
            launched = rt.agents.launch(
                "wf-tl",
                {"Increment": number, "sprint_id": rt.state.sprint_id,
                 "task ids already used this sprint": used},
                increment=number)
            needs_tl = False
        item = _first_open_issue(rt)
        if item:
            slice_scoped = issues.is_slice_scoped(item)
            if slice_scoped:
                _prune_recut(rt, item, number)
            issues.repair(rt, item)
            needs_tl = slice_scoped or not _has_increment(rt, number)
            continue
        if not _has_increment(rt, number):
            # "left no tasks" is a statement about the Tech Lead's judgement; it is only
            # true if the Tech Lead ran at all
            dispatch.check_launch(launched)
            raise Halt("tl_no_contracts",
                       f"the Tech Lead left no tasks for increment {number} and "
                       f"raised no design issue")
        if _contracts_green(rt):
            rt.report.line("sprint check green — contracts ready",
                           symbol=progress.OK, indent=1)
            return
        issues.record(rt, _gate_summary(rt, "sprint check"), scope="slice")
        item = _first_open_issue(rt)
        issues.repair(rt, item)
    raise Halt("contracts_not_ready", f"increment {number} never reached a green gate")


def _task_ids_so_far(rt) -> list:
    """Every task id the sprint's cumulative contract file already carries, in file
    order — what the next Tech Lead must not reuse."""
    path = rt.cfg.path_opt("sprint")
    if not path or not path.exists():
        return []
    try:
        doc = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError:
        return []
    tasks = (doc.get("tasks") or []) if isinstance(doc, dict) else []
    return [str(t["id"]) for t in tasks if isinstance(t, dict) and t.get("id")]


def _prune_recut(rt, item, number) -> None:
    """Drop the contracts a slice re-cut invalidates: the increment the design issue
    names, or — when it names none — every increment the sprint has not merged. The
    merged ones stay on disk; their tasks are the merge record the sprint closes on,
    and `sprint prune` refuses them anyway."""
    sprint_path = rt.cfg.path_opt("sprint")
    if not sprint_path or not sprint_path.exists():
        return
    named = item.get("increment")
    targets = [named] if named is not None else _unmerged_increments(rt, number)
    for target in targets:
        rt.cli.mutate("sprint", "prune", "--increment", str(target))


def _unmerged_increments(rt, number) -> list:
    """Every increment the sprint declares whose tasks have not all merged. Falls back
    to the increment in hand when the frontier cannot be read."""
    res = rt.cli.read("pipeline", "increments")
    if not res.ok:
        return [number]
    return [entry.get("increment") for entry in (res.data.get("increments") or [])
            if not entry.get("done")] or [number]


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


def gate_findings(data) -> str:
    """The findings a gate's JSON carries, as one line — what a halt or a repair
    dispatch has to name for its reader to act on it."""
    errors = (data or {}).get("errors") or []
    lines = [f"{e.get('code')}: {e.get('msg')}" if isinstance(e, dict) else str(e)
             for e in errors]
    return "; ".join(lines) or "no findings emitted"


def _gate_summary(rt, gate: str) -> str:
    return f"`wf {gate}` is red — " + gate_findings(rt.cli.read(*gate.split()).data)


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
            rt.report.line(f"sub-layer {index}", symbol=progress.RUN, indent=1)
            rt.cli.mutate("pipeline", "stage-start", "--stage", str(index))
            started.add(index)

        if not terminal.get("stage_done"):
            _run_frontier(rt, data, number)
            continue
        if data.get("repairing"):
            rt.report.line(f"sub-layer {index} is repairing — resolving open design "
                           f"issues", indent=1)
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
        rt.report.line(f"sub-layer {index} done", symbol=progress.OK, indent=1)
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
    rt.report.line(
        f"dispatching {len(entries)} task(s) "
        f"({', '.join(str(e.get('task_id')) for e in entries)}) "
        f"· {workers} in parallel", indent=2)
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
        rt.report.line(f"task {task_id} blocked — no build envelope could be written",
                       symbol=progress.BAD, indent=2)
        rt.cli.mutate("pipeline", "block-task", task_id,
                      "--reason", "the build envelope could not be written")
        return

    rejections = redispatches = 0
    while True:
        attempt = _attempt(rt, task_id)
        rt.cli.mutate("pipeline", "dispatch", "--agent", "wf-build",
                      "--task", task_id, "--attempt", str(attempt))
        _consume(rt, worktree, "review_ready")
        launched = rt.agents.launch(
            "wf-build",
            {"task_id": task_id, "worktree": str(worktree),
             "contract": rt.cfg.rel("current_task"), "attempt": attempt},
            cwd=worktree, task_id=task_id, increment=number)
        verdict = _inspect(rt, "inspect-build-return", worktree, task_id)
        kind = verdict.get("verdict")
        rt.report.line(f"task {task_id} · build (attempt {attempt}) → {kind}",
                       symbol=progress.OK if kind == "ready_for_review" else progress.BAD,
                       indent=2)
        if kind in ("ready_for_review", "design_issue"):
            # this dispatch was the rejection's reader, and it has answered
            _consume(rt, worktree, "feedback")
        if kind == "design_issue":
            issues.promote(rt, worktree, verdict.get("di_id"), task_id)
            return
        if kind != "ready_for_review":
            # blocking the task would record a verdict about work that was never done —
            # and with a refused harness every task in the frontier blocks the same way
            dispatch.check_launch(launched)
            # A dispatch that wrote nothing is an agent that mis-stepped, not a task that
            # cannot be built: it spends a redispatch and goes back into the same worktree,
            # where its work still is. This budget is its own — charged against the review
            # budget instead, two mis-steps left a task no fix attempt at all after its
            # first rejection, and it was blocked reporting the review had rejected it
            # every time.
            redispatches += 1
            if redispatches > REDISPATCH_ATTEMPTS:
                _block(rt, task_id, f"the build wrote no artifact to route on in "
                                    f"{redispatches} dispatches")
                return
            rt.cli.mutate("pipeline", "retry-task", task_id,
                          "--reason", f"the build returned {kind}")
            continue
        outcome = _review_chain(rt, worktree, task_id, verdict.get("build_commit_sha"),
                                number)
        if outcome != "rejected":
            return
        rejections += 1
        if rejections >= rt.cfg.max_attempts:
            _block(rt, task_id, "review rejected the build at every allowed attempt")
            return


def _block(rt, task_id, reason) -> None:
    rt.report.line(f"task {task_id} blocked — {reason}", symbol=progress.BAD, indent=2)
    rt.cli.mutate("pipeline", "block-task", task_id, "--reason", reason)


def _review_chain(rt, worktree, task_id, build_sha, number) -> str:
    """Run the configured review passes over one build. Returns the outcome that
    ends the chain: approved, rejected, design_issue or blocked."""
    index = 0
    passes = rt.cfg.review_passes
    for _ in range(len(passes) * rt.cfg.max_attempts + 1):
        if index >= len(passes):
            rt.report.line(f"task {task_id} approved by every review pass",
                           symbol=progress.OK, indent=2)
            rt.cli.mutate("pipeline", "approve-task", task_id, "--commit", build_sha)
            return "approved"
        agent = passes[index]
        rt.cli.mutate("pipeline", "dispatch", "--agent", agent, "--task", task_id,
                      "--attempt", str(_attempt(rt, task_id)), "--pass", str(index))
        launched = rt.agents.launch(
            agent,
            {"mode": "review", "task_id": task_id, "worktree": str(worktree),
             "sprint_branch": rt.state.sprint_branch, "pass": agent},
            cwd=worktree, task_id=task_id, increment=number)
        verdict = _inspect(rt, "inspect-review-return", worktree, task_id, build_sha)
        kind = verdict.get("verdict")
        rt.report.line(f"task {task_id} · {agent} → {kind}",
                       symbol=progress.OK if kind == "approved" else progress.BAD,
                       indent=2)
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
            dispatch.check_launch(launched)
            rt.cli.mutate("pipeline", "block-task", task_id,
                          "--reason", "the review left no readable verdict")
            return "blocked"
        dispatch.check_launch(launched)
        rt.cli.mutate("pipeline", "block-task", task_id,
                      "--reason", f"review returned {kind}")
        return "blocked"
    rt.cli.mutate("pipeline", "block-task", task_id,
                  "--reason", "the review chain did not settle")
    return "blocked"


def _consume(rt, worktree, marker) -> None:
    """Retire a presence marker the dispatch that reads it has now returned.

    The inspectors route on presence alone, so a marker left on disk is inspected
    again as a verdict about work it never saw: a stale `feedback` re-rejects a build
    however the review judged it, until the attempt cap blocks an approved task, and a
    stale `review_ready` passes off a build that produced nothing as ready. The build
    role deletes neither — a routing decision the loop depends on is the loop's to
    make."""
    res = rt.cli.raw("orchestrate", "consume-marker", str(worktree), marker,
                     mutating=True)
    if not res.ok:
        rt.report.line(f"could not retire the {marker} marker in {worktree} — "
                       f"{res.stderr.strip() or 'no reason given'}; the next "
                       f"inspection may read it as a fresh verdict",
                       symbol=progress.BAD, indent=2)


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
    parked a task: a conflicted merge is handed to ``wf-stage-repair``'s merge mode
    while it is still in the tree, and only a repair that cannot resolve it aborts the
    merge and records a design issue for the repair ladder."""
    if not approved:
        return False
    rt.report.line(f"merging {len(approved)} approved task(s) into "
                   f"{rt.state.sprint_branch}: "
                   f"{', '.join(str(t) for t in approved)}", indent=2)
    rt.git.checkout(rt.state.sprint_branch)
    conflicted = False
    for task_id in approved:
        branch = _task_branch(rt, task_id)
        build_sha = rt.cli.read("pipeline", "task-state", task_id).data.get("build_commit")
        result = rt.git.merge(branch, f"{task_id}: merge")
        merge_sha = result.sha if result.ok else _repair_merge(rt, task_id, branch,
                                                              result)
        if merge_sha:
            rt.cli.mutate("pipeline", "complete-task", task_id,
                          "--commit", str(build_sha), "--merge", merge_sha)
            rt.git.worktree_remove(_worktree_of(rt, task_id), branch)
            rt.tele.event("merge", task=task_id, sprint=rt.state.sprint_id,
                          merge_commit=merge_sha,
                          conflict_repaired=(not result.ok) or None)
            rt.report.line(f"{task_id} merged"
                           + (" (conflict repaired)" if not result.ok else ""),
                           symbol=progress.OK, indent=3)
            continue
        conflicted = True
        files = ", ".join(result.files) or "unknown paths"
        rt.report.line(f"{task_id} conflicted in {files} — merge left unapplied",
                       symbol=progress.BAD, indent=3)
        issues.record(rt, f"merging {branch} into {rt.state.sprint_branch} conflicted "
                          f"in {files} — two parallel tasks changed the same lines and "
                          f"the merge repair could not resolve them; the merge was "
                          f"left unapplied",
                      task_id=task_id, severity="high")
        rt.tele.event("merge_conflict", task=task_id, sprint=rt.state.sprint_id,
                      files=files)
    return conflicted


def _repair_merge(rt, task_id, branch, result) -> str:
    """Hand the conflicted merge — still in the tree — to ``wf-stage-repair``'s merge
    mode. Returns the merge commit when the repair finished it, and '' when it did not:
    the merge is then aborted so the rest of the batch can still land.

    Completion is proven by the merge itself: no MERGE_HEAD left, and the task branch is
    an ancestor of the sprint branch. Whether the tree is otherwise clean says nothing
    about the merge — and it is never clean, because the repair role's own last action is
    a row appended to the committed telemetry sink."""
    launched = rt.agents.launch(
        "wf-stage-repair",
        {"mode": "merge", "sprint_branch": rt.state.sprint_branch,
         "task_id": task_id, "task_branch": branch,
         "conflicting_paths": ", ".join(result.files) or "unknown paths"},
        task_id=task_id, mode="merge")
    resolved = (launched.exit_code == 0
                and not rt.git.merge_in_progress()
                and rt.git.is_merged_into(branch, rt.state.sprint_branch))
    if resolved:
        return rt.git.head_sha()
    # No check_launch here, deliberately: this is the one site where a non-zero exit is
    # already load-bearing — it IS the "did not resolve" signal, and the merge is aborted
    # and handed to the repair ladder either way.
    rt.git.merge_abort()
    return ""


def _worktree_of(rt, task_id) -> Path:
    return rt.worktrees.get(task_id) or (
        rt.cfg.worktree_base / f"{rt.state.sprint_id}-{task_id}")


def boundary(rt, number) -> str:
    """The increment boundary: heavy checks, design-issue repair, and the stop
    signals a running sprint honours. Returns 'done' or 'rework'."""
    rt.report.phase(f"increment {number} boundary")
    _blocked_gate(rt, number)
    command = rt.cfg.command("stage_check")
    if command:
        attempts = 0
        timeout = int(rt.cfg.driver("command_timeout_s"))
        while True:
            log_path = (rt.cfg.path("transient")
                        / f"stage-check-{rt.state.sprint_id}-{number}.log")
            # Its output goes to the log, so — like a dispatch — the step line and its
            # heartbeat are the only sign the gate is running rather than wedged.
            with rt.report.step(f"stage check · {command}", budget_s=timeout) as step:
                done = procs.run(command, timeout=timeout,
                                 cwd=rt.cfg.root, shell=True, stdout_path=log_path)
                step.ok = done.rc == 0
                step.note = "" if step.ok else f"rc={done.rc} · log {log_path}"
            rt.tele.event("stage_check", increment=number, rc=done.rc,
                          duration_s=done.duration_s, started_at=done.started_at,
                          ended_at=done.ended_at, sprint=rt.state.sprint_id)
            if done.rc == 0:
                break
            attempts += 1
            if attempts > STAGE_REPAIR_ATTEMPTS:
                raise Halt("stage_check_red",
                           f"increment {number}'s heavy checks stayed red after "
                           f"{STAGE_REPAIR_ATTEMPTS} repair rounds")
            rt.report.line(f"stage check red — repair round "
                           f"{attempts}/{STAGE_REPAIR_ATTEMPTS}", indent=1)
            # a refused launch would otherwise burn a repair round on nothing, three
            # times over, and end at a stage_check_red halt blaming the checks
            dispatch.check_launch(rt.agents.launch(
                "wf-stage-repair",
                {"mode": "repair", "sprint_branch": rt.state.sprint_branch,
                 "Increment": number, "Checkpoint": checkpoint(rt, number)},
                increment=number, mode="repair"))
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
                      sprint=rt.state.sprint_id)
        rt.report.line(f"stop pending ({stop.reason}) — this sprint ships, then the "
                       f"loop exits: {stop.detail}", indent=1)
    return "done"


def _blocked_gate(rt, number) -> None:
    """Stop on a task that never landed. A blocked task cannot be reopened inside the
    sprint, and ``propagate-blocks`` dooms everything depending on it — which is why the
    sub-layers after one close empty in a second. Nothing further down the loop reads
    that state, so without this the increment closes, the sprint ships, and the PR is
    missing the work with nothing saying so. Ahead of the heavy checks: a stage gate over
    a tree that is missing part of the increment proves nothing and costs an hour."""
    tasks = [t for t in (rt.cli.read("pipeline", "blocked-tasks").data or {}).get("tasks")
             or [] if isinstance(t, dict)]
    if not tasks:
        return
    named = "; ".join(
        f"{t.get('task_id')} ({t.get('reason') or 'blocked by ' + str(t.get('blocked_by'))})"
        for t in tasks)
    roots = [str(t.get("task_id")) for t in tasks if not t.get("blocked_by")]
    raise Halt("tasks_blocked",
               f"increment {number} cannot close — {len(tasks)} task(s) never landed and "
               f"every task depending on them was blocked with them: {named}. Their "
               f"worktrees still hold whatever was built; once the cause is fixed, "
               f"`wf pipeline unblock-task <id>` reopens a task and everything doomed "
               f"with it — the roots here are "
               f"{', '.join(roots) or 'in the list above'}")


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
