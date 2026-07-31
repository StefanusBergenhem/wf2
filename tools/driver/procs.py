"""Bounded subprocess execution.

Every process the driver starts carries an explicit timeout. A harness default is
not a bound we chose, and an unbounded wait strands the whole loop behind one hung
call (L-090). The constants below are the ceilings for the fixed-cost calls; the
open-ended ones (agents, project gates) take their bound from config.
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass

# Git plumbing answers in milliseconds; a call still running after this has hung.
GIT_TIMEOUT_S = 120
# A commit or merge runs the project's hooks, which can be slow.
GIT_WRITE_TIMEOUT_S = 900
# A push (and `gh pr create`) crosses the network.
NETWORK_TIMEOUT_S = 600
# A `wf` verb is pure file work over a sprint's artifacts.
CLI_TIMEOUT_S = 300


@dataclass
class Completed:
    argv: list
    rc: int
    stdout: str
    stderr: str
    duration_s: int
    timed_out: bool = False


def run(argv, *, timeout: int, cwd=None, shell: bool = False,
        stdout_path=None) -> Completed:
    """Run a command under an explicit bound. Never raises on a non-zero exit or a
    timeout — the caller routes on ``rc``. ``stdout_path`` streams both streams to a
    file instead of capturing them (agent output is logged, never read)."""
    started = time.monotonic()
    handle = None
    try:
        if stdout_path is not None:
            stdout_path.parent.mkdir(parents=True, exist_ok=True)
            handle = stdout_path.open("w", encoding="utf-8", errors="replace")
            proc = subprocess.run(argv, cwd=cwd and str(cwd), shell=shell,
                                  stdout=handle, stderr=subprocess.STDOUT,
                                  timeout=timeout)
            out, err = "", ""
        else:
            proc = subprocess.run(argv, cwd=cwd and str(cwd), shell=shell,
                                  capture_output=True, text=True, timeout=timeout)
            out, err = proc.stdout, proc.stderr
        rc, timed_out = proc.returncode, False
    except subprocess.TimeoutExpired:
        out, err, rc, timed_out = "", f"timed out after {timeout}s", 124, True
    except (OSError, subprocess.SubprocessError) as exc:
        out, err, rc, timed_out = "", str(exc), 127, False
    finally:
        if handle is not None:
            handle.close()
    return Completed(argv=argv if isinstance(argv, list) else [str(argv)], rc=rc,
                     stdout=out, stderr=err,
                     duration_s=int(time.monotonic() - started), timed_out=timed_out)
