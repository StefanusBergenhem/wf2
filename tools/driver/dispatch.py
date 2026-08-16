"""Role dispatch — launching one wf role headlessly.

The process contract is **exit code plus artifacts on disk**. The prompt names the
role's installed file and the parameters that role's envelope defines; the agent's
stdout goes to a log file and is never read, so no routing decision can be made
from an agent's prose.

The launch command is the config template ``driver.agent_cmd`` with ``{prompt}``
substituted — the one place harness differences live. A role named in
``driver.agent_cmd_overrides`` is launched with its own template instead, which is how
a single role gets a different model or harness flag.

A refused launch is not always a failure to route on: a harness rate limit is a wait,
not a verdict, so it is slept out and re-launched here rather than surfacing to callers
who would each have to recognise it.
"""
from __future__ import annotations

import datetime
import json
import re
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path

import config  # noqa: F401 — import FIRST: it puts the CLI on sys.path
import envelope  # noqa: E402  (a CLI module — needs the path config.py just added)
import procs
import progress
from runtime import Pause

_PLACEHOLDER = "{prompt}"
# How much of the harness's last log line is quoted back when a launch failed.
_BLAME_CHARS = 200

# How far past the reset the relaunch goes. The reset is the instant the window rolls;
# launching on it races the roll and spends the retry on a second refusal.
RATE_LIMIT_MARGIN_S = 120
# How many times ONE dispatch waits out a limit before handing the refusal back. A wait
# resumes into a fresh window, so needing a third means the work does not fit a window
# and a human should size it, not the loop.
RATE_LIMIT_WAITS = 2
# The wait is slept in slices this long so `driver.stop_file` is honoured while waiting
# — a wait can run for hours, and a stop must not have to outlast it.
RATE_LIMIT_POLL_S = 30

# What a rate-limited harness leaves behind. All three are tolerant of the whitespace a
# pretty-printer would add.
#
# The refusal is a `rate_limit_info` whose OWN status is `rejected`, and its reset is
# read from inside that same object. A harness that is still serving emits the identical
# event shape — same `resetsAt`, same `rateLimitType`, even the word `rejected` under
# `overageStatus` — as a routine heartbeat on nearly every stream, so anything short of
# reading the status reads every ordinary log as rate-limited, and a role that died for
# its own reasons sleeps to the next window rollover instead of surfacing.
_RATE_LIMIT_INFO_RE = re.compile(r'"rate_limit_info"\s*:\s*\{([^}]*)\}')
_REJECTED_RE = re.compile(r'"status"\s*:\s*"rejected"')
_RESETS_AT_RE = re.compile(r'"resetsAt"\s*:\s*(\d+)')
# The exit itself, for a harness that refuses without ever emitting the event.
_LIMIT_EXIT_RE = re.compile(r'"api_error_status"\s*:\s*429|"error"\s*:\s*"rate_limit"')


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


_ENVELOPE_RE = re.compile(r"^envelope:\s*$((?:\n\s+-\s+\S+)*)", re.M)


def declared_keys(role_file: Path) -> list:
    """The config keys a role's ``envelope:`` frontmatter list names, or None when it
    declares none. This is the role's own statement of what it reads, kept beside the
    text that reads it so the two cannot drift apart unnoticed."""
    try:
        text = Path(role_file).read_text(errors="replace")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    found = _ENVELOPE_RE.search(text[3:end] if end > 0 else text)
    if not found:
        return None
    return [line.strip().lstrip("- ").strip()
            for line in found.group(1).splitlines() if line.strip()]


