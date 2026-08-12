"""envelope — the resolved config block a dispatched role reads.

A role needs the config's *values* (where the contract is, what the gate command is)
and nothing else. Opening ``.wf/config.yaml`` to get them costs the whole file —
comments included, and the file is mostly comments, because it is written for the
human who edits it.

So the driver renders this block into the dispatch prompt instead. It carries every
``paths.*`` and ``commands.*`` key verbatim: no per-role subset, because a subset is a
second place that has to know what each role reads, and it drifts the first time a
skill needs one more key.

Transport only. A value here is the string the config carries, not an absolute path —
which tree a path roots on is the preamble's rule (a worktree-local artifact roots on
the worktree, a host-level one on the repo), and rendering absolutes here would decide
that question silently and differently.
"""
from __future__ import annotations

import common

# The blocks a role is given: where its artifacts live, the gates it runs, and the two
# sets of ceilings a role is itself held to (`limits.tasks_per_stage` bounds a cut,
# `hygiene.*_max` bounds what it writes). `driver` is deliberately absent — those knobs
# configure the loop that dispatched the role, and `driver.agent_cmd` carries the
# harness flags of the very launch reading this. So are `review`, `closeout`, `impact`
# and `orchestrate`: the loop acts on them, no role does.
_BLOCKS = ("paths", "commands", "limits", "hygiene")


def _value(raw) -> str:
    """One config value as the block renders it. A list (several test-tree roots in a
    polyglot repo) joins on ', '; an empty command stays an empty value rather than
    vanishing — "this repo has no such gate" is an answer, and a role that cannot tell
    it from a forgotten key would go looking for the key."""
    if isinstance(raw, (list, tuple)):
        return ", ".join(str(item) for item in raw)
    return "" if raw is None else str(raw)


def render(config_path: str) -> str:
    """The block, one ``<block>.<key>: <value>`` line each, config order preserved."""
    doc = common.config_doc(config_path)
    lines = []
    for block in _BLOCKS:
        values = doc.get(block)
        if not isinstance(values, dict):
            continue
        lines += [f"{block}.{key}: {_value(raw)}" for key, raw in values.items()]
    if not lines:
        common.die(f"{config_path} carries no paths — a role dispatched against it "
                   f"would have nowhere to read or write")
    return "\n".join(lines)


def _show(rest) -> int:
    args = common.base_parser("envelope show").parse_args(rest)
    print(render(args.config))
    return 0


COMMANDS = {
    ("envelope", "show"): _show,
}
