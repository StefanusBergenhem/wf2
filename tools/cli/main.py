"""wf — the orchestration CLI entrypoint.

Dispatches ``wf <noun> <verb> [args]`` to a handler. Each subcommand module
exposes a ``COMMANDS`` dict mapping ``(noun, verb)`` to a function; adding a verb
never touches this file beyond the module import list.
"""
from __future__ import annotations

import sys

import orchestrate
import pipeline
import sprint
import telemetry

REGISTRY: dict = {}
for _mod in (pipeline, orchestrate, sprint, telemetry):
    REGISTRY.update(getattr(_mod, "COMMANDS", {}))


def _commands_line() -> str:
    return ", ".join(f"{n} {v}" for n, v in sorted(REGISTRY))


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        sys.stdout.write(f"usage: wf <noun> <verb> [args]\ncommands: {_commands_line()}\n")
        return 0
    if len(argv) < 2:
        sys.stderr.write(f"wf: need <noun> <verb>\ncommands: {_commands_line()}\n")
        return 2
    noun, verb, rest = argv[0], argv[1], argv[2:]
    handler = REGISTRY.get((noun, verb))
    if handler is None:
        sys.stderr.write(f"wf: unknown command '{noun} {verb}'\ncommands: {_commands_line()}\n")
        return 2
    return handler(rest)


if __name__ == "__main__":
    raise SystemExit(main())
