"""The per-sprint phase machine.

``sprint_start`` (branch off the stack tip, refresh the read-view) → ``designing``
(the design role cuts one stage — its design and its task contracts — and the stage
gate decides whether it is buildable) → ``stage_run`` (stages.py) → back to
``designing`` for the next cut, or on to ``closeout`` (retro, ship). Each phase
writes its position to the driver state before it acts, so a restarted driver
re-enters where it left off.
"""
from __future__ import annotations

import datetime
import re

import adequacy
import config  # noqa: F401 — importing it puts the CLI package on sys.path
import dispatch
import gitops
import issues
import mdread
import procs
import progress
import stoprules
import yaml
from runtime import Halt, Pause

_BRIEF_DI_RE = re.compile(r"^\s*di_id:\s*([A-Za-z][\w-]*)", re.MULTILINE)

# How many times a red stage is sent back to the design role before the run halts.
# The build→review budget (`review.max_attempts`) bounds one task's chain, not this.
STAGE_GATE_ATTEMPTS = 3
SHIP_STEP = "ship"


# ── sprint_start ─────────────────────────────────────────────────────────────


def sprint_start(rt, resume: bool = False) -> None:
    if resume and rt.state.sprint_id:
        sprint_id, branch = rt.state.sprint_id, rt.state.sprint_branch
    else:
        sprint_id = rt.git.next_sprint_id()
        branch = f"{gitops.SPRINT_PREFIX}{sprint_id}"
    _clean_tree_gate(rt)
    base = rt.git.stack_tip()
    # The branch is cut BEFORE the position is recorded. The reverse order leaves a
    # window where the state file names a branch that git never got, and a resume then
    # re-enters a phase past sprint_start and builds the whole sprint on whatever HEAD
    # happens to be. Failing before the write costs only a sprint ordinal.
    rt.git.start_branch(branch, base)
    if not (resume and rt.state.sprint_id):
        rt.state.start_sprint(sprint_id, branch)
    rt.state.save()
    _carry_telemetry(rt, sprint_id)
    _reset_pr_body(rt)
    rt.tele.event("sprint_start", sprint=sprint_id, branch=branch, base=base)
    rt.report.phase(f"sprint {sprint_id} · branch {branch} · base {base}")

    resume_hygiene(rt)
    rt.cli.mutate("pipeline", "transition", "--to", "preparing",
                  "--reason", f"driver sprint {sprint_id}", "--sprint-id", sprint_id)

    launched = rt.agents.launch("wf-discover", {}, mode="refresh")
    brief = rt.cfg.path_opt("discover_brief")
    if not rt.dry_run and (not brief or not brief.exists()):
        dispatch.check_launch(launched)
        raise Halt("no_discover_brief",
                   f"wf-discover left no brief at {brief} — the design role cannot "
                   f"ground without it")
    rt.state.enter("designing")


def _reset_pr_body(rt) -> None:
    """Empty the PR-body accumulator. Each stage appends its own block to it as it
    merges, so a sprint that started on the last one's leftovers would ship a PR
    describing work that is already merged and gone."""
    path = rt.cfg.path_opt("pr_body")
    if path and path.exists() and not rt.dry_run:
        path.unlink()


def resume_hygiene(rt) -> None:
    """Drop handoffs whose consumer can never return, and free the slots an interrupted
    run left occupied. Runs on EVERY re-entry, not only the one through sprint_start: an
    interruption inside a stage is precisely where a task is left holding a slot no one
    will ever release, and that re-entry never passes through sprint_start."""
    rt.report.line("resume hygiene — sweeping transients, reclaiming stale slots",
                   indent=1)
    rt.cli.raw("orchestrate", "sweep-transients", "--config", rt.cfg.config_path,
               mutating=True)
    rt.cli.mutate("pipeline", "reclaim-stale")


def verify_position(rt) -> None:
    """A resumed run must stand where its state file says it does. Re-entering a phase
    past sprint_start cuts no branch, so a recorded branch git does not have is a
    fiction — and every commit, worktree and merge would land on whatever HEAD happens
    to be instead. Halts with the two ways out rather than guessing which was meant."""
    branch = rt.state.sprint_branch
    if not branch or rt.dry_run:
        return
    if not rt.git.branch_exists(branch):
        raise Halt("sprint_branch_missing",
                   f"the state file resumes sprint {rt.state.sprint_id} on {branch}, "
                   f"but git has no such branch — nothing this run built would land on "
                   f"it. Either re-create it (git checkout -b {branch} <base>) to carry "
                   f"on, or delete {rt.cfg.state_file} to start a fresh sprint")
    if rt.git.current_branch() != branch:
        rt.report.line(f"checking out {branch} — HEAD was on "
                       f"{rt.git.current_branch()}", indent=1)
        rt.git.checkout(branch)