def build_prompt(cfg, role: str, params: dict) -> str:
    """The prompt: read this role's file, follow it, and here is everything it would
    otherwise have to go find — its own directory, the config values it reads, and this
    dispatch's parameters.

    The config block is why the role never opens ``.wf/config.yaml``: that file is
    written for the human who edits it and is ~80% comments, so reading it costs about
    twelve times what its values do. It carries the keys the role declares and no others
    — the rest are keys it would have to decide to ignore, several of them pointing at
    the spec layer its own skill forbids it to open. A call-site parameter wins over the
    block — the block is repo-wide defaults, a parameter is this dispatch's own state.
    """
    role_file = cfg.role_file(role)
    if role_file is None:
        raise DispatchError(
            f"{role} is not installed under paths.agents or paths.skills — "
            f"run the wf installer against this repo")
    keys = declared_keys(role_file)
    if keys is None:
        raise DispatchError(
            f"{role_file} declares no `envelope:` list in its frontmatter, so there is "
            f"no set of config keys to hand {role} — add one naming every paths./"
            f"commands./limits./hygiene. key its text reads, plus project.name when it "
            f"names that")
    given = {k: v for k, v in params.items() if v is not None}
    lines = [f"Read {role_file} and follow it.",
             f"role_dir: {cfg.role_dir(role, role_file)}"]
    lines += [line for line in envelope.render(cfg.config_path, keys=keys).splitlines()
              if line.split(":", 1)[0] not in given]
    lines += [f"{k}: {v}" for k, v in given.items()]
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


# What the harness's closing result line calls each number, mapped to the name the
# telemetry row already uses for it — so a cost row reads like the usage rows beside it.
_RESULT_FIELDS = (("total_cost_usd", "cost_usd"), ("num_turns", "num_turns"))
_RESULT_USAGE = (("input_tokens", "input"), ("output_tokens", "output"),
                 ("cache_read_input_tokens", "cache_read"),
                 ("cache_creation_input_tokens", "cache_creation"))
# How one pi request is named inside its message usage, mapped to the same row names.
_PI_USAGE = (("input", "input"), ("output", "output"),
             ("cacheRead", "cache_read"), ("cacheWrite", "cache_creation"))
# Bound on the post-dispatch usage fold, so a wedged hook never holds a slot.
_USAGE_HOOK_TIMEOUT_S = 15


def _cost_total(usage) -> float:
    c = usage.get("cost")
    v = (c or {}).get("total")
    return v if isinstance(v, (int, float)) else 0.0


def _claude_result_row(line):
    """The closing ``type: result`` line a claude run writes — or None when the line
    is something else (or a truncated parse)."""
    try:
        row = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(row, dict) or row.get("type") != "result":
        return None
    out = {name: row[key] for key, name in _RESULT_FIELDS if key in row}
    usage = row.get("usage")
    if isinstance(usage, dict):
        out.update({name: usage[key] for key, name in _RESULT_USAGE if key in usage})
    return out


def _pi_stream_usage(lines):
    """pi's stream has no closed ``result`` line — each request's usage rides the
    ``message_end`` event, with the same assistant message repeated on ``turn_end``.
    Deduped by response id so a request sums once. None when the log is not a pi
    stream (nothing to report, not a free run)."""
    by_id, anon = {}, []
    for line in lines:
        try:
            entry = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(entry, dict) or entry.get("type") != "message_end":
            continue
        message = entry.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        usage = message.get("usage")
        if not isinstance(usage, dict) or usage.get("cost") is None:
            continue
        rid = message.get("responseId")
        if rid:
            by_id[rid] = usage
        else:
            anon.append(usage)
    usages = list(by_id.values()) + anon
    if not usages:
        return None

    def val(u, key):
        v = u.get(key) or 0
        return v if isinstance(v, (int, float)) else 0

    out = {"cost_usd": sum(_cost_total(u) for u in usages),
           "num_turns": len(usages)}
    out.update({name: sum(val(u, key) for u in usages)
                for key, name in _PI_USAGE})
    return out


