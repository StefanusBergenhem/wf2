"""wf-driver — the loop's command line.

    wf-driver [--config .wf/config.yaml] [--once] [--dry-run] [--max-sprints N]

With no flags it runs sprints continuously until a stop rule fires. ``--once`` runs
exactly one sprint (the bring-up mode that complements
``driver.max_unmerged_sprints: 1``). ``--dry-run`` prints the dispatches and git and
CLI mutations it would make, launches nothing, and writes nothing.
"""
from __future__ import annotations

import argparse

import config as driver_config  # noqa: F401 — import FIRST: it puts the CLI on sys.path
import common  # noqa: E402  (needs the path config.py just added)
import loop


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wf-driver", description="run the wf loop")
    p.add_argument("--config", default=common.default_config(),
                   help="path to .wf/config.yaml (default: the main checkout's)")
    p.add_argument("--once", action="store_true",
                   help="run exactly one sprint, then exit")
    p.add_argument("--dry-run", action="store_true",
                   help="print the planned dispatches without launching anything")
    p.add_argument("--max-sprints", type=int, default=None,
                   help="stop after this many sprints in this invocation")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return loop.run(args.config, once=args.once, dry_run=args.dry_run,
                    max_sprints=args.max_sprints)