def _clean_tree_gate(rt) -> None:
    """A sprint branch is cut from a clean tree. Runs BEFORE the branch is cut, so a
    tree carrying real work halts without leaving a stray sprint branch behind (a
    branch that exists burns its ordinal — the next run mints the one after it).

    The one exception is the telemetry sink: it is committed, and every role and Stop
    hook appends to it after the sprint's last commit, so it is dirty almost always.
    Those rows are carried onto the NEW branch by ``_carry_telemetry`` once it exists."""
    dirty = rt.git.dirty_paths()
    telemetry = rt.cfg.rel("telemetry")
    if not dirty or (telemetry and set(dirty) == {telemetry}):
        return
    raise Halt("dirty_tree",
               "the working tree carries uncommitted changes — a sprint branch must "
               f"be cut from a clean tree: {', '.join(sorted(dirty))}")


def _carry_telemetry(rt, sprint_id) -> None:
    """Commit the rows the last sprint's close left in the telemetry sink — AFTER the
    new branch is cut and checked out, so they land on it. HEAD is still on the previous
    sprint's branch until ``start_branch`` runs; committing there adds a commit to a
    branch that is already pushed and merging, which stops it registering as merged,
    strands it on the stack, and points the next sprint's PR at a base that is gone."""
    telemetry = rt.cfg.rel("telemetry")
    if telemetry and telemetry in rt.git.dirty_paths():
        rt.git.commit_paths([telemetry], f"telemetry: carry rows into sprint {sprint_id}")


# ── designing ────────────────────────────────────────────────────────────────


# A design dispatch that writes no stage is not automatically the end of the work: taking
# up a capability that has no scenario set yet is a job of its own, and the role ends that
# dispatch deliberately. Bounded so a role that only ever authors cannot spin forever.
SCENARIO_ROUNDS = 3


def designing(rt) -> dict:
    """Cut the next stage against the merged tree. The design role writes the whole
    artifact — its design header and that stage's task contracts — in one sitting, and
    ``wf stage check`` is the one gate between it and the build."""
    rt.state.enter("designing")
    rt.report.phase("designing — the design role cuts the next stage")
    return cut_stage(rt)


def cut_stage(rt, launched=None, before=None) -> dict:
    """Dispatch the design role until it produces a stage, then gate what it wrote.

    ``launched``/``before`` carry a dispatch the caller already made — the ruling resume
    — so its round is classified here rather than routed on its own. Every path into the
    gate runs these rounds: a resume that reached the gate directly read a dispatch spent
    on a scenario set as the end of the work and stopped a run with its whole work-set
    still open."""
    pending = launched is not None
    for _ in range(SCENARIO_ROUNDS):
        if not pending:
            before = _scenario_count(rt)
            launched = _design_dispatch(rt)
            if rt.dry_run:
                raise Pause("dry_run",
                            "planned dispatches printed; nothing was launched")
        pending = False
        if rt.cfg.path("stage").exists() or _scenario_count(rt) <= before:
            return stage_gate(rt, launched)
        rt.report.line("the role authored a scenario set instead of cutting — "
                       "dispatching it again to cut the stage", indent=1)
    return stage_gate(rt, launched)


def _scenario_count(rt) -> int:
    """How many SYS-TC scenarios the work-set carries, over both files. The one
    mechanical signal that separates "authored a capability's scenario set and stopped"
    from "nothing is in scope" — both leave no stage on disk, and routing the first as
    work exhaustion halts a loop that has work left to do."""
    result = rt.cli.read("workset", "check")
    return int((result.data or {}).get("scenarios") or 0)


def _design_dispatch(rt):
    """One design dispatch: the role has a single mode, so nothing is passed to select
    one. Escalation is checked immediately after, and every host issue the cut resolved
    has its run-state twin closed."""
    launched = rt.agents.launch("wf-designer", {}, stage=rt.state.stage)
    _escalation_gate(rt)
    issues.close_resolved(rt)
    return launched