def session_id(log_path):
    """The session this dispatch ran in, off its own log's opening line.

    Both harnesses name it there under their own key — pi as a ``session`` event, claude
    as its ``system``/``init`` line. It is the exact key between a dispatch (which knows
    the role) and the usage rows the harness hook writes (which know the tokens), so the
    two never have to be matched by comparing time windows: parallel dispatches nest, and
    a long build's window contains a faster sibling's whole build→review chain.
    """
    try:
        text = Path(log_path).read_text(errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(entry, dict):
            continue
        if entry.get("type") == "session" and entry.get("id"):
            return str(entry["id"])
        if entry.get("type") == "system" and entry.get("session_id"):
            return str(entry["session_id"])
    return None


def _is_pi_cmd(cmd) -> bool:
    """Whether a launch template names the pi harness — the one trim that decides
    whether the driver folds a dispatch's usage itself (pi has no end hook)."""
    return bool(cmd) and cmd.lstrip().split(None, 1)[0] == "pi"


def result_usage(log_path) -> dict:
    """What the dispatch cost, read off its own log — the only place cost exists, and
    the logs are transient, so a number not read here is gone with them.

    Two shapes: a claude run closes with one ``type: result`` line carrying the run
    totals; a pi run streams each request's usage on ``message_end``. Both map to the
    same row names, so the telemetry row never has to know which harness wrote it.

    Best-effort by contract: a dispatch the harness refused never wrote the line, and a
    killed one wrote half of it. Both report nothing rather than a zero, because a zero
    would read as a run that was free. Telemetry is observability, never correctness."""
    try:
        lines = [x for x in Path(log_path).read_text(errors="replace").splitlines()
                 if x.strip()]
    except OSError:
        return {}
    if not lines:
        return {}
    for line in reversed(lines):
        row = _claude_result_row(line)
        if row is not None:
            return row
    pi = _pi_stream_usage(lines)
    return pi if pi is not None else {}


def rate_limit_wait_s(log_path, *, now, cap_s):
    """How long to sleep before re-launching a refused dispatch, or ``None`` when
    waiting is not the answer.

    ``None`` covers both "this was not a rate limit" and "the limit outlasts
    ``driver.rate_limit_max_wait_s``" — one decision, since a weekly limit resets days
    out and sleeping the cap would burn hours only to relaunch into the same refusal.
    Either way the refusal goes back to the caller, whose pause quotes the harness's own
    line about when it lifts."""
    try:
        text = Path(log_path).read_text(errors="replace")
    except OSError:
        return None
    resets = _refusal_resets(text)
    if not resets:
        if not _LIMIT_EXIT_RE.search(text):
            return None
        # Rate-limited, but the harness named no reset. Waiting the cap is the only
        # move left, and it is the whole window — so the next launch is inside a new one.
        return cap_s
    wait = max(0, resets[-1] - int(now)) + RATE_LIMIT_MARGIN_S
    return None if wait > cap_s else wait


def _refusal_resets(text):
    """Every reset stamp the harness recorded on a REFUSAL, in the order it recorded
    them. A `rate_limit_info` that is not itself `rejected` is the still-serving
    heartbeat and names a window nothing is waiting for."""
    out = []
    for match in _RATE_LIMIT_INFO_RE.finditer(text):
        info = match.group(1)
        if not _REJECTED_RE.search(info):
            continue
        resets = _RESETS_AT_RE.search(info)
        if resets:
            out.append(int(resets.group(1)))
    return out


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
    only repeats the role — whether it names the role outright (a closeout step names
    itself) or names what the role already is with the prefix off, which is how a role's
    ordinary mode reads (``wf-build`` in mode ``build``). A mode that says something the
    role does not — ``fix``, ``merge``, ``resume`` — survives. The id is printed bare: it
    is a task for most roles and a capability for the adequacy pass."""
    bits = [role]
    if mode and str(mode) not in (role, role.removeprefix("wf-")):
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
        self.rate_limit_cap = int(cfg.driver("rate_limit_max_wait_s"))
        self.log_dir = cfg.path("transient") / "driver-logs"
        self.planned: list = []
        # Injected so the wait is testable without spending it.
        self.clock = time.time
        self.sleep = time.sleep

    def launch(self, role: str, params: dict, *, cwd=None, task_id=None,
               stage=None, mode=None) -> Launched:
        prompt = build_prompt(self.cfg, role, params)
        cmd = render_cmd(self.cfg.agent_cmd_for(role), prompt)
        if self.dry_run:
            log_path = self._log_path(role, task_id)
            self.planned.append({"role": role, "mode": mode, "task_id": task_id,
                                 "cwd": str(cwd or self.cfg.root), "cmd": cmd,
                                 "params": params})
            print(f"[dry-run] dispatch {role}"
                  + (f" ({mode})" if mode else "")
                  + (f" task={task_id}" if task_id else "")
                  + f"\n          cwd: {cwd or self.cfg.root}\n          cmd: {cmd}")
            return Launched(role, 0, 0, False, log_path, cmd, params)

        waits = 0
        while True:
            log_path = self._log_path(role, task_id)
            done = self._run_once(cmd, role, mode, task_id, stage, cwd, log_path)
            if done.rc == 0 or done.timed_out or waits >= RATE_LIMIT_WAITS:
                break
            wait_s = rate_limit_wait_s(log_path, now=self.clock(),
                                       cap_s=self.rate_limit_cap)
            if wait_s is None:
                break
            waits += 1
            if not self._wait_out_limit(role, wait_s, task_id):
                break
        return Launched(role, done.rc, done.duration_s, done.timed_out, log_path,
                        cmd, params)

    def _run_once(self, cmd, role, mode, task_id, stage, cwd, log_path):
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
                             stage=stage, rc=done.rc,
                             duration_s=done.duration_s,
                             started_at=done.started_at, ended_at=done.ended_at,
                             timed_out=done.timed_out or None,
                             session_id=session_id(log_path),
                             log=str(log_path), **result_usage(log_path))
        self._emit_pi_usage(cmd, log_path)
        return done

    def _emit_pi_usage(self, cmd, log_path):
        """Fold a pi dispatch's session into the same ``kind: usage`` rows the claude
        Stop hook writes, so ``wf telemetry roles`` reports a pi run's context the same
        way. pi has no end-of-session hook, so the driver is it. Best-effort and never a
        verdict: a pi session the hook cannot read is simply not reported — telemetry is
        observability, never correctness."""
        if not _is_pi_cmd(cmd):
            return
        sid = session_id(log_path)
        if not sid:
            return
        hook = self.cfg.path("tools") / "telemetry" / "pi_usage_hook.py"
        if not hook.is_file():
            return
        cmdline = (f'python3 {shlex.quote(str(hook))}')
        for flag, value in (("--session-dir", str(self.cfg.path("pi_sessions"))),
                            ("--session-id", sid),
                            ("--sink", str(self.cfg.path("telemetry")))):
            cmdline += f" {flag} {shlex.quote(value)}"
        try:
            procs.run(cmdline, timeout=_USAGE_HOOK_TIMEOUT_S, shell=True)
        except Exception:
            return

    def _wait_out_limit(self, role: str, wait_s: int, task_id=None) -> bool:
        """Sleep out a harness rate limit. Returns False if a stop was asked for while
        waiting — the wait is the longest thing the loop ever does, so it watches for
        one rather than making a stop queue behind it."""
        self.telemetry.event("rate_limit_wait", role=role, task=task_id,
                             wait_s=wait_s)
        label = (f"rate limit — {describe(role, None, task_id)} waits "
                 f"{progress.duration(wait_s)} for the harness window to reset")
        with self.report.step(label, budget_s=wait_s) as step:
            remaining = wait_s
            while remaining > 0:
                if self.cfg.stop_file.exists():
                    step.ok = False
                    step.note = f"{self.cfg.stop_file} appeared — not relaunching"
                    return False
                nap = min(RATE_LIMIT_POLL_S, remaining)
                self.sleep(nap)
                remaining -= nap
        return True

    def _log_path(self, role: str, task_id) -> Path:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        name = f"{role}-{task_id}-{stamp}.log" if task_id else f"{role}-{stamp}.log"
        return self.log_dir / name
