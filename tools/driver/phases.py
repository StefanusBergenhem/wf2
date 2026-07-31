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
import gitops
import issues
import slice as slice_reader
import yaml
from runtime import Halt, Pause

_TC_HEAD_RE = re.compile(r"-\s+\*\*(SYS-TC-\d+):\*\*")
_COVERS_RE = re.compile(r"\*\*Covers:\*\*(.*)")


# ── sprint_start ─────────────────────────────────────────────────────────────


def sprint_start(rt, resume: bool = False) -> None:
    if not rt.git.is_clean():
        raise Halt("dirty_tree",
                   "the working tree carries uncommitted changes — a sprint branch "
                   "must be cut from a clean tree")
    base = rt.git.stack_tip()
    if resume and rt.state.sprint_id:
        sprint_id, branch = rt.state.sprint_id, rt.state.sprint_branch
    else:
        sprint_id = rt.git.next_sprint_id()
        branch = f"{gitops.SPRINT_PREFIX}{sprint_id}"
        rt.state.start_sprint(sprint_id, branch)
    rt.state.save()
    rt.git.start_branch(branch, base)
    rt.tele.event("sprint_start", sprint_id=sprint_id, branch=branch, base=base)
    rt.log(f"sprint {sprint_id} on {branch} (base {base})")

    # resume hygiene: drop handoffs whose consumer can never return, and free the
    # slots an interrupted run left occupied.
    rt.cli.raw("orchestrate", "sweep-transients", "--config", rt.cfg.config_path,
               mutating=True)
    rt.cli.mutate("pipeline", "reclaim-stale")
    rt.cli.mutate("pipeline", "transition", "--to", "preparing",
                  "--reason", f"driver sprint {sprint_id}", "--sprint-id", sprint_id)

    rt.agents.launch("wf-discover", {"mode": "refresh"}, mode="refresh")
    brief = rt.cfg.path_opt("discover_brief")
    if not rt.dry_run and (not brief or not brief.exists()):
        raise Halt("no_discover_brief",
                   f"wf-discover left no brief at {brief} — the design role cannot "
                   f"ground without it")
    rt.state.enter("designing")


# ── designing ────────────────────────────────────────────────────────────────


def designing(rt) -> dict:
    """Dispatch the design role and gate its slice. Returns the slice-check payload
    (its serves header and increment list drive the rest of the sprint)."""
    rt.state.enter("designing")
    rt.agents.launch("wf-designer", {"Mode": "originate"}, mode="originate")
    _escalation_gate(rt)
    if rt.dry_run:
        raise Pause("dry_run", "planned dispatches printed; nothing was launched")
    return slice_gate(rt)


def resume_ruling(rt) -> dict:
    """Re-enter a run that halted for a human ruling. The design role's resume mode
    consumes the ruling; an unruled brief keeps the run paused."""
    decision_prep = rt.cfg.path_opt("decision_prep")
    if decision_prep and decision_prep.exists():
        if not ruling_present(decision_prep):
            rt.tele.event("stop", reason="escalation", sprint_id=rt.state.sprint_id,
                          detail="the ruling section is still empty")
            raise Pause("escalation", f"{decision_prep} carries no ruling yet")
        rt.agents.launch("wf-designer", {"Mode": "resume"}, mode="resume")
        _escalation_gate(rt)
    return slice_gate(rt)


def ruling_present(path) -> bool:
    """True when the brief's `## Ruling` section holds prose — comments and blank
    lines are not a ruling."""
    text = path.read_text()
    body = slice_reader.section(text, "Ruling")
    return bool(re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL).strip())


def slice_gate(rt) -> dict:
    """The mechanical gate between design and build: a slice on disk that passes
    `wf slice check`. A red gate routes to the design role's repair mode."""
    slice_path = rt.cfg.path("design_slice")
    if not slice_path.exists():
        raise Pause("work_exhaustion",
                    "the design role wrote no slice — nothing open and unparked is "
                    "in scope")

    for _ in range(rt.cfg.max_attempts):
        check = rt.cli.read("slice", "check")
        if check.ok:
            rt.state.enter("increment_loop")
            return check.data
        findings = "; ".join(
            f"{e.get('code')}: {e.get('msg')}" for e in (check.data.get("errors") or [])
            if isinstance(e, dict))
        issues.record(rt, f"`wf slice check` is red — {findings or 'no findings emitted'}",
                      scope="slice")
        item = issues.open_entries(rt)[0]
        issues.repair(rt, item)
        _escalation_gate(rt)
    raise Halt("slice_check_red", "the slice never passed its gate")


