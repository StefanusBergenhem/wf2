"""Dispatch adapter — the one abstraction that differs between the two drivers.

A ``Dispatcher`` runs a named agent to completion against a given working
directory. Crucially, it returns **nothing**: the agent's verdict is read back
afterwards from on-disk artifacts (``review_ready.yaml`` / ``feedback.yaml`` /
git HEAD) via the inspect verbs. The AI-skill driver dispatches by spawning a
subagent; this Python driver dispatches via a ``Dispatcher`` subclass — but both
read the verdict from the same on-disk artifacts, which is what makes the two
drivers behave identically.

* ``ClaudeSdkDispatcher`` (in ``sdk_adapter``) drives live agents through the
  Claude Agent SDK.
* ``FakeDispatcher`` (here) records calls and optionally runs a caller-supplied
  callback so a test can *simulate* the agent by writing the artifacts the
  inspect verbs will later read — no SDK, no subprocess.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, NamedTuple


class DispatchCall(NamedTuple):
    """One recorded dispatch — what was asked of which agent, where."""

    agent_name: str
    worktree: str | None
    envelope: str


class Dispatcher(ABC):
    """Runs an agent to completion. The verdict is NOT returned — it is read
    from on-disk artifacts afterwards via the inspect verbs. This is the seam
    that guarantees AI-skill / Python parity: swapping the dispatch primitive
    cannot change the loop's decisions, because the loop never sees a return
    value from dispatch; it only ever reads the same files off disk."""

    @abstractmethod
    def dispatch(self, agent_name: str, envelope: str, worktree: str | None) -> None:
        """Run ``agent_name`` to completion with ``envelope`` as its prompt
        context, in ``worktree`` (or the repo root when ``worktree`` is None).
        Must block until the agent has finished writing its artifacts."""
        raise NotImplementedError


class FakeDispatcher(Dispatcher):
    """Test double. Records every dispatch and optionally invokes a callback so a
    test can simulate the agent's on-disk effects (write ``review_ready.yaml``,
    make a commit, append a DI, etc.).

    The callback receives the full :class:`DispatchCall` and may inspect/mutate
    whatever it needs. It returns nothing — exactly like a real dispatch.
    """

    def __init__(self, on_dispatch: Callable[[DispatchCall], None] | None = None):
        self.calls: list[DispatchCall] = []
        self._on_dispatch = on_dispatch

    def dispatch(self, agent_name: str, envelope: str, worktree: str | None) -> None:
        call = DispatchCall(agent_name=agent_name, worktree=worktree, envelope=envelope)
        self.calls.append(call)
        if self._on_dispatch is not None:
            self._on_dispatch(call)

    def agents_dispatched(self) -> list[str]:
        return [c.agent_name for c in self.calls]

    def calls_for_agent(self, agent_name: str) -> list[DispatchCall]:
        return [c for c in self.calls if c.agent_name == agent_name]
