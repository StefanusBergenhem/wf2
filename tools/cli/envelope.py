"""envelope — the resolved config block a dispatched role reads.

A role needs the config's *values* (where the contract is, what the gate command is)
and nothing else. Opening ``.wf/config.yaml`` to get them costs the whole file —
comments included, and the file is mostly comments, because it is written for the
human who edits it.

So the driver renders this block into the dispatch prompt instead. It carries the keys
the role's own ``envelope:`` list declares, verbatim — the declaration sits beside the
text that reads them, so the two cannot drift apart unnoticed.

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

# Single keys renderable from outside those blocks. `project` is not a block a role may
# read whole — `target` and `base_branch` configure the launch and the branch it stacks
# on — but the repo's own name is a value a role that titles a human-facing artifact has
# no other source for.
_KEYS = ("project.name",)


def _value(raw) -> str:
    """One config value as the block renders it. A list (several test-tree roots in a
    polyglot repo) joins on ', '; an empty command stays an empty value rather than
    vanishing — "this repo has no such gate" is an answer, and a role that cannot tell
    it from a forgotten key would go looking for the key."""
    if isinstance(raw, (list, tuple)):
        return ", ".join(str(item) for item in raw)
    return "" if raw is None else str(raw)


def _renderable(doc):
    """Every ``<block>.<key>`` the config carries that a role may be handed, paired with
    its raw value: the named singles first, then each block's keys in config order."""
    for name in _KEYS:
        block, _, key = name.partition(".")
        values = doc.get(block)
        if isinstance(values, dict) and key in values:
            yield name, values[key]
    for block in _BLOCKS:
        values = doc.get(block)
        if not isinstance(values, dict):
            continue
        for key, raw in values.items():
            yield f"{block}.{key}", raw


def render(config_path: str, keys=None) -> str:
    """The block, one ``<block>.<key>: <value>`` line each, config order preserved.

    ``keys`` is the role's declared set — the keys its own text names. Every dispatch
    renders that set and nothing else, so the block is the role's working vocabulary
    rather than the config's whole surface. A declared key the config does not carry is
    fatal here: rendering the rest would leave the role reading a line that is not there,
    and it has no other way to resolve one.
    """
    doc = common.config_doc(config_path)
    wanted = None if keys is None else list(keys)
    lines, seen = [], set()
    for name, raw in _renderable(doc):
        if wanted is not None and name not in wanted:
            continue
        seen.add(name)
        lines.append(f"{name}: {_value(raw)}")
    if wanted is not None:
        missing = [k for k in wanted if k not in seen]
        if missing:
            common.die(f"{config_path} carries none of {', '.join(missing)} — declared "
                       f"in the role's envelope but absent from the config")
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
