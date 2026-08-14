"""wf drain — the completion gate and the close-time drain of the durable work-set.

Shipped-ness is never stored: ``capability-complete`` derives it from the ``[SYS-TC:id]``
tags in the test tree and set-differences that against the scenario set authored under
each capability and learning, naming the entries whose whole set has landed.

A drain is keyed on proof, never on a role's prose. A learning drains at
``complete-sprint`` — the sprint served it and every task covering it merged. A capability
drains only through ``drain-capability``, on an ``adequate`` full-promise adequacy digest
read off disk; any other verdict mutates nothing, and an inadequate one carries its
residual paths onto the capability's ``notes:`` (``append-residuals``) for the next cut.

Every write to a durable file is a TEXT edit: round-tripping through PyYAML would strip
the header comments these files carry and normalise their unicode (L-106).

The verbs are exposed under the ``pipeline`` noun — ``pipeline`` merges this command
table into its own.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import archive
import common
import mdread
import runstate

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "reconcile"))
from reconcile import DEFAULT_TEST_GLOBS, harvest  # noqa: E402


# ── The completion gate ──────────────────────────────────────────────────────
#
# A capability's (or learning's) SYS-TC set is its proof obligation, authored beside the
# promise. Shipped-ness is NEVER stored: it derives from the [SYS-TC:id] tags in the test
# tree. `capability-complete` set-differences the two and names the entries whose whole
# set has landed — the mechanical trigger the driver dispatches adequacy gate 2 on.

_WORKSET_FILES = (("capabilities", "capability"), ("learnings", "learning"))


def _test_dirs(args):
    """The configured test-tree roots that exist — paths.tests is a LIST because a
    polyglot repo's tests are not one directory."""
    cfg_tests = (common.config_doc(args.config).get("paths") or {}).get("tests")
    roots = [cfg_tests] if isinstance(cfg_tests, str) else list(cfg_tests or [])
    root_dir = common.project_root(args.config)
    return [d for d in (root_dir / r for r in roots) if d.is_dir()]


def _shipped_ids(args):
    """The SYS-TC ids the test tree provably carries, read off its tags."""
    dirs = _test_dirs(args)
    return set(harvest(dirs, DEFAULT_TEST_GLOBS)) if dirs else set()


def _workset_entries(args, paths):
    """[(kind, entry)] over paths.capabilities and paths.learnings — the open work-set."""
    out = []
    for key, kind in _WORKSET_FILES:
        if not paths.get(key):
            continue
        doc = common.load_yaml(common.resolve_path(args.config, key, None), optional=True)
        for value in (doc.values() if isinstance(doc, dict) else []):
            if isinstance(value, list):
                out += [(kind, e) for e in value if isinstance(e, dict) and e.get("id")]
    return out


def _scenario_ids(entry):
    """The SYS-TC ids nested under one work-set entry. A cross-cutting scenario is
    duplicated under every entry it proves, keeping the same id — so one tag satisfies
    all of them."""
    ids = [str(st["id"]) for st in (entry.get("system_tests") or [])
           if isinstance(st, dict) and st.get("id")]
    return list(dict.fromkeys(ids))


def _capability_complete(rest):
    """Name the work-set entries whose whole scenario set has shipped — the mechanical
    trigger for adequacy gate 2. Pure set-difference, no judgment. An entry carrying no
    set is not a candidate — a capability with none has simply not been taken up, and a
    learning's proof is its tasks' own criteria plus the merge record.

    Every row carries its ``kind``, and the caller must honour it: only a capability is
    an adequacy candidate. A learning may carry a set and is reported here on the same
    footing, but that set never gates its drain — that happens at sprint close off the
    merge record."""
    args = common.base_parser("pipeline capability-complete").parse_args(rest)
    paths = common.config_doc(args.config).get("paths") or {}
    shipped = _shipped_ids(args)

    complete, pending = [], []
    for kind, entry in _workset_entries(args, paths):
        wanted = _scenario_ids(entry)
        if not wanted:
            continue
        missing = [sid for sid in wanted if sid not in shipped]
        row = {"id": str(entry["id"]), "kind": kind, "system_tests": wanted,
               "missing": missing}
        (pending if missing else complete).append(row)

    common.emit({
        "shipped": sorted(shipped),
        "complete": sorted(complete, key=lambda r: r["id"]),
        "pending": sorted(pending, key=lambda r: r["id"]),
    }, args.format)
    return 0


