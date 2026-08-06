"""The per-sprint phase machine.

``sprint_start`` (branch off the stack tip, refresh the read-view) → ``designing``
(the design role cuts the slice; the slice gate decides whether it is usable) →
``increment_loop`` (increments.py) → ``closeout`` (retro, close-time adequacy,
drain, ship). Each phase writes its position to the driver state before it acts, so
a restarted driver re-enters where it left off.
"""
from __future__ import annotations

import datetime
import re

import adequacy
import config  # noqa: F401 — importing it puts the CLI package on sys.path
import dispatch
import gitops
import issues
import procs
import progress
import slice as slice_reader
import yaml
from runtime import Halt, Pause

_TC_HEAD_RE = re.compile(r"-\s+\*\*(SYS-TC-\d+):\*\*")
_COVERS_RE = re.compile(r"\*\*Covers:\*\*(.*)")
_HALT_MODE_RE = re.compile(r"^\s*mode:\s*([A-Za-z][\w-]*)", re.MULTILINE)
_BRIEF_DI_RE = re.compile(r"^\s*di_id:\s*([A-Za-z][\w-]*)", re.MULTILINE)

# How many times a red slice is sent back to the design role before the run halts.
# The build→review budget (`review.max_attempts`) bounds one task's chain, not this.
SLICE_GATE_ATTEMPTS = 3
# The closeout steps the driver runs itself; everything else in the list is a role.
ADEQUACY_STEP = "adequacy"
SHIP_STEP = "ship"
DECISION_LOG_SECTION = "Decision log"


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
    rt.tele.event("sprint_start", sprint=sprint_id, branch=branch, base=base)
    rt.report.phase(f"sprint {sprint_id} · branch {branch} · base {base}")

    resume_hygiene(rt)
    rt.cli.mutate("pipeline", "transition", "--to", "preparing",
                  "--reason", f"driver sprint {sprint_id}", "--sprint-id", sprint_id)

    launched = rt.agents.launch("wf-discover", {"mode": "refresh"}, mode="refresh")
    brief = rt.cfg.path_opt("discover_brief")
    if not rt.dry_run and (not brief or not brief.exists()):
        dispatch.check_launch(launched)
        raise Halt("no_discover_brief",
                   f"wf-discover left no brief at {brief} — the design role cannot "
                   f"ground without it")
    rt.state.enter("designing")


def resume_hygiene(rt) -> None:
    """Drop handoffs whose consumer can never return, and free the slots an interrupted
    run left occupied. Runs on EVERY re-entry, not only the one through sprint_start: an
    interruption inside the increment loop is precisely where a task is left holding a
    slot no one will ever release, and that re-entry never passes through sprint_start."""
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


def designing(rt) -> dict:
    """Dispatch the design role and gate its slice. Returns the slice-check payload
    (its serves header and increment list drive the rest of the sprint)."""
    rt.state.enter("designing")
    rt.report.phase("designing — the design role cuts the slice")
    launched = rt.agents.launch("wf-designer", {"Mode": "originate"}, mode="originate")
    _escalation_gate(rt)
    if rt.dry_run:
        raise Pause("dry_run", "planned dispatches printed; nothing was launched")
    return slice_gate(rt, launched)


def resume_ruling(rt) -> dict:
    """Re-enter a run that halted for a human ruling. The design role's resume mode
    consumes the ruling; an unruled brief keeps the run paused. Where the run picks up
    is what the brief's ``mode:`` header says the halt interrupted: an originate halt
    carries on designing, a repair halt goes back into the increment loop."""
    decision_prep = rt.cfg.path_opt("decision_prep")
    halted_in = ""
    if decision_prep and decision_prep.exists():
        rt.report.phase(f"resuming a halted run · ruling brief {decision_prep}")
        if not ruling_present(decision_prep):
            rt.tele.event("stop", reason="escalation", sprint=rt.state.sprint_id,
                          detail="the ruling section is still empty")
            raise Pause("escalation", f"{decision_prep} carries no ruling yet")
        halted_in = halt_mode(decision_prep)
        di_id = brief_di_id(decision_prep)
        launched = rt.agents.launch("wf-designer", {"Mode": "resume"}, mode="resume")
        _escalation_gate(rt)
        _close_resolved_twins(rt, di_id)
    else:
        launched = None
    if halted_in == "repair" or (not halted_in
                                 and rt.state.resume_phase == "increment_loop"):
        rt.state.resume_phase = None
        rt.state.enter("increment_loop")
        return {}
    rt.state.resume_phase = None
    return slice_gate(rt, launched)


