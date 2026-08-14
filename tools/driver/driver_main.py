"""wf-driver — the loop's command line.

    wf-driver [--config .wf/config.yaml] [--once] [--dry-run] [--max-sprints N]
              [--verbose] [--quiet]

With no flags it runs sprints continuously until a stop rule fires. ``--once`` runs
exactly one sprint (the bring-up mode that complements
``driver.max_unmerged_sprints: 1``). ``--dry-run`` prints the dispatches and git and
CLI mutations it would make, launches nothing, and writes nothing. ``--verbose`` adds
a line per `wf` verb; ``--quiet`` drops the heartbeat that says a long dispatch is
still alive.

An interrupt names what was in flight when it landed, so a run killed mid-hang says
where it was stuck.
"""
from __future__ import annotations

import argparse

import config as driver_config  # noqa: F401 — import FIRST: it puts the CLI on sys.path
import common  # noqa: E402  (needs the path config.py just added)
import loop
import progress


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
    p.add_argument("--verbose", "-v", action="store_true",
                   help="also print every `wf` verb the loop calls and its exit code")
    p.add_argument("--quiet", "-q", action="store_true",
                   help=f"drop the every-{progress.HEARTBEAT_S}s heartbeat that says a "
                        f"running dispatch is still alive")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    report = progress.Reporter(verbose=args.verbose,
                               heartbeat_s=None if args.quiet else progress.HEARTBEAT_S)
    rt = loop.build_runtime(args.config, dry_run=args.dry_run, report=report)
    progress.install_interrupt_report(report)
    try:
        return loop.run_loop(rt, once=args.once, max_sprints=args.max_sprints)
    except KeyboardInterrupt:
        return 130