# ── The close-time drain ─────────────────────────────────────────────────────
#
# Two signals close a sprint's working set. What its stages set out to serve (accumulated
# at every load-stage, since each stage artifact is archived and deleted the moment it
# merges) and the merge record — which tasks completed, and what they cover. A learning
# drains when every task covering it merged. A capability does NOT drain here: that takes
# the adequacy judgment, which arrives as its own artifact and drives `drain-capability`.

_LIST_ITEM_RE = re.compile(r"^(\s*)-\s")
_ENTRY_ID_RE = re.compile(r"^\s*(?:-\s+)?id:\s*[\"']?([\w.-]+)")
_ADEQUACY_HEAD_RE = re.compile(r"^#\s*Adequacy:\s*(CAP-\d+)\s*[—\-–:]+\s*(\w+)", re.MULTILINE)
_QUESTION_LINE_RE = re.compile(r"^\*\*Question:\*\*\s*(.*)$", re.MULTILINE)
# An adequacy digest's residual lines, its stamp, and the notes field they append to.
_RESIDUAL_LINE_RE = re.compile(r"^\s*[-*]\s+(.*\bRESIDUAL\b.*)$", re.MULTILINE)
_DIGEST_STAMP_RE = re.compile(r"-(\d{8}T\d{6}Z)\.md$")
_DIGEST_DATE_RE = re.compile(r"^\*\*Date:\*\*\s*([^\s*]+)", re.MULTILINE)
_NOTES_RE = re.compile(r"^(\s*)notes:(.*)$")
_QUOTES = "\"'"
# The two questions wf-adequacy stamps into a digest filename: the whole promise at
# completion, and the proposed scenario set at authoring.
_FULL_PROMISE = "full-promise"
_PROPOSED_SET = "proposed-set"


def _sprint_tasks(doc, stage_doc):
    """{task_id: covers} for every task the sprint has run. A stage artifact dies at its
    own merge, so the run state — where load-stage recorded each task's `covers` — is what
    spans the PR; the stage still on disk is folded in on top."""
    out = {tid: [str(c) for c in ((ts or {}).get("covers") or [])]
           for tid, ts in (doc.get("task_states") or {}).items()}
    for task in runstate.stage_tasks(stage_doc):
        out[task["id"]] = runstate.covers_of(task)
    return out


def _completed_tasks(doc, stage_doc):
    """{task_id: covers} for the subset that merged — the sprint's merge record."""
    task_states = doc.get("task_states") or {}
    return {tid: covers for tid, covers in _sprint_tasks(doc, stage_doc).items()
            if runstate.effective_status(tid, None, task_states) in runstate.COMPLETED}


def _stage_serves(doc, stage_doc):
    """Every CAP/L id the sprint's stages set out to serve, in the order they were cut."""
    served = [str(s) for s in (doc.get("serves") or [])]
    return served + [s for s in runstate.serves_of(stage_doc) if s not in served]


def _superseded_sys_tc_survivors(args, stage_doc):
    """Every SYS-TC id the stage's `supersessions:` retires that is STILL tagged in the
    test tree — the build was required to remove it; a survivor is a finding for the next
    cut. Retiring a shipped scenario is above the escalation gate, so this only ever fires
    on a stage cut after a human ruled on one."""
    ids = list(dict.fromkeys(
        re.findall(r"\bSYS-TC-\d+\b", str(stage_doc.get("supersessions") or ""))))
    dirs = _test_dirs(args) if ids else []
    if not dirs:
        return []
    harvested = harvest(dirs, DEFAULT_TEST_GLOBS)
    return [{"id": rid, "files": sorted(set(harvested[rid]["files"]))}
            for rid in ids if rid in harvested]


