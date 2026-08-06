"""The continuous loop — one invocation, sprint after sprint.

Between sprints the stop rules decide whether another one starts. Inside a sprint
the phase machine runs to ship. Every position is on disk, so an interrupted run
resumes exactly where it stopped instead of starting over.
"""
from __future__ import annotations

import cliverbs
import config as driver_config
import dispatch
import events
import gitops
import increments
import phases
import procs
import progress
import state as driver_state
import stoprules
from runtime import Halt, Pause, Runtime


def build_runtime(config_path: str, dry_run: bool = False,
                  report=None) -> Runtime:
    cfg = driver_config.load(config_path)
    tele = events.Telemetry(cfg, dry_run=dry_run)
    report = report if report is not None else progress.Reporter()
    return Runtime(
        cfg=cfg,
        state=driver_state.load(cfg, dry_run=dry_run),
        tele=tele,
        cli=cliverbs.Cli(cfg, dry_run=dry_run, report=report),
        git=gitops.Git(cfg, dry_run=dry_run),
        agents=dispatch.Dispatcher(cfg, tele, dry_run=dry_run, report=report),
        dry_run=dry_run,
        report=report,
    )


def run(config_path: str, *, once: bool = False, dry_run: bool = False,
        max_sprints=None, verbose: bool = False) -> int:
    report = progress.Reporter(verbose=verbose)
    rt = build_runtime(config_path, dry_run=dry_run, report=report)
    return run_loop(rt, once=once, max_sprints=max_sprints)


def _mode(once, max_sprints, dry_run) -> str:
    span = ("one sprint" if once else
            f"up to {max_sprints} sprints" if max_sprints else "continuous")
    return f"{span} · dry-run" if dry_run else span


def run_loop(rt, *, once: bool = False, max_sprints=None) -> int:
    """Returns a process exit code: 0 for a clean stop, 1 for a halt."""
    rt.report.line(f"wf loop starting · base {rt.cfg.base_branch} · "
                   f"{_mode(once, max_sprints, rt.dry_run)} · "
                   f"config {rt.cfg.config_path}")
    shipped = 0
    while True:
        refresh_base(rt)
        if rt.state.sprint_id is None:
            stop = stoprules.pre_sprint(rt.cfg, rt.git)
            if stop:
                return _stop(rt, stop.reason, stop.detail)
        try:
            run_sprint(rt)
        except Pause as pause:
            return _stop(rt, pause.reason, pause.detail)
        except Halt as halt:
            rt.state.stop_reason = halt.reason
            rt.state.save()
            rt.tele.event("halt", reason=halt.reason, detail=halt.detail,
                          sprint=rt.state.sprint_id)
            rt.log(f"HALT {halt.reason}: {halt.detail}")
            return 1

        shipped += 1
        if rt.state.stop_pending:
            reason = rt.state.stop_pending
            rt.state.stop_pending = None
            return _stop(rt, reason, "the sprint in flight was finished and shipped")
        if once or (max_sprints and shipped >= max_sprints):
            return _stop(rt, "sprint_limit", f"{shipped} sprint(s) this invocation")
        rt.report.line(f"{shipped} sprint(s) shipped this invocation — starting the next")


def run_sprint(rt) -> None:
    """One iteration of the loop, entered at whatever phase the state names."""
    if rt.state.phase not in ("sprint_start", "stopped"):
        # Re-entering past sprint_start skips the branch cut AND the hygiene that frees
        # what the interrupted run was holding, so both are done here instead.
        rt.report.line(f"resuming at phase {rt.state.phase}"
                       + (f" · sprint {rt.state.sprint_id}" if rt.state.sprint_id
                          else ""))
        phases.verify_position(rt)
        phases.resume_hygiene(rt)
    if rt.state.phase == "awaiting_ruling":
        phases.resume_ruling(rt)
    if rt.state.phase in ("sprint_start", "stopped"):
        phases.sprint_start(rt, resume=rt.state.sprint_id is not None)
    if rt.state.phase == "designing":
        phases.designing(rt)
    if rt.state.phase == "increment_loop":
        numbers = _increment_numbers(rt)
        increments.run_increment_loop(rt, numbers)
        rt.state.enter("closeout")
    if rt.state.phase == "closeout":
        phases.closeout(rt)


def _increment_numbers(rt) -> list:
    check = rt.cli.read("slice", "check")
    if not check.ok:
        raise Halt("slice_check_red",
                   "the slice on disk no longer passes its gate — it cannot be built")
    return [int(item["n"]) for item in (check.data.get("increments") or [])]


def refresh_base(rt) -> None:
    """Teach git what merged on the forge before anything derives the stack from it.
    An unreachable origin is a warning, not a stop: the loop then works from local
    state, which is stale but never wrong about what it does know."""
    with rt.report.step(f"fetching {rt.cfg.base_branch} from origin",
                        budget_s=procs.NETWORK_TIMEOUT_S) as step:
        ok, detail = rt.git.fetch_base()
        step.ok = ok
        step.note = "up to date" if ok else detail
    if ok:
        return
    rt.tele.event("warn", reason="fetch_failed", detail=detail,
                  sprint=rt.state.sprint_id)
    rt.log(f"warning: could not fetch {rt.cfg.base_branch} from origin — the stack is "
           f"derived from local state ({detail})")


def _stop(rt, reason: str, detail: str) -> int:
    rt.state.stop_reason = reason
    rt.state.save()
    rt.tele.event("stop", reason=reason, detail=detail, sprint=rt.state.sprint_id)
    rt.log(f"stop ({reason}): {detail}")
    return 0
