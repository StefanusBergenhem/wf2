"""Bounded subprocess execution.

Every process the driver starts carries an explicit timeout. A harness default is
not a bound we chose, and an unbounded wait strands the whole loop behind one hung
call (L-090). The constants below are the ceilings for the fixed-cost calls; the
open-ended ones (agents, project gates) take their bound from config.
"""
from __future__ import annotations

import datetime
import os
import signal
import subprocess
import time
from dataclasses import dataclass

# The CLI verbs run from the installed .wf/tools/ tree; bytecode written there
# dirties the target's working tree and trips the driver's clean-tree gate.
_ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}

# Git plumbing answers in milliseconds; a call still running after this has hung.
GIT_TIMEOUT_S = 120
# A commit or merge runs the project's hooks, which can be slow.
GIT_WRITE_TIMEOUT_S = 900
# A push (and `gh pr create`) crosses the network.
NETWORK_TIMEOUT_S = 600
# A `wf` verb is pure file work over a sprint's artifacts.
CLI_TIMEOUT_S = 300
# How long a killed process group gets to die between SIGTERM and SIGKILL.
KILL_GRACE_S = 5
# Own process group per command, so a timeout can kill the whole tree. POSIX only;
# elsewhere the direct-child kill is all that is available.
_OWN_GROUP = os.name == "posix"


@dataclass
class Completed:
    argv: list
    rc: int
    stdout: str
    stderr: str
    duration_s: int
    timed_out: bool = False
    # The wall-clock span, ISO-8601 UTC — what a telemetry row joins on.
    started_at: str = ""
    ended_at: str = ""


def _stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def kill_tree(proc, grace: int = KILL_GRACE_S) -> None:
    """Kill the whole process group, not just the direct child.

    A role is launched through a shell, so the agent is a GRANDchild: killing the shell
    leaves the agent running — still holding its worktree and still writing into a repo
    the driver has already given up on and moved past. SIGTERM first so an agent can
    flush, SIGKILL for whatever ignores it, then one final sweep of the group to catch
    grandchildren that outlived the child that spawned them."""
    if not _OWN_GROUP:
        proc.kill()
        return
    try:
        pgid = os.getpgid(proc.pid)
    except OSError:            # already reaped — nothing to signal
        return
    _signal_group(pgid, signal.SIGTERM)
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        _signal_group(pgid, signal.SIGKILL)
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            pass
    _signal_group(pgid, signal.SIGKILL)


def _signal_group(pgid: int, sig) -> None:
    try:
        os.killpg(pgid, sig)
    except OSError:            # the group is gone, which is the outcome we wanted
        return


def _terminate(proc) -> None:
    """Kill the tree, then reap it. The reap is not optional: a killed process whose
    pipes are never drained leaks two file descriptors per dispatch, and the loop runs
    thousands of them in one invocation."""
    if proc is None:
        return
    kill_tree(proc)
    try:
        proc.communicate(timeout=KILL_GRACE_S)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        pass


def run(argv, *, timeout: int, cwd=None, shell: bool = False,
        stdout_path=None) -> Completed:
    """Run a command under an explicit bound. Never raises on a non-zero exit or a
    timeout — the caller routes on ``rc``. ``stdout_path`` streams both streams to a
    file instead of capturing them (agent output is logged, never read).

    The command gets its own process group so that hitting the bound — or a ^C on the
    way through — takes the whole tree down with it."""
    started, started_at = time.monotonic(), _stamp()
    handle, proc = None, None
    try:
        if stdout_path is not None:
            stdout_path.parent.mkdir(parents=True, exist_ok=True)
            handle = stdout_path.open("w", encoding="utf-8", errors="replace")
            streams = {"stdout": handle, "stderr": subprocess.STDOUT}
        else:
            streams = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
                       "text": True}
        proc = subprocess.Popen(argv, cwd=cwd and str(cwd), shell=shell, env=_ENV,
                                start_new_session=_OWN_GROUP, **streams)
        out, err = proc.communicate(timeout=timeout)
        out, err, rc, timed_out = out or "", err or "", proc.returncode, False
    except subprocess.TimeoutExpired:
        _terminate(proc)
        out, err, rc, timed_out = "", f"timed out after {timeout}s", 124, True
    except (OSError, subprocess.SubprocessError) as exc:
        _terminate(proc)
        out, err, rc, timed_out = "", str(exc), 127, False
    except BaseException:
        # ^C reaches the driver, but its own process group no longer contains the agent
        _terminate(proc)
        raise
    finally:
        if handle is not None:
            handle.close()
    return Completed(argv=argv if isinstance(argv, list) else [str(argv)], rc=rc,
                     stdout=out, stderr=err,
                     duration_s=int(time.monotonic() - started), timed_out=timed_out,
                     started_at=started_at, ended_at=_stamp())