def resume_ruling(rt) -> dict:
    """Re-enter a run that halted for a human ruling. The design role's resume mode
    consumes the ruling; an unruled brief keeps the run paused. The cut then carries on
    through ``cut_stage``'s rounds like any other — the ruling round is one design
    dispatch, not the whole cut.

    The phase moves off ``awaiting_ruling`` as soon as the ruling is consumed. Leaving it
    there wedges the run: the brief the phase names is gone, so every restart resumes into
    a ruling that no longer exists, launches nothing, and exits."""
    decision_prep = rt.cfg.path_opt("decision_prep")
    launched, before = None, None
    if decision_prep and decision_prep.exists():
        rt.report.phase(f"resuming a halted run · ruling brief {decision_prep}")
        if not ruling_present(decision_prep):
            rt.tele.event("stop", reason="escalation", sprint=rt.state.sprint_id,
                          detail="the ruling section is still empty")
            raise Pause("escalation", f"{decision_prep} carries no ruling yet")
        di_id = brief_di_id(decision_prep)
        before = _scenario_count(rt)
        launched = rt.agents.launch("wf-designer", {"Mode": "resume"}, mode="resume",
                                    stage=rt.state.stage)
        _escalation_gate(rt)
        _close_resolved_twins(rt, di_id)
    else:
        rt.report.phase("resuming a halted run · its ruling is already consumed")
    rt.state.resume_phase = None
    rt.state.enter("designing")
    return cut_stage(rt, launched, before)


def ruling_present(path) -> bool:
    """True when the brief's `## Ruling` section holds prose — comments and blank
    lines are not a ruling."""
    text = path.read_text()
    body = mdread.section(text, "Ruling")
    return bool(_prose(body))


def brief_di_id(path) -> str:
    """The design issue the brief says the halt interrupted, or '' when it names none."""
    found = _BRIEF_DI_RE.search(path.read_text())
    return found.group(1) if found else ""


def _close_resolved_twins(rt, di_id: str) -> None:
    """Close the run-state twin of every design issue the resume run finished. The
    brief's own issue closes on the ruling; any other twin closes once the host file
    says it is resolved."""
    issues.close_resolved(rt)
    if di_id:
        rt.cli.mutate("pipeline", "resolve-design-issue", di_id)


def _prose(body: str) -> str:
    """A markdown section's body with html comments stripped — a comment is not prose."""
    return re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL).strip()


def stage_gate(rt, launched=None) -> dict:
    """The one mechanical gate between design and build: a stage on disk that passes
    ``wf stage check`` — the design header the build envelope carries and that stage's
    task contracts, checked together because one role wrote them in one sitting. A red
    gate records the findings as a design issue and sends the role back in."""
    if not rt.cfg.path("stage").exists():
        return _work_exhausted(rt, launched)

    findings = ""
    for attempt in range(1, STAGE_GATE_ATTEMPTS + 1):
        # materialize first: it inlines the scenario text an e2e task's contract carries,
        # and its own errors (an id no capability carries) are gate findings too.
        materialized = rt.cli.mutate("stage", "materialize")
        check = rt.cli.read("stage", "check")
        if materialized.ok and check.ok:
            served = ", ".join(str(s) for s in check.data.get("serves") or []) or "—"
            rt.state.stage = check.data.get("stage")
            rt.report.line(f"stage check green · stage {rt.state.stage} · "
                           f"serves {served}", symbol=progress.OK, indent=1)
            rt.state.enter("stage_run")
            return check.data
        findings = "; ".join(gate_findings(r.data) for r in (materialized, check)
                             if not r.ok)
        rt.report.line(f"stage check red ({attempt}/{STAGE_GATE_ATTEMPTS}) — "
                       f"back to the design role: {findings}",
                       symbol=progress.BAD, indent=1)
        issues.record(rt, f"`wf stage check` is red — {findings}")
        _design_dispatch(rt)
    raise Halt("stage_gate_red",
               f"the cut never passed `wf stage check` in {STAGE_GATE_ATTEMPTS} "
               f"attempts: {findings}")


def gate_findings(data) -> str:
    """The findings a gate's JSON carries, as one line — what a halt or a design issue
    has to name for its reader to act on it."""
    errors = (data or {}).get("errors") or []
    lines = [f"{e.get('code')}: {e.get('msg')}" if isinstance(e, dict) else str(e)
             for e in errors]
    return "; ".join(lines) or "no findings emitted"