def _drop_yaml_entries(text, ids):
    """Remove the list entries whose ``id:`` is in ``ids``, editing the file as TEXT.
    Round-tripping through PyYAML would strip the header comments these durable files
    carry and normalise their unicode, so the drain rewrites lines instead (L-106).
    Returns (new_text, dropped_ids)."""
    lines = text.splitlines(keepends=True)
    out, dropped, i = [], [], 0
    while i < len(lines):
        m = _LIST_ITEM_RE.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        indent, block, pending, j = len(m.group(1)), [lines[i]], [], i + 1
        while j < len(lines):
            nxt = lines[j]
            if not nxt.strip():
                pending.append(nxt)
                j += 1
                continue
            if len(nxt) - len(nxt.lstrip()) <= indent:
                break
            block.extend(pending)
            pending = []
            block.append(nxt)
            j += 1
        entry_id = next((mm.group(1) for mm in
                         (_ENTRY_ID_RE.match(b) for b in block) if mm), None)
        if entry_id in ids:
            dropped.append(entry_id)
        else:
            out.extend(block)
        out.extend(pending)
        i = j
    return "".join(out), dropped


def drop_entries(path, ids):
    """Drop ``ids`` from a YAML list file in place; returns the ids actually removed.
    A no-op leaves the file untouched (L-106)."""
    text = path.read_text()
    new_text, dropped = _drop_yaml_entries(text, set(ids))
    if dropped and new_text != text:
        path.write_text(new_text)
    return dropped


def _drain_working_set(args, doc, paths):
    """Run the close-time drain; returns the drain summary."""
    stage_doc = runstate.stage_doc(args, paths)
    tasks = _sprint_tasks(doc, stage_doc)
    completed = _completed_tasks(doc, stage_doc)
    served = _stage_serves(doc, stage_doc)

    # A learning drains when the sprint served it AND every task covering it merged.
    # Partial ship is not proof, and a served id no task covered was never built.
    drained_learnings, retained = [], []
    for ident in served:
        if not ident.startswith("L-"):
            continue
        owners = sorted(tid for tid, covers in tasks.items() if ident in covers)
        unmerged = [tid for tid in owners if tid not in completed]
        if not owners:
            retained.append({"id": ident, "reason": "no task covered it"})
        elif unmerged:
            retained.append({"id": ident,
                             "reason": "unmerged tasks: " + ", ".join(unmerged)})
        else:
            drained_learnings.append(ident)
    if drained_learnings and paths.get("learnings"):
        lp = common.resolve_path(args.config, "learnings", None)
        drained_learnings = drop_entries(lp, drained_learnings) if lp.exists() else []

    return {
        "served": served,
        "merged_tasks": sorted(completed),
        "learnings_drained": drained_learnings,
        "learnings_retained": retained,
        "superseded_survivors": _superseded_sys_tc_survivors(args, stage_doc),
    }


def _append_drain_report(args, paths, sprint_id, drain):
    if not paths.get("drain_report") or not any(drain.values()):
        return None
    rp = common.resolve_path(args.config, "drain_report", None)
    doc = common.load_yaml(rp, optional=True)
    doc.setdefault("reports", []).append(
        {"sprint_id": sprint_id, "closed_at": common.now(), **drain})
    common.write_yaml(rp, doc)
    return str(rp)