def ruling_present(path) -> bool:
    """True when the brief's `## Ruling` section holds prose — comments and blank
    lines are not a ruling."""
    text = path.read_text()
    body = slice_reader.section(text, "Ruling")
    return bool(_prose(body))


def halt_mode(path) -> str:
    """The design-role mode the brief says it halted in (`originate` | `repair`), or ''
    when it names none."""
    found = _HALT_MODE_RE.search(path.read_text())
    return found.group(1).lower() if found else ""


def brief_di_id(path) -> str:
    """The design issue the brief says the halt interrupted, or '' when it names none
    (an originate halt carries no issue)."""
    found = _BRIEF_DI_RE.search(path.read_text())
    return found.group(1) if found else ""


def _close_resolved_twins(rt, di_id: str) -> None:
    """Close the run-state twin of every design issue the resume run finished. The
    design role writes only the host file; the twin is what parks the task the issue
    names, so a twin left open keeps that task parked, the sub-layer never settles and
    the increment burns its iteration budget. The brief's own issue closes on the ruling;
    any other twin closes once the host file says it is resolved."""
    res = rt.cli.read("pipeline", "unresolved-design-issues")
    for item in (res.data.get("issues") or []):
        if not isinstance(item, dict) or not item.get("di_id"):
            continue
        twin = str(item["di_id"])
        host = issues.entry(rt, twin)
        if twin == di_id or (host is not None
                             and str(host.get("status")) == "resolved"):
            rt.cli.mutate("pipeline", "resolve-design-issue", twin)


def _prose(body: str) -> str:
    """A markdown section's body with html comments stripped — a comment is not prose."""
    return re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL).strip()


def slice_gate(rt, launched=None) -> dict:
    """The mechanical gate between design and build: a slice on disk that passes
    `wf slice check`. A red gate routes to the design role's repair mode."""
    slice_path = rt.cfg.path("design_slice")
    if not slice_path.exists():
        # "no slice" only means "nothing is in scope" if the role actually ran and
        # decided so; a refused launch leaves the same empty disk for a different reason
        dispatch.check_launch(launched)
        raise Pause("work_exhaustion",
                    "the design role wrote no slice — nothing open and unparked is "
                    "in scope")

    for attempt in range(1, SLICE_GATE_ATTEMPTS + 1):
        check = rt.cli.read("slice", "check")
        if check.ok:
            rt.report.line(f"slice check green · serves "
                           f"{', '.join(str(s) for s in check.data.get('serves') or []) or '—'}",
                           symbol=progress.OK, indent=1)
            rt.state.enter("increment_loop")
            return check.data
        findings = "; ".join(
            f"{e.get('code')}: {e.get('msg')}" for e in (check.data.get("errors") or [])
            if isinstance(e, dict))
        rt.report.line(f"slice check red ({attempt}/{SLICE_GATE_ATTEMPTS}) — "
                       f"back to the design role: {findings or 'no findings emitted'}",
                       symbol=progress.BAD, indent=1)
        issues.record(rt, f"`wf slice check` is red — {findings or 'no findings emitted'}",
                      scope="slice")
        item = issues.open_entries(rt)[0]
        issues.repair(rt, item)
        _escalation_gate(rt)
    raise Halt("slice_check_red", "the slice never passed its gate")


def _escalation_gate(rt) -> None:
    decision_prep = rt.cfg.path_opt("decision_prep")
    if decision_prep and decision_prep.exists():
        rt.state.suspend("awaiting_ruling")
        rt.tele.event("stop", reason="escalation", sprint=rt.state.sprint_id,
                      detail=str(decision_prep))
        raise Pause("escalation", f"a ruling is pending in {decision_prep}")


# ── closeout ─────────────────────────────────────────────────────────────────


def closeout(rt) -> None:
    rt.state.enter("closeout")
    steps = _closeout_steps(rt)
    rt.cli.mutate("pipeline", "transition", "--to", "end_of_sprint")
    served = _served_ids(rt)
    rt.report.phase(f"closeout · {' → '.join(steps)}")
    for step in steps:
        if step in rt.state.closeout_done:
            rt.report.line(f"{step} already ran this sprint — skipping", indent=1)
            continue
        if step == SHIP_STEP:
            ship(rt)          # terminal: it resets the state for the next sprint
            return
        if step == ADEQUACY_STEP:
            adequacy_pass(rt, served)
        else:
            # A step is banked as done below, and ship is terminal — so a step whose
            # launch was refused must stop the close, not be marked run. The pause keeps
            # the banked list, so a resume re-runs only what is still missing.
            dispatch.check_launch(rt.agents.launch(
                step, {"mode": step, "sprint_branch": rt.state.sprint_branch},
                mode=step))
        rt.state.step_done(step)