def _work_exhausted(rt, launched):
    """No stage on disk. That means "nothing is in scope" only if the role actually ran
    and decided so — a refused launch leaves the same empty disk for the opposite
    reason. Work merged into the PR in flight ships before the loop exits; stranding it
    on a branch nobody is looking at is the one outcome worse than stopping."""
    dispatch.check_launch(launched)
    if launched is None and not rt.dry_run:
        raise Halt("no_design_dispatch",
                   "the cut reached its no-stage ending with no design dispatch behind "
                   "it — nothing ran, so nothing decided that no work is in scope")
    reason, detail = _no_stage_verdict(rt, launched)
    if rt.state.stages_shipped:
        rt.report.line(f"{detail}; shipping the {rt.state.stages_shipped} stage(s) "
                       f"already merged", indent=1)
        rt.state.stop_pending = reason
        rt.state.enter("closeout")
        return {}
    raise Pause(reason, detail)


def _no_stage_verdict(rt, launched):
    """Which of the two no-stage endings this is, as (reason, detail). Work exhaustion is
    a claim about the WORK-SET, so it is read from the work-set — an empty disk on its own
    says only that this cut produced nothing, and a stop that asserts a drained backlog
    while capabilities sit open sends its reader looking in the wrong place."""
    work = stoprules.open_work(rt.cfg)
    caps, learnings = work["capabilities"], work["learnings"]
    if not caps and not learnings:
        return ("work_exhaustion",
                "the design role cut no stage — nothing open and unparked is in scope")
    log = getattr(launched, "log_path", None)
    return ("no_stage_cut",
            f"the design role cut no stage, but {len(caps)} capability(s) and "
            f"{len(learnings)} learning(s) are still open and unparked"
            + (f" — its log says why: {log}" if log else ""))


def _escalation_gate(rt) -> None:
    decision_prep = rt.cfg.path_opt("decision_prep")
    if decision_prep and decision_prep.exists():
        rt.state.suspend("awaiting_ruling")
        rt.tele.event("stop", reason="escalation", sprint=rt.state.sprint_id,
                      detail=str(decision_prep))
        raise Pause("escalation", f"a ruling is pending in {decision_prep}")


# ── the completion gate, at every stage close ────────────────────────────────


def capability_gate(rt) -> list:
    """Adequacy gate 2, fired per capability rather than per sprint: set-difference
    each work-set entry's scenario set against the shipped ``[SYS-TC:]`` tags, and
    review whatever came out whole. Pure mechanism — the driver decides nothing here,
    it only asks the question when the evidence says it is answerable."""
    res = rt.cli.read("pipeline", "capability-complete")
    # Capabilities only. A learning MAY carry a scenario set, and the verb reports its
    # shipped-ness the same way — but that set never gates a learning's drain: its proof
    # is its tasks' own criteria plus the merge record, and it drains at sprint close off
    # that. Sent down this path it spends an adequacy dispatch and then `drain-capability`
    # fails twice over — the digest heading matches `CAP-\d+`, and the drop targets the
    # capabilities file, where no L- id lives — with each failure reading as one more
    # inadequate verdict against an entry nothing is wrong with.
    complete = [str(e.get("id")) if isinstance(e, dict) else str(e)
                for e in (res.data.get("complete") or [])
                if not isinstance(e, dict) or e.get("kind", "capability") == "capability"]
    if complete:
        adequacy_pass(rt, complete)
    return complete