def _drain_capability(rest):
    """Drain one capability on an adequacy verdict. The verdict is READ FROM THE DIGEST
    wf-adequacy left on disk — never from an agent's prose — and only ``adequate`` drains:
    the capability is snapshotted into paths.archive and removed from paths.capabilities.
    Any other verdict mutates nothing and exits 1.

    Only a FULL-PROMISE digest can drain. The proposed-set review judges a scenario set at
    authoring time, months before anything ships, so it can never answer whether the whole
    promise is proven; consuming one here would drain on a question nobody asked."""
    p = common.base_parser("pipeline drain-capability")
    p.add_argument("capability")
    p.add_argument("--verdict", help="path to the adequacy digest (default: the newest "
                                     "adequacy-<cap>-full-promise-*.md under "
                                     "paths.drill_cache)")
    args = p.parse_args(rest)

    path = Path(args.verdict) if args.verdict else _newest_adequacy_digest(args)
    if path is None or not path.is_file():
        msg = (f"no full-promise adequacy digest for {args.capability}"
               + (f" at {path}" if path else " under paths.drill_cache"))
        # A digest whose name carries neither question marker cannot be assumed to answer
        # the whole promise, so it is right that the glob passes it over — but silently
        # passing it over leaves a capability undrained against a verdict that is on disk,
        # with nothing on screen to say the file is even there.
        unstamped = _unstamped_digests(args)
        if unstamped:
            msg += ("; " + ", ".join(p.name for p in unstamped)
                    + " name no question — pass --verdict <path> to drain on one")
        common.die(msg)
    text = path.read_text(errors="replace")
    if _is_proposed_set(path, text):
        common.die(f"{path} answers the proposed-set question — only a full-promise "
                   f"review can drain {args.capability}")
    head = _ADEQUACY_HEAD_RE.search(text)
    if not head:
        common.die(f"{path} carries no '# Adequacy: <CAP-id> — <verdict>' heading")
    reviewed, verdict = head.group(1), head.group(2).lower()
    if reviewed != args.capability:
        common.die(f"{path} reviews {reviewed}, not {args.capability}")

    result = {"capability": args.capability, "verdict": verdict, "digest": str(path)}
    if verdict != "adequate":
        result["drained"] = False
        common.emit(result, args.format)
        return 1

    cap_path = common.resolve_path(args.config, "capabilities", None)
    if not cap_path.exists():
        common.die(f"capabilities file not found: {cap_path}")
    paths = common.config_doc(args.config).get("paths") or {}
    if paths.get("archive"):
        root = common.resolve_path(args.config, "archive", None)
        result["archived"] = str(archive.snapshot(root, "capabilities", cap_path, move=False))
    dropped = drop_entries(cap_path, [args.capability])
    if not dropped:
        common.die(f"{args.capability} is not open in {cap_path}")
    result["drained"] = True
    common.emit(result, args.format)
    return 0


def _append_residuals(rest):
    """Carry one inadequate adequacy review's residuals onto the capability's ``notes:``.
    Each residual is a path inside the promise that no scenario reaches; parked on the
    capability, it is what the next cut reads.

    The edit is TEXT, like every other write to a durable file: comments, ordering and
    unicode survive (L-106). It is idempotent per digest — the appended block is stamped
    with the digest's own timestamp, and a digest already carried over appends nothing."""
    p = common.base_parser("pipeline append-residuals")
    p.add_argument("capability")
    p.add_argument("--digest", help="path to the adequacy digest (default: the newest "
                                    "adequacy-<cap>-full-promise-*.md under "
                                    "paths.drill_cache)")
    args = p.parse_args(rest)

    path = Path(args.digest) if args.digest else _newest_adequacy_digest(args)
    if path is None or not path.is_file():
        common.die(f"no adequacy digest for {args.capability}"
                   + (f" at {path}" if path else " under paths.drill_cache"))
    text = path.read_text(errors="replace")
    head = _ADEQUACY_HEAD_RE.search(text)
    if not head:
        common.die(f"{path} carries no '# Adequacy: <CAP-id> — <verdict>' heading")
    if head.group(1) != args.capability:
        common.die(f"{path} reviews {head.group(1)}, not {args.capability}")

    cap_path = common.resolve_path(args.config, "capabilities", None)
    if not cap_path.exists():
        common.die(f"capabilities file not found: {cap_path}")
    residuals = _digest_residuals(text)
    result = {"capability": args.capability, "digest": str(path),
              "verdict": head.group(2).lower(), "residuals": len(residuals)}
    if not residuals:
        result["appended"] = False
        common.emit(result, args.format)
        return 0

    stamp = _digest_stamp(path, text)
    block = [f"[adequacy {stamp}] residuals from the {head.group(2).lower()} review:"]
    block += [f"- {line}" for line in residuals]
    original = cap_path.read_text()
    new_text = _append_notes(original, args.capability, block, marker=f"[adequacy {stamp}]")
    if new_text is None:
        common.die(f"{args.capability} is not open in {cap_path}")
    if new_text == original:
        result["appended"] = False
        common.emit(result, args.format)
        return 0
    import yaml as _yaml
    try:
        _yaml.safe_load(new_text)
    except _yaml.YAMLError as exc:
        common.die(f"appending to {args.capability} would not parse: {exc}")
    cap_path.write_text(new_text)
    result["appended"] = True
    common.emit(result, args.format)
    return 0


