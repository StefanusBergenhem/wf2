"""One stage — the tasks with no dependency between them, run in one batch.

The design role cut this stage against the merged tree and wrote its contracts in the
same sitting, so there is nothing to author here: load the task list, dispatch the
whole frontier into parallel worktrees, run each task's build→review chain, batch-merge
what was approved, and close.

A task that blocks dooms nothing. There are no edges inside a stage, so the stage closes
with what merged and the blocked work re-enters at the next cut through the design issue
the block raises.

Every routing decision here comes from a `wf` verb's JSON or from an artifact on disk.
Nothing routes on what an agent said.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import config  # noqa: F401 — importing it puts the CLI package on sys.path
import dispatch
import drillcache
import issues
import phases
import procs
import progress
import stoprules
import yaml
from runtime import Halt

# Bounds on the loops that are driven by external state. A well-behaved run never
# reaches them; hitting one means the pipeline is not converging and a human must look.
MAX_FRONTIER_ITERATIONS = 400
# How many stage-repair rounds one stage close gets. Its own budget:
# `review.max_attempts` bounds one task's build→review chain and nothing else.
STAGE_REPAIR_ATTEMPTS = 3
# How many times a build that left no artifact at all is sent back in. Its own budget,
# for the same reason: a dispatch that produced nothing to judge is a different failure
# from a build the review judged and rejected, and must not spend that one's fix cycles.
REDISPATCH_ATTEMPTS = 2


# ── the stage ────────────────────────────────────────────────────────────────


def run_stage(rt) -> None:
    """Load the cut, run it, close it. Ends in ``designing`` (cut the next stage) or
    ``closeout`` (the PR is ready to ship)."""
    rt.state.enter("stage_run")
    if not rt.dry_run and not rt.cfg.path("stage").exists():
        # The close archives and deletes the artifact, so a run interrupted between that
        # and the phase write comes back here with nothing to load. Its work is merged;
        # the next cut is what is missing.
        rt.report.line("no stage on disk — the last cut already closed; cutting the next",
                       indent=1)
        rt.state.enter("designing")
        return
    res = rt.cli.mutate("pipeline", "load-stage")
    if not res.ok:
        raise Halt("load_stage", res.stderr.strip() or str(res.data))
    stage = res.data.get("stage") or rt.state.stage
    tasks = res.data.get("tasks") or []
    rt.state.stage = stage
    rt.state.save()
    rt.report.phase(f"stage {stage} · {len(tasks)} task(s): "
                    f"{', '.join(str(t) for t in tasks) or 'none'}")
    rt.tele.event("stage_start", sprint=rt.state.sprint_id, stage=stage,
                  width=len(tasks))
    rt.cli.mutate("pipeline", "transition", "--to", "running_stage",
                  "--reason", f"stage {stage}")
    merged = frontier_loop(rt, stage)
    close(rt, stage, tasks, merged)


def frontier_loop(rt, stage) -> list:
    """Dispatch the stage's frontier until nothing is pending or in flight, then merge
    the approved set. One pass: a stage's tasks are independent, so there is no second
    layer to advance into. Returns the task ids that merged."""
    merged, started = [], False
    for _ in range(MAX_FRONTIER_ITERATIONS):
        res = rt.cli.read("pipeline", "next")
        if not res.ok:
            raise Halt("pipeline_next", res.stderr.strip() or "the frontier is unreadable")
        data = res.data
        terminal = data.get("terminal") or {}
        if terminal.get("halt"):
            raise Halt("pipeline_halt", str((terminal["halt"] or {}).get("reason")))
        if not started:
            rt.cli.mutate("pipeline", "stage-start")
            started = True
        if not terminal.get("stage_done"):
            _run_frontier(rt, data)
            continue
        approved = data.get("approved") or []
        landed = _merge_batch(rt, approved)
        merged += [t for t in landed if t not in merged]
        if len(landed) < len(approved):
            continue  # a conflict parked a task: let the frontier re-settle first
        rt.cli.mutate("pipeline", "stage-summary")
        rt.cli.mutate("pipeline", "stage-end")
        if rt.cfg.history_cap:
            rt.cli.mutate("pipeline", "archive-history", "--cap", str(rt.cfg.history_cap))
        return merged
    raise Halt("frontier_loop", f"stage {stage} did not settle")


def _run_frontier(rt, data) -> None:
    entries = data.get("dispatch") or []
    if not entries:
        raise Halt("stalled_frontier",
                   "the stage has pending work but nothing dispatchable")
    workers = max(1, min(int(rt.cfg.driver("max_parallel")), len(entries)))
    rt.report.line(
        f"dispatching {len(entries)} task(s) "
        f"({', '.join(str(e.get('task_id')) for e in entries)}) "
        f"· {workers} in parallel", indent=2)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_run_task, rt, entry) for entry in entries]
        for future in futures:
            future.result()


def _run_task(rt, entry) -> None:
    task_id = entry["task_id"]
    worktree = (rt.cfg.root / entry["worktree"]).resolve()
    rt.worktrees[task_id] = worktree
    prior = _open_worktree(rt, task_id, worktree)
    contract = worktree / rt.cfg.rel("current_task")
    args = ["stage", "task", task_id, "--write", str(contract)]
    if prior:
        args += ["--prior-attempt", prior]
    written = rt.cli.mutate(*args)
    if not written.ok:
        rt.report.line(f"task {task_id} blocked — no build envelope could be written",
                       symbol=progress.BAD, indent=2)
        _block(rt, task_id, "the build envelope could not be written")
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
            cwd=worktree, task_id=task_id, stage=rt.state.stage)
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
        outcome = _review_chain(rt, worktree, task_id, verdict.get("build_commit_sha"))
        if outcome != "rejected":
            return
        rejections += 1
        if rejections >= rt.cfg.max_attempts:
            _block(rt, task_id, "review rejected the build at every allowed attempt")
            return


def _open_worktree(rt, task_id, worktree):
    """Give the task its worktree, carrying an earlier blocked attempt's commits into it
    when this task is that attempt's successor. Returns the ``prior_attempt`` line for
    the build envelope, or '' when nothing was carried.

    Without that line the build's Red phase breaks silently: its rule is *confirm the
    tests FAIL*, and code the previous attempt already wrote can make a new test pass,
    which the agent then misdiagnoses as a vacuous test and restructures a good one."""
    branch = _task_branch(rt, task_id)
    found = issues.salvage(rt, task_id)
    if found:
        old_branch, reason = found
        if rt.git.worktree_from(worktree, branch, old_branch, rt.state.sprint_branch):
            rt.report.line(f"task {task_id} · carried {old_branch} forward and rebased "
                           f"onto {rt.state.sprint_branch}", indent=2)
            return f"{old_branch} — {reason}"
        rt.report.line(f"task {task_id} · could not carry {old_branch} forward — "
                       f"starting from the sprint tip", indent=2)
    rt.git.worktree_add(worktree, branch, rt.state.sprint_branch)
    return ""


def _block(rt, task_id, reason) -> None:
    """Block one task and open its channel back into the next cut. The block is terminal
    for this task alone — nothing depends on it — so the design issue is what carries the
    work forward: the role reads it when it grounds, and its resolution names the
    successor task the driver then cuts a worktree for from this branch."""
    branch = _task_branch(rt, task_id)
    rt.report.line(f"task {task_id} blocked — {reason}", symbol=progress.BAD, indent=2)
    issues.record(rt, f"task {task_id} blocked — {reason}. Its attempts are on branch "
                      f"{branch}; author the task that answers this and name it here, "
                      f"and the driver carries that branch into its worktree",
                  task_id=task_id, severity="high")
    rt.cli.mutate("pipeline", "block-task", task_id, "--reason", reason)
    rt.tele.event("task_blocked", task=task_id, sprint=rt.state.sprint_id,
                  stage=rt.state.stage, branch=branch, reason=reason)


def _review_chain(rt, worktree, task_id, build_sha) -> str:
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
            cwd=worktree, task_id=task_id, stage=rt.state.stage)
        verdict = _inspect(rt, "inspect-review-return", worktree, task_id, build_sha)
        kind = verdict.get("verdict")
        rt.report.line(f"task {task_id} · {agent} → {kind}",
                       symbol=progress.OK if kind == "approved" else progress.BAD,
                       indent=2)
        if kind == "approved":
            index += 1
            continue
        if kind == "redispatch_same_attempt":
            # An untouched worktree is what a review that mis-stepped leaves — and it is
            # byte-identical to what a harness that never ran the review leaves. Only the
            # exit code separates them, and without asking, a refused harness spends the
            # whole chain in seconds and blocks the task for "not settling".
            dispatch.check_launch(launched)
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
            _block(rt, task_id, "the review left no readable verdict")
            return "blocked"
        dispatch.check_launch(launched)
        _block(rt, task_id, f"review returned {kind}")
        return "blocked"
    _block(rt, task_id, "the review chain did not settle")
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


# ── merges ───────────────────────────────────────────────────────────────────


def _merge_batch(rt, approved) -> list:
    """Merge the approved set into the sprint branch. Returns the task ids that landed;
    a conflicted merge is handed to ``wf-stage-repair``'s merge mode while it is still in
    the tree, and only a repair that cannot resolve it aborts the merge and records a
    design issue for the next cut."""
    if not approved:
        return []
    rt.report.line(f"merging {len(approved)} approved task(s) into "
                   f"{rt.state.sprint_branch}: "
                   f"{', '.join(str(t) for t in approved)}", indent=2)
    rt.git.checkout(rt.state.sprint_branch)
    landed = []
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
                          stage=rt.state.stage, merge_commit=merge_sha,
                          conflict_repaired=(not result.ok) or None)
            rt.report.line(f"{task_id} merged"
                           + (" (conflict repaired)" if not result.ok else ""),
                           symbol=progress.OK, indent=3)
            landed.append(task_id)
            continue
        files = ", ".join(result.files) or "unknown paths"
        rt.report.line(f"{task_id} conflicted in {files} — merge left unapplied",
                       symbol=progress.BAD, indent=3)
        issues.record(rt, f"merging {branch} into {rt.state.sprint_branch} conflicted "
                          f"in {files} — two parallel tasks changed the same lines and "
                          f"the merge repair could not resolve them; the merge was "
                          f"left unapplied",
                      task_id=task_id, severity="high")
        rt.tele.event("merge_conflict", task=task_id, sprint=rt.state.sprint_id,
                      stage=rt.state.stage, files=files)
    return landed


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
        task_id=task_id, mode="merge", stage=rt.state.stage)
    resolved = (launched.exit_code == 0
                and not rt.git.merge_in_progress()
                and rt.git.is_merged_into(branch, rt.state.sprint_branch))
    if resolved:
        return rt.git.head_sha()
    # No check_launch here, deliberately: this is the one site where a non-zero exit is
    # already load-bearing — it IS the "did not resolve" signal, and the merge is aborted
    # and handed to the next cut either way.
    rt.git.merge_abort()
    return ""


def _worktree_of(rt, task_id) -> Path:
    return rt.worktrees.get(task_id) or (
        rt.cfg.worktree_base / f"{rt.state.sprint_id}-{task_id}")


# ── the stage close ──────────────────────────────────────────────────────────


def close(rt, stage, tasks, merged) -> None:
    """Everything that happens once a stage's work is on the sprint branch: the heavy
    checks over the integrated tree, the PR-body block, the completion gate, the drill
    sweep — and then the one decision that ends a sprint, since there is no increment
    list left to exhaust."""
    rt.report.phase(f"stage {stage} close")
    doc = stage_doc(rt)
    _heavy_checks(rt, stage, checkpoint(doc))
    rt.cli.mutate("pipeline", "append-pr-body")
    _archive_stage(rt, stage)
    # Counted here, before anything else at the close can pause: past this point the
    # stage is merged and out of the working set, and a resume finds nothing to reload —
    # so a stage not counted now would never be counted at all, and the PR cap would
    # silently stretch.
    rt.state.stages_shipped += 1
    rt.state.save()

    phases.capability_gate(rt)
    drillcache.prune(rt)
    landed_e2e = _landed_e2e(doc, merged)
    rt.tele.event("stage_done", sprint=rt.state.sprint_id, stage=stage,
                  width=len(tasks), merged=len(merged), e2e=landed_e2e or None)

    stop = stoprules.at_boundary(rt.cfg, rt.git)
    if stop:
        rt.state.stop_pending = stop.reason
        rt.state.save()
        rt.tele.event("stop_pending", reason=stop.reason, detail=stop.detail,
                      sprint=rt.state.sprint_id)
        rt.report.line(f"stop pending ({stop.reason}) — this sprint ships, then the "
                       f"loop exits: {stop.detail}", indent=1)
    _ship_or_cut(rt, landed_e2e)


def _ship_or_cut(rt, landed_e2e: bool) -> None:
    """The PR boundary is a fact about the system: ship when the merged tree crosses an
    end-to-end checkpoint, or at ``driver.max_stages_per_sprint`` when a hardening
    stretch never lands one. Otherwise cut the next stage against what just merged."""
    cap = int(rt.cfg.driver("max_stages_per_sprint"))
    if landed_e2e:
        reason = "the stage landed an end-to-end scenario"
    elif rt.state.stages_shipped >= cap:
        reason = f"{rt.state.stages_shipped} stage(s) merged (cap {cap})"
    elif rt.state.stop_pending:
        reason = f"a stop is pending ({rt.state.stop_pending})"
    else:
        rt.report.line(f"{rt.state.stages_shipped}/{cap} stage(s) in the PR, none "
                       f"end-to-end yet — cutting the next stage", indent=1)
        rt.state.enter("designing")
        return
    rt.report.line(f"shipping the PR — {reason}", symbol=progress.OK, indent=1)
    rt.state.enter("closeout")


def _heavy_checks(rt, stage, observable) -> None:
    """``commands.stage_check`` over the integrated tree, at EVERY stage close. The
    stage has already merged when it runs, so red means the sprint branch is broken:
    repair, then halt naming the log — reverting a branch that may already be pushed is
    worse machinery than a stop."""
    command = rt.cfg.command("stage_check")
    if not command:
        return
    attempts = 0
    timeout = int(rt.cfg.driver("command_timeout_s"))
    log_path = (rt.cfg.path("transient")
                / f"stage-check-{rt.state.sprint_id}-{stage}.log")
    while True:
        # Its output goes to the log, so — like a dispatch — the step line and its
        # heartbeat are the only sign the gate is running rather than wedged.
        with rt.report.step(f"stage check · {command}", budget_s=timeout) as step:
            done = procs.run(command, timeout=timeout,
                             cwd=rt.cfg.root, shell=True, stdout_path=log_path)
            step.ok = done.rc == 0
            step.note = "" if step.ok else f"rc={done.rc} · log {log_path}"
        rt.tele.event("stage_check", stage=stage, rc=done.rc,
                      duration_s=done.duration_s, started_at=done.started_at,
                      ended_at=done.ended_at, sprint=rt.state.sprint_id)
        if done.rc == 0:
            return
        attempts += 1
        if attempts > STAGE_REPAIR_ATTEMPTS:
            raise Halt("stage_check_red",
                       f"stage {stage}'s heavy checks stayed red after "
                       f"{STAGE_REPAIR_ATTEMPTS} repair rounds — the sprint branch is "
                       f"broken and it has already merged; the run is in {log_path}")
        rt.report.line(f"stage check red — repair round "
                       f"{attempts}/{STAGE_REPAIR_ATTEMPTS}", indent=1)
        # a refused launch would otherwise burn a repair round on nothing, three
        # times over, and end at a stage_check_red halt blaming the checks
        dispatch.check_launch(rt.agents.launch(
            "wf-stage-repair",
            {"mode": "repair", "sprint_branch": rt.state.sprint_branch,
             "Stage": stage, "Checkpoint": observable},
            stage=stage, mode="repair"))


def _archive_stage(rt, stage) -> None:
    """The stage artifact leaves the working set at its own merge: a copy into
    ``paths.archive`` for the maintainer, then gone. Nothing accumulates, and the next
    cut starts from an empty disk — which is also what makes "no stage written" a
    trustworthy work-exhaustion signal."""
    path = rt.cfg.path_opt("stage")
    if not path or not path.exists() or rt.dry_run:
        return
    rt.cli.raw("archive", "add", str(path), "--label", f"stage-{stage}",
               "--config", rt.cfg.config_path, mutating=True)
    path.unlink()


def stage_doc(rt) -> dict:
    """The stage artifact as YAML, or {} — read before the close archives it."""
    path = rt.cfg.path_opt("stage")
    if not path or not path.exists():
        return {}
    try:
        doc = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError:
        return {}
    return doc if isinstance(doc, dict) else {}


def checkpoint(doc) -> str:
    """The stage's observable checkpoint — what must demonstrably work now. It is a
    YAML key, so this is one read; a role that authored several observable facts gets
    them joined."""
    value = doc.get("checkpoint")
    if isinstance(value, (list, tuple)):
        return "; ".join(str(v).strip() for v in value if str(v).strip())
    return str(value or "").strip()


def _landed_e2e(doc, merged) -> bool:
    """True when a task carrying system tests actually merged. That — not the cut's
    intent — is the end-to-end fact the PR boundary follows."""
    landed = set(merged)
    return any(t.get("system_tests") and t.get("id") in landed
               for t in (doc.get("tasks") or []) if isinstance(t, dict))
