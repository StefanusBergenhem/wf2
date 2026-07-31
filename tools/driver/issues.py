"""Design issues — the repair ladder's plumbing.

The host ``paths.design_issues`` file is the authority on what is open: build and
review agents raise one inside their worktree (the driver promotes it), the Tech
Lead raises a slice-scoped one straight into it, and the driver itself raises one
for a failure it detected mechanically (a red gate, a merge conflict). Every open
entry is mirrored into the run state so the scheduler parks the task it names, and
resolution routes through the design role's repair mode.
"""
from __future__ import annotations

import re
import threading

import yaml
from runtime import Halt, Pause

_ID_RE = re.compile(r"(\d+)$")
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


def repair(rt, item: dict) -> str:
    """Dispatch the design role's repair mode for one issue and route on the entry it
    leaves behind. Returns the fix_kind it recorded (may be empty)."""
    di_id = str(item.get("id"))
    sprint_path = rt.cfg.path_opt("sprint")
    params = {"Mode": "repair", "di_id": di_id}
    if sprint_path and sprint_path.exists():
        params["sprint_artifact"] = str(sprint_path)
    if item.get("task_id"):
        params["task_id"] = str(item["task_id"])
    rt.tele.event("repair", role="wf-designer", mode="repair", di_id=di_id,
                  task_id=item.get("task_id"))
    rt.agents.launch("wf-designer", params, mode="repair", task_id=item.get("task_id"))

    decision_prep = rt.cfg.path_opt("decision_prep")
    if decision_prep and decision_prep.exists():
        raise Pause("escalation", f"the design role escalated {di_id} to a ruling")

    after = entry(rt, di_id)
    if after is None:
        raise Halt("design_issue_missing",
                   f"{di_id} is no longer in {rt.cfg.path('design_issues')}")
    if str(after.get("status")) != "resolved":
        raise Halt("design_issue_unresolved",
                   f"{di_id} came back {after.get('status')} — a human ruling is needed")
    rt.cli.mutate("pipeline", "resolve-design-issue", di_id)
    return str(after.get("fix_kind") or "")


def is_slice_scoped(item: dict) -> bool:
    """A slice defect names no task (the increment itself cannot be built as
    allocated), or says so outright."""
    return (str(item.get("scope") or "") == "slice"
            or str(item.get("fix_kind") or "") == "slice_recut"
            or not item.get("task_id"))