def _closeout_steps(rt) -> list:
    """The configured closeout list, checked before anything runs: every entry must be
    one the driver can execute, `ship` is terminal, and the close-time adequacy gate
    must run before it — shipping archives the slice and drains the working set the
    gate reviews."""
    steps = rt.cfg.closeout
    unknown = [s for s in steps
               if s not in (ADEQUACY_STEP, SHIP_STEP) and not s.startswith("wf-")]
    if unknown:
        raise Halt("unknown_closeout_step",
                   f"closeout names {', '.join(unknown)} — an entry is a wf-* role, "
                   f"'{ADEQUACY_STEP}', or '{SHIP_STEP}'")
    if SHIP_STEP not in steps:
        return steps
    if steps[-1] != SHIP_STEP:
        raise Halt("closeout_order",
                   f"'{SHIP_STEP}' is the terminal closeout step — it archives the "
                   f"slice and drains the working set, so nothing configured after it "
                   f"can run: {', '.join(steps)}")
    if ADEQUACY_STEP not in steps:
        raise Halt("closeout_order",
                   f"closeout must run '{ADEQUACY_STEP}' before '{SHIP_STEP}' — "
                   f"without it no capability is ever proven, drained or parked")
    return steps


def _served_ids(rt) -> list:
    check = rt.cli.read("slice", "check")
    served = check.data.get("serves")
    if served:
        return list(served)
    path = rt.cfg.path_opt("design_slice")
    return slice_reader.serves_ids(path.read_text()) if path and path.exists() else []


def adequacy_pass(rt, served) -> None:
    """The close-time gate: for every capability the slice served, ask whether the
    shipped scenario register now covers its WHOLE promise, then drain or park on the
    digest the reviewer left — never on what it said."""
    candidates = [s for s in served if str(s).startswith("CAP-")]
    rt.report.line(f"adequacy · {', '.join(candidates) or 'no capability served'}",
                   indent=1)
    for cap in candidates:
        entry = _capability_entry(rt, cap)
        # a refused launch leaves no digest, which reads as "inadequate" and pushes the
        # capability one step closer to being parked — on no evidence at all
        dispatch.check_launch(rt.agents.launch("wf-adequacy", {
            "Question": adequacy.FULL_PROMISE,
            "Capability": cap,
            "Statement": entry.get("statement", ""),
            "Value": entry.get("value", ""),
            "Claimed scenarios": ", ".join(_scenarios_for(rt, cap)) or "none in this slice",
            "Candidate shipped scenarios": "the [SYS-TC:] tags in the test tree "
                                           "(paths.tests) — derive them yourself",
        }, mode=adequacy.FULL_PROMISE, task_id=cap))

        result = rt.cli.mutate("pipeline", "drain-capability", cap)
        drained = bool(result.ok and result.data.get("drained"))
        rt.tele.event("adequacy", capability=cap, sprint=rt.state.sprint_id,
                      verdict=result.data.get("verdict"), drained=drained)
        rt.report.line(
            f"{cap} · verdict {result.data.get('verdict') or 'unreadable'} · "
            f"{'drained' if drained else 'stays open'}",
            symbol=progress.OK if drained else progress.BAD, indent=2)
        if drained:
            continue
        # inadequate: the residuals are the next design's input, so they go back onto
        # the capability before the park count decides whether it is designable at all.
        digest = result.data.get("digest") or adequacy.newest_digest(rt.cfg, cap)
        if digest:
            rt.cli.mutate("pipeline", "append-residuals", cap, "--digest", str(digest))
        if adequacy.should_park(rt.cfg, cap):
            if adequacy.park_capability(rt.cfg, cap):
                rt.tele.event("park", capability=cap, sprint=rt.state.sprint_id,
                              consecutive=adequacy.consecutive_inadequate(rt.cfg, cap))
                rt.log(f"{cap} parked after {adequacy.PARK_THRESHOLD} inadequate "
                       f"verdicts — it needs a PO session")


