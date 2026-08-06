"""Role dispatch — launching one wf role headlessly.

The process contract is **exit code plus artifacts on disk**. The prompt names the
role's installed file and the parameters that role's envelope defines; the agent's
stdout goes to a log file and is never read, so no routing decision can be made
from an agent's prose.

The launch command is the config template ``driver.agent_cmd`` with ``{prompt}``
substituted — the one place harness differences live. A role named in
``driver.agent_cmd_overrides`` is launched with its own template instead, which is how
a single role gets a different model or harness flag.
"""
from __future__ import annotations

import datetime
import shlex
from dataclasses import dataclass, field
from pathlib import Path

import procs
import progress
from runtime import Pause

_PLACEHOLDER = "{prompt}"
# How much of the harness's last log line is quoted back when a launch failed.
_BLAME_CHARS = 200


class DispatchError(Exception):
    """A dispatch that cannot even be constructed — an uninstalled role, or a launch
    template with no {prompt} placeholder."""


@dataclass
class Launched:
    role: str
    exit_code: int
    duration_s: int
    timed_out: bool
    log_path: Path
    cmd: str
    params: dict = field(default_factory=dict)


def build_prompt(cfg, role: str, params: dict) -> str:
    """The prompt: read this role's file, follow it, and here are its parameters."""
    role_file = cfg.role_file(role)
    if role_file is None:
        raise DispatchError(
            f"{role} is not installed under paths.agents or paths.skills — "
            f"run the wf installer against this repo")
    lines = [f"Read {role_file} and follow it."]
    lines += [f"{k}: {v}" for k, v in params.items() if v is not None]
    return "\n".join(lines)


def render_cmd(template: str, prompt: str) -> str:
    """Substitute the prompt into the launch template, escaped for the quoting the
    template itself uses around the placeholder."""
    idx = template.find(_PLACEHOLDER)
    if idx < 0:
        raise DispatchError(
            f"driver.agent_cmd carries no {_PLACEHOLDER} placeholder: {template}")
    before = template[idx - 1] if idx > 0 else ""
    if before == '"':
        body = prompt.replace("\\", "\\\\").replace('"', '\\"') \
                     .replace("$", "\\$").replace("`", "\\`")
    elif before == "'":
        body = prompt.replace("'", "'\\''")
    else:
        body = shlex.quote(prompt)
    return template.replace(_PLACEHOLDER, body)


def last_line(log_path, limit: int = _BLAME_CHARS) -> str:
    """The last non-empty line a role left in its log. A harness that refused the launch
    outright — a session limit, an expired login — prints its reason there instead of
    doing the work, and that reason is the only account of the failure anyone gets."""
    try:
        text = Path(log_path).read_text(errors="replace")
    except OSError:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][:limit] if lines else ""


def check_launch(launched) -> None:
    """Call where a role left nothing its caller can route on. A non-zero exit then means
    the harness never ran it, so every conclusion the caller would otherwise draw about
    the WORK ("no contracts", "nothing in scope") is false. Pauses rather than halts: the
    position is already on disk and the condition is usually one you wait out.

    A non-zero exit on its own proves nothing and is deliberately not checked anywhere —
    a role can exit badly having already written a perfectly good artifact."""
    if launched is None or launched.exit_code == 0:
        return
    if launched.timed_out:
        raise Pause("launch_timeout",
                    f"{launched.role} was killed at its driver.agent_timeout_s budget "
                    f"after {progress.duration(launched.duration_s)} with nothing "
                    f"written — raise the budget if the role legitimately needs longer "
                    f"(log: {launched.log_path})")
    blame = last_line(launched.log_path)
    raise Pause("launch_failed",
                f"{launched.role} exited {launched.exit_code} and left nothing to route "
                f"on — the role never ran"
                + (f": {blame}" if blame else "")
                + f" (log: {launched.log_path})")


def describe(role: str, mode=None, task_id=None) -> str:
    """How one dispatch is named in the run's commentary. The mode is dropped when it
    only repeats the role (a closeout step names itself), and the id is printed bare —
    it is a task for most roles and a capability for the adequacy pass."""
    bits = [role]
    if mode and str(mode) != role:
        bits.append(f"({mode})")
    if task_id:
        bits.append(f"· {task_id}")
    return " ".join(str(b) for b in bits)


class Dispatcher:
    def __init__(self, cfg, telemetry, dry_run: bool = False, report=None):
        self.cfg = cfg
        self.telemetry = telemetry
        self.dry_run = dry_run
        self.report = report if report is not None else progress.silent()
        self.timeout = int(cfg.driver("agent_timeout_s"))
        self.log_dir = cfg.path("transient") / "driver-logs"
        self.planned: list = []

    def launch(self, role: str, params: dict, *, cwd=None, task_id=None,
               increment=None, mode=None) -> Launched:
        prompt = build_prompt(self.cfg, role, params)
        cmd = render_cmd(self.cfg.agent_cmd_for(role), prompt)
        log_path = self._log_path(role, task_id)
        if self.dry_run:
            self.planned.append({"role": role, "mode": mode, "task_id": task_id,
                                 "cwd": str(cwd or self.cfg.root), "cmd": cmd,
                                 "params": params})
            print(f"[dry-run] dispatch {role}"
                  + (f" ({mode})" if mode else "")
                  + (f" task={task_id}" if task_id else "")
                  + f"\n          cwd: {cwd or self.cfg.root}\n          cmd: {cmd}")
            return Launched(role, 0, 0, False, log_path, cmd, params)

        # The role's own output goes to the log and is never read, so this step line and
        # its heartbeat are the only sign the dispatch is alive — and `agent_timeout_s`
        # is how long it can hang before anything else notices.
        with self.report.step(describe(role, mode, task_id),
                              budget_s=self.timeout) as step:
            done = procs.run(cmd, timeout=self.timeout, cwd=cwd or self.cfg.root,
                             shell=True, stdout_path=log_path)
            step.ok = done.rc == 0
            step.note = (f"TIMED OUT after {self.timeout}s · log {log_path}"
                         if done.timed_out else
                         "" if step.ok else
                         f"rc={done.rc} · log {log_path}")
        self.telemetry.event("dispatch", role=role, mode=mode, task=task_id,
                             increment=increment, rc=done.rc,
                             duration_s=done.duration_s,
                             started_at=done.started_at, ended_at=done.ended_at,
                             timed_out=done.timed_out or None,
                             log=str(log_path))
        return Launched(role, done.rc, done.duration_s, done.timed_out, log_path,
                        cmd, params)

    def _log_path(self, role: str, task_id) -> Path:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        name = f"{role}-{task_id}-{stamp}.log" if task_id else f"{role}-{stamp}.log"
        return self.log_dir / name