def adequacy_pass(rt, candidates) -> None:
    """For each entry whose scenario set has fully shipped, ask whether those scenarios
    prove its WHOLE promise, then drain or park on the digest the reviewer left — never
    on what it said."""
    rt.report.line(f"adequacy · {', '.join(candidates)}", indent=1)
    for cap in candidates:
        # Banked per capability, not per gate. Each pass through this loop APPLIES its
        # verdict — drains the entry, or appends residuals and moves the park count — so
        # a refused launch partway down the list would otherwise pause the close with
        # earlier verdicts already on disk and nothing recorded, and the resume would
        # review them again: a second inadequate digest and its residuals appended twice,
        # against a capability with no new evidence either way. The banked list is reset
        # per stage, so the next close does re-review what is still open.
        step = f"adequacy:{cap}"
        if step in rt.state.stage_close_done:
            continue
        entry = _workset_entry(rt, cap)
        # a refused launch leaves no digest, which reads as "inadequate" and pushes the
        # capability one step closer to being parked — on no evidence at all
        dispatch.check_launch(rt.agents.launch("wf-adequacy", {
            "Question": adequacy.FULL_PROMISE,
            "Capability": cap,
            "Statement": entry.get("statement", "") or entry.get("observation", ""),
            "Value": entry.get("value", ""),
            "Claimed scenarios": ", ".join(_scenarios_for(rt, cap)) or "none authored",
            "Candidate shipped scenarios": "the [SYS-TC:] tags in the test tree "
                                           "(paths.tests) — derive them yourself",
        }, mode=adequacy.FULL_PROMISE, task_id=cap))

        result = rt.cli.mutate("pipeline", "drain-capability", cap)
        drained = bool(result.ok and result.data.get("drained"))
        rt.tele.event("adequacy", capability=cap, sprint=rt.state.sprint_id,
                      stage=rt.state.stage, verdict=result.data.get("verdict"),
                      drained=drained)
        rt.report.line(
            f"{cap} · verdict {result.data.get('verdict') or 'unreadable'} · "
            f"{'drained' if drained else 'stays open'}",
            symbol=progress.OK if drained else progress.BAD, indent=2)
        if drained:
            rt.state.close_step_done(step)
            continue
        # inadequate: the residuals are the next cut's input, so they go back onto the
        # capability before the park count decides whether it is designable at all.
        digest = result.data.get("digest") or adequacy.newest_digest(rt.cfg, cap)
        if digest:
            rt.cli.mutate("pipeline", "append-residuals", cap, "--digest", str(digest))
        if adequacy.should_park(rt.cfg, cap):
            if adequacy.park_capability(rt.cfg, cap):
                rt.tele.event("park", capability=cap, sprint=rt.state.sprint_id,
                              consecutive=adequacy.consecutive_inadequate(rt.cfg, cap))
                rt.log(f"{cap} parked after {adequacy.PARK_THRESHOLD} inadequate "
                       f"verdicts — it needs a PO session")
        rt.state.close_step_done(step)


def _workset_entry(rt, ident) -> dict:
    """One entry from the durable work-set. Capabilities and learnings share a shape,
    and the completion gate names both."""
    for key in ("capabilities", "learnings"):
        path = rt.cfg.path_opt(key)
        if not path or not path.exists():
            continue
        try:
            doc = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError:
            continue
        for entry in (doc.get(key) or []):
            if isinstance(entry, dict) and str(entry.get("id")) == str(ident):
                return entry
    return {}


def _scenarios_for(rt, ident) -> list:
    """The SYS-TC ids nested under this entry — its proof obligation, kept beside the
    promise rather than minted into a transient artifact."""
    entry = _workset_entry(rt, ident)
    return [str(st["id"]) for st in (entry.get("system_tests") or [])
            if isinstance(st, dict) and st.get("id")]


# ── closeout ─────────────────────────────────────────────────────────────────


def closeout(rt) -> None:
    rt.state.enter("closeout")
    steps = _closeout_steps(rt)
    rt.cli.mutate("pipeline", "transition", "--to", "end_of_sprint")
    rt.report.phase(f"closeout · {' → '.join(steps)}")
    for step in steps:
        if step in rt.state.closeout_done:
            rt.report.line(f"{step} already ran this sprint — skipping", indent=1)
            continue
        if step == SHIP_STEP:
            ship(rt)          # terminal: it resets the state for the next sprint
            return
        # A step is banked as done below, and ship is terminal — so a step whose launch
        # was refused must stop the close, not be marked run. The pause keeps the banked
        # list, so a resume re-runs only what is still missing.
        dispatch.check_launch(rt.agents.launch(
            step, {"mode": step, "sprint_branch": rt.state.sprint_branch}, mode=step))
        rt.state.step_done(step)


