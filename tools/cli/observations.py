"""wf observations — the mechanical bound on the admission buffer.

``paths.observations`` sits in front of ``paths.learnings``. wf-retrospective files a
first sighting here and promotes it to a learning on its second, so a lone run's friction
never reaches the design role's context. That gate solves one direction only: the buffer
itself grows every run and drains only by promotion, which makes it the accumulator the
gate exists to prevent.

``observations age`` bounds it. It keeps the ``hygiene.observations_max`` most recently
sighted entries and archives the rest — recency read off each entry's newest ``sources``
stamp, so an observation seen again is not stale however old its first sighting. An entry
carrying no readable stamp is KEPT: nothing about it can be judged, and dropping on
absent evidence is how a real signal disappears.

Pure mechanism, no judgment: which entries survive is a sort, not a ranking.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import common

# The retrospective stamps a sighting with the session's `ended_at`, or `sprint:<id>` for
# a run pattern. Only the first form orders — a sprint id carries no time.
_STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")


def _sighted_at(entry):
    """The entry's newest readable source stamp, or None when it carries none. None is
    'unjudgeable', never 'ancient' — the caller keeps those."""
    stamps = []
    for src in (entry.get("sources") or []) if isinstance(entry, dict) else []:
        text = str(src).strip()
        if not _STAMP_RE.match(text):
            continue
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            continue
        stamps.append(parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc))
    return max(stamps) if stamps else None


def _age(rest):
    """Cut the buffer to hygiene.observations_max, archiving what leaves."""
    parser = common.base_parser("observations age")
    parser.add_argument("--max", type=int, default=None,
                        help="entry cap (default: hygiene.observations_max)")
    args = parser.parse_args(rest)

    doc_cfg = common.config_doc(args.config)
    cap = args.max if args.max is not None else \
        (doc_cfg.get("hygiene") or {}).get("observations_max")
    path = common.resolve_path(args.config, "observations", None) \
        if (doc_cfg.get("paths") or {}).get("observations") else None

    # An absent buffer and an unset cap are both silent: the buffer legitimately starts
    # empty, and a project that has not set the knob is not one to start deleting from.
    if not path or not path.exists() or not cap:
        common.emit({"kept": 0, "dropped": 0, "archived": None}, args.format)
        return 0

    doc = common.load_yaml(path, optional=True)
    entries = [e for e in (doc.get("observations") or []) if isinstance(e, dict)]
    if len(entries) <= int(cap):
        common.emit({"kept": len(entries), "dropped": 0, "archived": None}, args.format)
        return 0

    # Sort by recency, unstamped first so `keep` takes them last... except an unstamped
    # entry cannot be judged, so it is exempt from the drop and never enters the sort.
    unjudgeable = [e for e in entries if _sighted_at(e) is None]
    judgeable = sorted((e for e in entries if _sighted_at(e) is not None),
                       key=_sighted_at, reverse=True)
    room = max(int(cap) - len(unjudgeable), 0)
    keep_judgeable, dropped = judgeable[:room], judgeable[room:]
    # Restore the file's own order among the survivors — the buffer is a list a human may
    # read, and re-sorting it every run would churn the diff for no reader's benefit.
    survivors = set(map(id, unjudgeable + keep_judgeable))
    kept = [e for e in entries if id(e) in survivors]

    archived = _archive(args, dropped) if dropped else None
    if dropped:
        doc["observations"] = kept
        common.write_yaml(path, doc)
    common.emit({"kept": len(kept), "dropped": len(dropped),
                 "archived": str(archived) if archived else None}, args.format)
    return 0


def _archive(args, dropped):
    """Write what left to paths.archive — the write-only maintainer sink. A dropped
    observation was still evidence; it just was not evidence twice."""
    rel = (common.config_doc(args.config).get("paths") or {}).get("archive")
    if not rel:
        return None
    out_dir = Path(common.project_root(args.config)) / rel / "observations"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = common.now().replace(":", "").replace("-", "")
    path = out_dir / f"{stamp}__observations.yaml"
    common.write_yaml(path, {"version": 1, "dropped_at": common.now(),
                             "observations": dropped})
    return path


COMMANDS = {
    ("observations", "age"): _age,
}