def _escalation_gate(rt) -> None:
    decision_prep = rt.cfg.path_opt("decision_prep")
    if decision_prep and decision_prep.exists():
        rt.state.enter("awaiting_ruling")
        rt.tele.event("stop", reason="escalation", sprint_id=rt.state.sprint_id,
                      detail=str(decision_prep))
        raise Pause("escalation", f"a ruling is pending in {decision_prep}")


# ── closeout ─────────────────────────────────────────────────────────────────


def closeout(rt) -> None:
    rt.state.enter("closeout")
    rt.cli.mutate("pipeline", "transition", "--to", "end_of_sprint")
    served = _served_ids(rt)
    for step in rt.cfg.closeout:
        if step == "ship":
            adequacy_pass(rt, served)
            ship(rt)
            continue
        rt.agents.launch(step, {"mode": step, "sprint_branch": rt.state.sprint_branch},
                         mode=step)


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
    for cap in [s for s in served if str(s).startswith("CAP-")]:
        entry = _capability_entry(rt, cap)
        rt.agents.launch("wf-adequacy", {
            "Question": "full promise",
            "Capability": cap,
            "Statement": entry.get("statement", ""),
            "Value": entry.get("value", ""),
            "Claimed scenarios": ", ".join(_scenarios_for(rt, cap)) or "none in this slice",
            "Candidate shipped scenarios": "the [SYS-TC:] tags in the test tree "
                                           "(paths.tests) — derive them yourself",
        }, mode="full-promise", task_id=cap)

        result = rt.cli.mutate("pipeline", "drain-capability", cap)
        drained = bool(result.ok and result.data.get("drained"))
        rt.tele.event("adequacy", capability=cap, sprint_id=rt.state.sprint_id,
                      verdict=result.data.get("verdict"), drained=drained)
        if drained:
            continue
        if adequacy.should_park(rt.cfg, cap):
            if adequacy.park_capability(rt.cfg, cap):
                rt.tele.event("park", capability=cap, sprint_id=rt.state.sprint_id,
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
    # Read what the close drains out of the working set BEFORE it drains it: the
    # decision report and the slice's title are both gone once complete-sprint runs.
    decisions = ""
    spec_decisions = rt.cfg.path_opt("spec_decisions")
    if spec_decisions and spec_decisions.exists():
        decisions = spec_decisions.read_text()
    title = _title(rt)

    closed = rt.cli.mutate("pipeline", "complete-sprint")
    if not closed.ok:
        raise Halt("complete_sprint", closed.stderr.strip() or "the close verb failed")
    drain = closed.data.get("drain") or {}
    sprint_id = closed.data.get("sprint_id") or rt.state.sprint_id
    branch = rt.state.sprint_branch

    body_path = rt.cfg.path("transient") / f"pr-body-{sprint_id}.md"
    body_path.parent.mkdir(parents=True, exist_ok=True)
    body_path.write_text(_pr_body(rt, sprint_id, drain, decisions))

    rt.git.commit_paths(
        [rt.cfg.rel(k) for k in ("archive", "learnings", "capabilities", "plan")]
        + [".wf/config.yaml"],
        f"sprint close: archive + drain {sprint_id}")
    rt.git.push(branch)
    base = rt.git.pr_base(branch)
    rc, out = rt.git.pr_create(base, branch, f"{sprint_id}: {title}", body_path)
    if rc != 0:
        raise Halt("ship_failed",
                   f"{sprint_id} is closed and committed on {branch}, but publishing "
                   f"it failed ({out}) — push and open the PR by hand")
    rt.tele.event("ship", sprint_id=sprint_id, branch=branch, base=base, pr=out)
    rt.log(f"shipped {sprint_id}: {out}")

    rt.state.sprint_id = None
    rt.state.sprint_branch = None
    rt.state.increment = 1
    rt.state.enter("sprint_start")


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
        lines += ["## Decisions", "", decisions.strip(), ""]
    return "\n".join(lines)


def _one_line(item) -> str:
    if isinstance(item, dict):
        return " · ".join(f"{k}: {v}" for k, v in item.items() if v not in (None, "", []))
    return str(item)
