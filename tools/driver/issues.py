"""Design issues — the channel work takes back into the next cut.

The host ``paths.design_issues`` file is the authority on what is open: build and
review agents raise one inside their worktree (the driver promotes it), and the
driver itself raises one for a failure it detected mechanically (a red gate, a
conflicted merge, a task that blocked). Every open entry is mirrored into the run
state so the scheduler parks the task it names.

Nothing is dispatched from here. The design role reads the open entries when it
grounds the next cut and drains each one by authoring the task that answers it,
naming that successor on the entry — which is also how a blocked attempt's branch
finds its way into the successor's worktree (``salvage``).
"""
from __future__ import annotations

import re
import threading

import progress
import yaml

_ID_RE = re.compile(r"(\d+)$")
# The task branch a driver-raised block names in its summary — the successor task's
# worktree is cut from it when the design role's resolution names that successor.
_BRANCH_RE = re.compile(r"\btask/[\w.\-/]+")
# Parallel task threads promote their own issues into the one host file: the
# read-modify-write below is serialized so the second promotion cannot lose the first.
_LOCK = threading.Lock()


def _read(path):
    if not path or not path.exists():
        return {}
    try:
        doc = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError:
        return {}
    return doc if isinstance(doc, dict) else {}


def _entries(doc):
    return [e for e in (doc.get("issues") or []) if isinstance(e, dict)]


def open_entries(rt) -> list:
    """Every open entry in the host design-issues file, in file order."""
    return [e for e in _entries(_read(rt.cfg.path_opt("design_issues")))
            if str(e.get("status") or "open") == "open"]


def entry(rt, di_id: str):
    for item in _entries(_read(rt.cfg.path_opt("design_issues"))):
        if str(item.get("id")) == str(di_id):
            return item
    return None


def _write(rt, doc) -> None:
    path = rt.cfg.path("design_issues")
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = yaml.safe_dump(doc, sort_keys=False, default_flow_style=False,
                              allow_unicode=True)
    if path.exists() and path.read_text() == rendered:
        return
    path.write_text(rendered)


def _next_id(doc) -> str:
    highest = 0
    for item in _entries(doc):
        m = _ID_RE.search(str(item.get("id") or ""))
        if m:
            highest = max(highest, int(m.group(1)))
    return f"DI-{highest + 1}"


def record(rt, summary: str, *, task_id=None, severity: str = "high",
           scope: str = "") -> str:
    """Raise a design issue the driver detected itself (a red gate, a conflicted
    merge). Writes the host entry AND the run-state twin, and returns its id."""
    with _LOCK:
        doc = _read(rt.cfg.path_opt("design_issues")) or {}
        di_id = _next_id(doc)
        item = {"id": di_id, "task_id": task_id, "severity": severity,
                "status": "open", "raised_by": "wf-driver", "summary": summary}
        if scope:
            item["scope"] = scope
        doc.setdefault("issues", []).append(item)
        _write(rt, doc)
    rt.report.line(f"design issue {di_id} raised: {summary[:160]}",
                   symbol=progress.BAD, indent=1)
    mirror(rt, item)
    return di_id


def promote(rt, worktree, di_id: str, task_id: str) -> str:
    """Copy the entry a build/review agent raised in its worktree into the host file
    — the repair role reads only the host file — and mirror it into the run state."""
    rel = rt.cfg.rel("design_issues")
    with _LOCK:
        source = _read(worktree / rel) if rel else {}
        item = next((e for e in _entries(source) if str(e.get("id")) == str(di_id)), None)
        if item is None:
            item = {"id": di_id, "task_id": task_id, "severity": "high",
                    "status": "open",
                    "summary": f"raised in {worktree} with no readable entry"}
        doc = _read(rt.cfg.path_opt("design_issues")) or {}
        if not any(str(e.get("id")) == str(di_id) for e in _entries(doc)):
            doc.setdefault("issues", []).append(item)
            _write(rt, doc)
    mirror(rt, item)
    return str(item.get("id"))


def mirror(rt, item: dict) -> None:
    """Record the issue in the run state, which parks the task it names."""
    args = ["pipeline", "record-design-issue", str(item.get("id")),
            "--severity", str(item.get("severity") or "high")]
    if item.get("task_id"):
        args += ["--task", str(item["task_id"])]
    if item.get("fix_kind"):
        args += ["--fix_kind", str(item["fix_kind"])]
    rt.cli.mutate(*args)


def salvage(rt, task_id: str):
    """``(branch, reason)`` when this task succeeds a blocked one, else ``None``.

    A block writes an issue naming the branch its attempts are on; the design role
    drains that issue by authoring the successor and naming it (``task:``). Those two
    facts together are the whole mapping — the driver cuts the successor's worktree
    from the old branch and hands the build the reason the last attempt failed, so its
    Red phase does not mistake already-passing code for a vacuous test."""
    for item in _entries(_read(rt.cfg.path_opt("design_issues"))):
        if str(item.get("task") or "") != str(task_id):
            continue
        if str(item.get("status") or "") != "resolved":
            continue
        found = _BRANCH_RE.search(str(item.get("summary") or ""))
        if found:
            return found.group(0), str(item.get("summary"))
    return None


def close_resolved(rt) -> list:
    """Close the run-state twin of every host issue the design role has resolved. The
    role writes only the host file, and the twin is what parks the task the issue names
    — a twin left open keeps that task out of every later frontier."""
    closed = []
    res = rt.cli.read("pipeline", "unresolved-design-issues")
    for item in (res.data.get("issues") or []):
        if not isinstance(item, dict) or not item.get("di_id"):
            continue
        twin = str(item["di_id"])
        host = entry(rt, twin)
        if host is not None and str(host.get("status")) == "resolved":
            rt.cli.mutate("pipeline", "resolve-design-issue", twin)
            closed.append(twin)
    return closed