def _digest_residuals(text) -> list:
    """The residual lines a digest reports: its own ``## Residuals`` section when it has
    one, else every bullet on the falsifying-paths list flagged RESIDUAL."""
    body = mdread.section(text, "Residuals")
    lines = [ln.strip().lstrip("-*").strip()
             for ln in body.splitlines() if ln.strip().startswith(("-", "*"))]
    if lines:
        return lines
    return [m.group(1).strip() for m in _RESIDUAL_LINE_RE.finditer(text)]


def _digest_stamp(path, text) -> str:
    """The digest's own timestamp — from the filename wf-adequacy stamps, else its
    ``**Date:**`` line. It keys the idempotency check, so it must not be "now"."""
    m = _DIGEST_STAMP_RE.search(path.name)
    if m:
        return m.group(1)
    m = _DIGEST_DATE_RE.search(text)
    return m.group(1).strip() if m else common.now()


def _entry_blocks(lines):
    """[(start, end, indent)] for every list entry in a YAML list file."""
    out, i = [], 0
    while i < len(lines):
        m = _LIST_ITEM_RE.match(lines[i])
        if not m:
            i += 1
            continue
        indent, j = len(m.group(1)), i + 1
        while j < len(lines):
            nxt = lines[j]
            if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= indent:
                break
            j += 1
        out.append((i, j, indent))
        i = j
    return out


def _append_notes(text, entry_id, block, marker):
    """Append ``block`` to one entry's ``notes:`` as TEXT. Returns the new text, the
    text unchanged when ``marker`` is already in the entry, or None when no such entry
    exists. A missing or empty ``notes:`` becomes a literal block; an existing one is
    extended in place."""
    lines = text.splitlines(keepends=True)
    for start, end, indent in _entry_blocks(lines):
        ident = next((m.group(1) for m in
                      (_ENTRY_ID_RE.match(b) for b in lines[start:end]) if m), None)
        if ident != entry_id:
            continue
        if any(marker in ln for ln in lines[start:end]):
            return text
        field_indent = " " * (indent + 2)
        note_at = next((i for i in range(start, end)
                        if _NOTES_RE.match(lines[i])), None)
        if note_at is None:
            body = [f"{field_indent}notes: |\n"]
            body += [f"{field_indent}  {ln}\n" for ln in block]
            return "".join(lines[:start + 1] + body + lines[start + 1:])

        head = _NOTES_RE.match(lines[note_at])
        value = head.group(2).strip()
        # an existing block sets the body indent; a new one is two past the key
        first_body = next((ln for ln in lines[note_at + 1:end]
                           if ln.strip() and ln.startswith(head.group(1) + " ")), None)
        body_indent = (first_body[:len(first_body) - len(first_body.lstrip())]
                       if value and first_body else f"{head.group(1)}  ")
        tail = note_at + 1
        while tail < end and (not lines[tail].strip()
                              or lines[tail].startswith(body_indent)):
            tail += 1
        while tail > note_at + 1 and not lines[tail - 1].strip():
            tail -= 1                      # keep trailing blank lines below the block
        prefix = list(lines[:note_at])
        if value in ("", "|", "|-", "|+", ">", ">-"):
            kept = lines[note_at + 1:tail] if value else []
            prefix += [f"{head.group(1)}notes: |\n"] + list(kept)
        else:
            # a one-line scalar: keep its text as the block's first line
            kept_value = value.strip(_QUOTES)
            prefix += [f"{head.group(1)}notes: |\n",
                       f"{body_indent}{kept_value}\n"]
        body = [f"{body_indent}{ln}\n" for ln in block]
        return "".join(prefix + body + lines[tail:])
    return None


