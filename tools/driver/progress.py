"""Progress reporting — the loop's window onto itself.

A sprint is minutes of git and CLI work wrapped around hours of headless role
dispatches, and a dispatch writes nothing to the terminal until it exits. Without a
running commentary a live run and a hung one look identical, so every long-running
thing the loop starts is announced when it opens, beats while it runs, and reports
its duration when it closes. ``inflight`` answers the other question — *where* it is
stuck — by naming what is open right now and for how long.

Reporting only: nothing here decides anything, and a step never changes what its
body returns or raises.
"""
from __future__ import annotations

import contextlib
import signal
import sys
import threading
import time
from dataclasses import dataclass

# How often an open step says it is still alive. Long enough not to bury the real
# lines, short enough that a stalled terminal is obvious within a coffee break.
HEARTBEAT_S = 60

RUN = "▶"     # something long-running just started
OK = "✔"      # it finished
BAD = "✖"     # it finished badly, or blew up
BEAT = "·"    # it is still going
PHASE = "──"  # a position in the machine, not an action


def duration(seconds) -> str:
    """A span in the largest unit that still reads at a glance."""
    total = int(seconds)
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m{total % 60:02d}s"
    return f"{total // 3600}h{(total % 3600) // 60:02d}m"


def clock_str(seconds) -> str:
    """Elapsed-since-start as a fixed-width clock, so the lines stay in columns."""
    total = int(seconds)
    return f"{total // 3600}:{(total % 3600) // 60:02d}:{total % 60:02d}"


@dataclass
class Step:
    """The handle a step body writes its outcome onto before the step closes."""
    label: str
    ok: bool = True
    note: str = ""


class _Activity:
    def __init__(self, label: str, started: float, budget_s):
        self.label = label
        self.started = started
        self.budget_s = budget_s
        self.stop = threading.Event()


class Reporter:
    """Writes the run's commentary to one stream, safely from parallel task threads."""

    def __init__(self, *, out=None, heartbeat_s=HEARTBEAT_S, verbose: bool = False,
                 clock=time.monotonic):
        self.out = out if out is not None else sys.stdout
        self.heartbeat_s = heartbeat_s
        self.verbose = verbose
        self._clock = clock
        self._t0 = clock()
        self._write_lock = threading.Lock()
        self._active_lock = threading.Lock()
        self._active: list = []

    # ── writing ──────────────────────────────────────────────────────────────

    def line(self, message: str, *, symbol: str = "", indent: int = 0) -> None:
        body = "  " * indent + (f"{symbol} " if symbol else "") + str(message)
        with self._write_lock:
            self.out.write(f"[wf-driver {clock_str(self._clock() - self._t0)}] {body}\n")
            self.out.flush()

    def detail(self, message: str, *, indent: int = 1) -> None:
        """A line only a `--verbose` run wants: every CLI verb, every routing read."""
        if self.verbose:
            self.line(message, symbol=BEAT, indent=indent)

    def phase(self, message: str) -> None:
        self.line(f"{PHASE} {message}")

    # ── steps ────────────────────────────────────────────────────────────────

    @contextlib.contextmanager
    def step(self, label: str, *, budget_s=None, indent: int = 1):
        """Announce a long-running thing, beat while it runs, and close with how long
        it took. Yields a ``Step`` the body can write its outcome onto."""
        handle = Step(label)
        activity = _Activity(label, self._clock(), budget_s)
        self.line(label, symbol=RUN, indent=indent)
        with self._active_lock:
            self._active.append(activity)
        beater = self._start_beating(activity, indent)
        try:
            yield handle
        except BaseException as exc:                       # noqa: BLE001 — re-raised
            handle.ok = False
            handle.note = f"{type(exc).__name__}: {exc}".strip()[:200]
            raise
        finally:
            activity.stop.set()
            if beater is not None:
                beater.join(timeout=1)
            with self._active_lock:
                self._active.remove(activity)
            # duration first, so it sits in the same place on every closing line —
            # a note can be a long log path, and the span must not end up behind it
            span = duration(self._clock() - activity.started)
            note = f" · {handle.note}" if handle.note else ""
            self.line(f"{label} — {span}{note}",
                      symbol=OK if handle.ok else BAD, indent=indent)

    def _start_beating(self, activity: _Activity, indent: int):
        if not self.heartbeat_s:
            return None
        thread = threading.Thread(target=self._beat, args=(activity, indent),
                                  daemon=True)
        thread.start()
        return thread

    def _beat(self, activity: _Activity, indent: int) -> None:
        while not activity.stop.wait(self.heartbeat_s):
            budget = (f" / {duration(activity.budget_s)} budget"
                      if activity.budget_s else "")
            span = duration(self._clock() - activity.started)
            self.line(f"{activity.label} still running — {span}{budget}",
                      symbol=BEAT, indent=indent + 1)

    # ── what is open right now ───────────────────────────────────────────────

    def inflight(self) -> list:
        """Every open step as ``(label, seconds)`` — what a stalled run is waiting on."""
        now = self._clock()
        with self._active_lock:
            return [(a.label, now - a.started) for a in self._active]

    def report_inflight(self, reason: str) -> None:
        open_now = self.inflight()
        if not open_now:
            self.line(f"{reason} — nothing was in flight")
            return
        self.line(f"{reason} — in flight:")
        for label, secs in open_now:
            self.line(f"{label} (running {duration(secs)})", symbol=BEAT, indent=1)


def install_interrupt_report(report: Reporter) -> None:
    """Make ^C say where the run was. The snapshot has to be taken here, inside the
    handler: the interrupt unwinds every open ``step`` on its way out, so a caller that
    waits to catch ``KeyboardInterrupt`` always finds the in-flight set already empty.
    The previous handler is restored first, so a second ^C is the plain one."""
    previous = signal.getsignal(signal.SIGINT)

    def handler(_signum, _frame):
        signal.signal(signal.SIGINT, previous)
        report.report_inflight("interrupted")
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, handler)


def silent() -> Reporter:
    """A reporter that writes nowhere — the default for tests and library use."""
    return Reporter(out=_Null(), heartbeat_s=None)


class _Null:
    def write(self, _text):
        return 0

    def flush(self):
        return None