def _capability_entry(rt, cap) -> dict:
    path = rt.cfg.path_opt("capabilities")
    if not path or not path.exists():
        return {}
    try:
        doc = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError:
        return {}
    for entry in (doc.get("capabilities") or []):
        if isinstance(entry, dict) and str(entry.get("id")) == str(cap):
            return entry
    return {}


def _scenarios_for(rt, cap) -> list:
    """The slice's SYS-TC ids whose Covers line names this capability."""
    path = rt.cfg.path_opt("design_slice")
    if not path or not path.exists():
        return []
    section = slice_reader.section(path.read_text(), "System test cases")
    out, current = [], None
    for line in section.splitlines():
        head = _TC_HEAD_RE.search(line)
        if head:
            current = head.group(1)
            continue
        covers = _COVERS_RE.search(line)
        if current and covers and cap in covers.group(1):
            out.append(current)
    return out


# ── ship ─────────────────────────────────────────────────────────────────────


def ship(rt) -> None:
    """Close the sprint, then publish it in the run's one push."""
    # Read what the close drains out of the working set BEFORE it drains it: the slice
    # carries both the decision report and the sprint's title, and complete-sprint
    # archives the slice.
    decisions = decision_log(rt)
    title = _title(rt)

    rt.report.line("closing the sprint — archive + drain", indent=1)
    closed = rt.cli.mutate("pipeline", "complete-sprint")
    if not closed.ok:
        raise Halt("complete_sprint", closed.stderr.strip() or "the close verb failed")
    drain = closed.data.get("drain") or {}
    sprint_id = closed.data.get("sprint_id") or rt.state.sprint_id
    branch = rt.state.sprint_branch

    body_path = rt.cfg.path("transient") / f"pr-body-{sprint_id}.md"
    body_path.parent.mkdir(parents=True, exist_ok=True)
    body_path.write_text(_pr_body(rt, sprint_id, drain, decisions))

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
        rc, out = rt.git.pr_create(base, branch, f"{sprint_id}: {title}", body_path)
        step.ok = rc == 0
        step.note = f"PR onto {base}" if step.ok else f"rc={rc}"
    if rc != 0:
        raise Halt("ship_failed",
                   f"{sprint_id} is closed and committed on {branch}, but publishing "
                   f"it failed ({out}) — push and open the PR by hand")
    rt.tele.event("ship", sprint=sprint_id, branch=branch, base=base, pr=out)
    rt.log(f"shipped {sprint_id}: {out}")

    rt.state.sprint_id = None
    rt.state.sprint_branch = None
    rt.state.increment = 1
    rt.state.closeout_done = []
    rt.state.enter("sprint_start")


def decision_log(rt) -> str:
    """The slice's `## Decision log` — every decision the design role took below the
    escalation gate. It ships in the PR body, which is where the human reviews them."""
    path = rt.cfg.path_opt("design_slice")
    if not path or not path.exists():
        return ""
    return _prose(slice_reader.section(path.read_text(), DECISION_LOG_SECTION))


def _title(rt) -> str:
    path = rt.cfg.path_opt("design_slice")
    if path and path.exists():
        for line in path.read_text().splitlines():
            if line.startswith("# "):
                return line[2:].strip()
    return "sprint"


def _pr_body(rt, sprint_id, drain, decisions) -> str:
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# Sprint {sprint_id}", "", f"Closed {stamp} by the wf driver.", ""]
    served = drain.get("served") or []
    if served:
        lines += ["## Served", "", ", ".join(str(s) for s in served), ""]
    merged = drain.get("merged_tasks") or []
    if merged:
        lines += ["## Merged tasks", "", ", ".join(str(t) for t in merged), ""]
    for key, title in (("learnings_drained", "Learnings drained"),
                       ("learnings_retained", "Learnings retained"),
                       ("adequacy_candidates", "Adequacy candidates"),
                       ("superseded_survivors", "Superseded scenarios still tagged")):
        items = drain.get(key) or []
        if items:
            lines += [f"## {title}", ""]
            lines += [f"- {_one_line(i)}" for i in items] + [""]
    plan = rt.cfg.path_opt("plan")
    if plan and plan.exists():
        lines += ["## Plan", "", plan.read_text().strip(), ""]
    if decisions.strip():
        lines += [f"## {DECISION_LOG_SECTION}", "", decisions.strip(), ""]
    return "\n".join(lines)


def _one_line(item) -> str:
    if isinstance(item, dict):
        return " · ".join(f"{k}: {v}" for k, v in item.items() if v not in (None, "", []))
    return str(item)