def _closeout_steps(rt) -> list:
    """The configured closeout list, checked before anything runs: every entry must be
    one the driver can execute, and `ship` is terminal. Adequacy is not a step here —
    it fires per capability at every stage close, on a mechanical trigger with no
    ordering to configure."""
    steps = rt.cfg.closeout
    unknown = [s for s in steps if s != SHIP_STEP and not s.startswith("wf-")]
    if unknown:
        raise Halt("unknown_closeout_step",
                   f"closeout names {', '.join(unknown)} — an entry is a wf-* role or "
                   f"'{SHIP_STEP}'")
    if SHIP_STEP in steps and steps[-1] != SHIP_STEP:
        raise Halt("closeout_order",
                   f"'{SHIP_STEP}' is the terminal closeout step — it closes the sprint "
                   f"and publishes it, so nothing configured after it can run: "
                   f"{', '.join(steps)}")
    return steps


# ── ship ─────────────────────────────────────────────────────────────────────


def ship(rt) -> None:
    """Close the sprint, then publish it in the run's one push."""
    rt.report.line("closing the sprint — archive + drain", indent=1)
    closed = rt.cli.mutate("pipeline", "complete-sprint")
    if not closed.ok:
        raise Halt("complete_sprint", closed.stderr.strip() or "the close verb failed")
    drain = closed.data.get("drain") or {}
    sprint_id = closed.data.get("sprint_id") or rt.state.sprint_id
    branch = rt.state.sprint_branch

    # The accumulator is the PR body's spine: one block per stage, appended as that
    # stage merged. Ship folds the close's drain and the plan in around it, in place.
    body_path = rt.cfg.path("pr_body")
    body_path.parent.mkdir(parents=True, exist_ok=True)
    body_path.write_text(_pr_body(rt, sprint_id, drain))

    # The telemetry sink is committed and every role appended to it this sprint; it
    # ships with the close so the next sprint starts from a clean tree.
    rt.git.commit_paths(
        [rt.cfg.rel(k) for k in ("archive", "learnings", "capabilities", "plan",
                                 "telemetry")]
        + [".wf/config.yaml"],
        f"sprint close: archive + drain {sprint_id}")
    with rt.report.step(f"publishing {branch} (push + PR)",
                        budget_s=procs.NETWORK_TIMEOUT_S) as step:
        rt.git.push(branch)
        base = rt.git.pr_base(branch)
        rc, out = rt.git.pr_create(base, branch, _title(sprint_id, drain), body_path)
        step.ok = rc == 0
        step.note = f"PR onto {base}" if step.ok else f"rc={rc}"
    if rc != 0:
        raise Halt("ship_failed",
                   f"{sprint_id} is closed and committed on {branch}, but publishing "
                   f"it failed ({out}) — push and open the PR by hand")
    rt.tele.event("ship", sprint=sprint_id, branch=branch, base=base, pr=out,
                  stages=rt.state.stages_shipped)
    rt.log(f"shipped {sprint_id}: {out}")

    rt.state.sprint_id = None
    rt.state.sprint_branch = None
    rt.state.stage = None
    rt.state.stages_shipped = 0
    rt.state.closeout_done = []
    rt.state.enter("sprint_start")


def _title(sprint_id, drain) -> str:
    """What the PR is for, from the ids its stages set out to serve. The stage artifact
    that named them is archived and deleted at its own merge, so the close's drain is
    what still knows."""
    served = [str(s) for s in (drain.get("served") or [])]
    return f"{sprint_id}: {', '.join(served)}" if served else f"{sprint_id}: sprint"


def _pr_body(rt, sprint_id, drain) -> str:
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# Sprint {sprint_id}", "", f"Closed {stamp} by the wf driver.", ""]
    stages = _accumulated(rt)
    if stages:
        lines += [stages, ""]
    merged = drain.get("merged_tasks") or []
    if merged:
        lines += ["## Merged tasks", "", ", ".join(str(t) for t in merged), ""]
    for key, title in (("learnings_drained", "Learnings drained"),
                       ("learnings_retained", "Learnings retained"),
                       ("superseded_survivors", "Superseded scenarios still tagged")):
        items = drain.get(key) or []
        if items:
            lines += [f"## {title}", ""]
            lines += [f"- {_one_line(i)}" for i in items] + [""]
    plan = rt.cfg.path_opt("plan")
    if plan and plan.exists():
        lines += ["## Plan", "", plan.read_text().strip(), ""]
    return "\n".join(lines)


def _accumulated(rt) -> str:
    path = rt.cfg.path_opt("pr_body")
    return path.read_text().strip() if path and path.exists() else ""


def _one_line(item) -> str:
    if isinstance(item, dict):
        return " · ".join(f"{k}: {v}" for k, v in item.items() if v not in (None, "", []))
    return str(item)
