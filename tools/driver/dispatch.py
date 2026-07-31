"""Role dispatch — launching one wf role headlessly.

The process contract is **exit code plus artifacts on disk**. The prompt names the
role's installed file and the parameters that role's envelope defines; the agent's
stdout goes to a log file and is never read, so no routing decision can be made
from an agent's prose.

The launch command is the config template ``driver.agent_cmd`` with ``{prompt}``
substituted — the one place harness differences live.
"""
from __future__ import annotations

import datetime
import shlex
from dataclasses import dataclass, field
from pathlib import Path

import procs

_PLACEHOLDER = "{prompt}"


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


class Dispatcher:
    def __init__(self, cfg, telemetry, dry_run: bool = False):
        self.cfg = cfg
        self.telemetry = telemetry
        self.dry_run = dry_run
        self.timeout = int(cfg.driver("agent_timeout_s"))
        self.log_dir = cfg.path("transient") / "driver-logs"
        self.planned: list = []

    def launch(self, role: str, params: dict, *, cwd=None, task_id=None,
               increment=None, mode=None) -> Launched:
        prompt = build_prompt(self.cfg, role, params)
        cmd = render_cmd(self.cfg.agent_cmd, prompt)
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

        done = procs.run(cmd, timeout=self.timeout, cwd=cwd or self.cfg.root,
                         shell=True, stdout_path=log_path)
        self.telemetry.event("dispatch", role=role, mode=mode, task_id=task_id,
                             increment=increment, exit_code=done.rc,
                             duration_s=done.duration_s,
                             timed_out=done.timed_out or None,
                             log=str(log_path))
        return Launched(role, done.rc, done.duration_s, done.timed_out, log_path,
                        cmd, params)

    def _log_path(self, role: str, task_id) -> Path:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        name = f"{role}-{task_id}-{stamp}.log" if task_id else f"{role}-{stamp}.log"
        return self.log_dir / name
