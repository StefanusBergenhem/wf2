"""wf orchestrator — the standalone Python pipeline driver.

The orchestration *decisions* live in ``pipeline.py`` (``wf pipeline next`` + the
stage computation); the mechanical verdicts (inspect/dispatch/preserve/classify)
live in ``orchestrate.py``; the run-state mutations live in ``wf pipeline``. This
package adds only the **driver**: the asyncio loop that walks the staged machine —
per stage, dispatch the frontier the brain returns, run each task's
build→review-chain in its worktree, batch-merge the approved set at the stage
boundary, then run the closeout (retrospective, ship).

It is one of two interchangeable drivers over the same brain:

* **Driver 1** — the ``wf-orchestrate`` AI skill (an LLM reads the same
  ``pipeline next`` JSON and follows the same prose loop).
* **Driver 2** — *this* package, the same staged loop expressed in Python.

Both consume identical decisions, so they behave identically; only the
*agent-dispatch primitive* differs (an LLM spawns a subagent vs. this package's
``Dispatcher`` adapter). The dispatch verdict is never returned from the dispatch
call — it is read back from on-disk artifacts via the inspect verbs. That
on-disk-artifact contract is exactly what guarantees skill/Python parity.

The Claude-Agent-SDK adapter (``sdk_adapter``) is imported lazily by ``driver``
so a plain ``wf pipeline next`` query never loads the optional dependency.
"""
from __future__ import annotations

from .dispatch import Dispatcher, FakeDispatcher

__all__ = ["Dispatcher", "FakeDispatcher"]
