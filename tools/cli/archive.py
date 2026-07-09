"""wf archive — snapshot a working-set artifact into the maintainer research archive.

A WRITE-ONLY sink. A drain site calls ``archive add`` as an artifact leaves the working
set, so the wf2 maintainer can study run quality offline. No wf role is ever instructed
to read ``paths.archive``; it is outside the governed working set and therefore exempt
from the derive-don't-store rule — nothing consumes it as truth, so it cannot rot.
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

import common


def snapshot(root: Path, label: str, src: Path, move: bool = False) -> Path:
    """Copy (or move) ``src`` into ``<root>/<label>/<stamp>__<name>`` and return the
    destination. Distinct snapshots of the same file never clobber — the stamp carries
    microseconds, with a counter fallback. The single archiving mechanism every drain
    site shares (the CLI verb and complete-sprint both call this)."""
    dest_dir = root / label
    dest_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%f") + "Z"
    dest = dest_dir / f"{stamp}__{src.name}"
    counter = 1
    while dest.exists():
        dest = dest_dir / f"{stamp}-{counter}__{src.name}"
        counter += 1

    if move:
        src.replace(dest)
    else:
        shutil.copy2(src, dest)
    return dest


def _add(rest):
    """Snapshot FILE into ``<paths.archive>/<label>/``. Copies by default; ``--move``
    drains the source."""
    p = common.base_parser("archive add")
    p.add_argument("file", help="the working-set file to snapshot")
    p.add_argument("--label", required=True,
                   help="archive subdir grouping (e.g. a sprint_id, or 'capabilities')")
    p.add_argument("--move", action="store_true",
                   help="move instead of copy — drain the source from the working set")
    args = p.parse_args(rest)

    src = Path(args.file)
    if not src.exists():
        common.die(f"archive: file not found: {src}")

    root = common.resolve_path(args.config, "archive", None)  # dies if paths.archive unset
    dest = snapshot(root, args.label, src, move=args.move)
    common.emit({"archived": str(dest), "label": args.label, "moved": bool(args.move)}, args.format)
    return 0


COMMANDS = {
    ("archive", "add"): _add,
}