def _newest_adequacy_digest(args):
    """The most recent ``adequacy-<cap>-full-promise-<utc>.md`` under paths.drill_cache
    — the stamped filename orders them, so the latest full-promise review wins. A
    proposed-set digest is never a candidate, however recent it is."""
    rel = (common.config_doc(args.config).get("paths") or {}).get("drill_cache")
    if not rel:
        return None
    cache = (common.project_root(args.config) / rel).resolve()
    pattern = f"adequacy-{args.capability}-{_FULL_PROMISE}-*.md"
    hits = sorted(cache.glob(pattern)) if cache.is_dir() else []
    return hits[-1] if hits else None


def _unstamped_digests(args):
    """This capability's adequacy digests whose names carry neither question marker —
    written before the naming split, so no glob can tell which question they answer."""
    rel = (common.config_doc(args.config).get("paths") or {}).get("drill_cache")
    if not rel:
        return []
    cache = (common.project_root(args.config) / rel).resolve()
    if not cache.is_dir():
        return []
    return sorted(p for p in cache.glob(f"adequacy-{args.capability}-*.md")
                  if _FULL_PROMISE not in p.name and _PROPOSED_SET not in p.name)


def _is_proposed_set(path, text):
    """True when the digest itself says it answers the proposed-set question — named in
    the filename wf-adequacy stamps, or on its ``**Question:**`` line. Guards the explicit
    ``--verdict`` path, which no filename glob filters."""
    if _PROPOSED_SET in path.name:
        return True
    line = _QUESTION_LINE_RE.search(text)
    return bool(line and "proposed" in line.group(1).lower())


def _complete_sprint(rest):
    """Close the PR packaging unit and reset the pipeline for the next one. Run during
    ship, before the push, so its archive snapshots and working-set trims commit into the
    PR.

    The drain: from what the sprint's stages served and the merge record, drain every
    learning whose covering tasks all merged, sweep the surviving stage's superseded
    SYS-TC ids for tags that should be gone, and append the drain report
    (paths.drain_report) the ship step folds into the PR body. Capabilities are NOT
    drained here — only an adequacy verdict drains one (`drain-capability`).

    Each stage artifact already archived and drained at its own merge, so there is none
    left to sweep. When paths.archive is set, snapshot learnings + capabilities
    (pre-drain), the design issues, and the final run state. The design issues are COPIED,
    never moved: an issue still open at the PR boundary is the channel a blocked task
    re-enters the next cut through. Then reset pipeline_state to a bare ``idle``.

    Git is intentionally NOT touched — a pure file/state mutation."""
    args = common.base_parser("pipeline complete-sprint").parse_args(rest)

    doc = runstate.load_state(args)
    sprint_id = doc.get("sprint_id") or "sprint"
    paths = common.config_doc(args.config).get("paths") or {}
    root = common.resolve_path(args.config, "archive", None) if paths.get("archive") else None

    archived = {}
    if root:
        # Pre-drain snapshots: the archive keeps the durable files as the sprint left them.
        for key in ("learnings", "capabilities"):
            if paths.get(key):
                p = common.resolve_path(args.config, key, None)
                if p.exists():
                    archived[key] = str(archive.snapshot(root, sprint_id, p, move=False))

    drain = _drain_working_set(args, doc, paths)
    report_path = _append_drain_report(args, paths, sprint_id, drain)

    if root:
        for key in ("design_issues", "pipeline_state"):
            if paths.get(key):
                p = common.resolve_path(args.config, key, None)
                if p.exists():
                    archived[key] = str(archive.snapshot(root, sprint_id, p, move=False))

    runstate.save_state(args, {"current_phase": "idle"})
    common.emit({
        "sprint_id": sprint_id,
        "archived": archived,
        "drain": drain,
        "drain_report": report_path,
        "pipeline_state_reset": str(runstate.state_path(args)),
    }, args.format)
    return 0


COMMANDS = {
    ("pipeline", "capability-complete"): _capability_complete,
    ("pipeline", "drain-capability"): _drain_capability,
    ("pipeline", "append-residuals"): _append_residuals,
    ("pipeline", "complete-sprint"): _complete_sprint,
}
