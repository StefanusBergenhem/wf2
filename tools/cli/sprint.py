"""wf sprint — read-side helpers over the sprint's task contracts (sprint.yaml).

The file is CUMULATIVE across the sprint: each increment's Tech Lead appends its
tasks (every task carries its ``increment:``) to what earlier increments left, so
the merge record at sprint close sees the whole sprint. ``sprint check`` therefore
validates the whole file; only ``pipeline compute-stages --increment N`` scopes to
one increment.

``sprint task`` builds ONE task's build envelope in the worktree: the increment's
slice section (the stage narrative), then covers → story → acceptance → boundaries
→ grounding, plus the task's title and the inlined SYS-TC text on an e2e task.
Nothing else travels — a field the build agent cannot act on is pure context cost.

``sprint materialize`` inlines the slice's verbatim SYS-TC text into the sprint:
the TL authors bare ids and this fills in the scenario and its covered
capabilities, so mechanical copying stays out of the LLM's output budget.
Idempotent; re-run after any sprint edit, before ``sprint check``.

``sprint prune --increment N`` removes ONE increment's tasks (and their run state)
after a slice re-cut invalidated them. Every other increment's entries stay
byte-for-byte, and an increment whose tasks already merged is refused.

``sprint check`` is the contract gate: a mechanical linter over the four-section
contract schema (story / acceptance / boundaries / grounding) and the task DAG.
It verifies the STRUCTURE of the criterion → test trace and the pointers into
source, never their meaning. Exits non-zero on any error-severity finding.
"""
from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

import common
import pipeline
import slice as slice_checks

_DRIVER_ID_RE = re.compile(r"\b(?:CAP|L)-\d+\b")
_SLICE_TC_RE = re.compile(r"\b(SYS-TC-\d+)\b")

# A grounding token that names a file (deliberately conservative): an optional path
# prefix and a lower-case, letter-led extension. An upper-case-led final segment
# (uuid.UUID, ElementRepository.List) is a qualified symbol, not a file path.
_PATH_TOKEN_RE = re.compile(r"^[\w./-]*[\w-]\.[a-z][a-z0-9]{0,4}$")
_TOKEN_SKIP = {"e.g", "i.e"}

# The four sections the contract schema retired: each fact now lives in exactly one
# place, so a resurrected field is drift waiting to happen.
_RETIRED_BOUNDARY_FIELDS = ("out_of_scope", "read_only", "read_only_files",
                            "fixed_interfaces", "interface_contract",
                            "interface_contract_ref")
_RETIRED_SPEC_FIELDS = ("requirements", "acceptance_criteria", "implementation_notes",
                        "serves", "files_to_touch", "testing_mandate")

_PLACEHOLDER_RE = re.compile(r"\b(TODO|FIXME|TBD)\b", re.IGNORECASE)
_STORY_MIN_WORDS = 20


def _covers_of(item):
    """A `covers` field may be a scalar or a list; normalise to a list of strings."""
    c = item.get("covers")
    if c is None:
        return []
    return [str(x) for x in (c if isinstance(c, list) else [c])]


def _grounding_pointers(entry):
    """(file tokens, [(file, symbol)]) named by one grounding entry. A `<path>:<n>`
    line reference contributes its file only — a digit suffix is a line, not a symbol."""
    files, symbols = [], []
    for raw in re.split(r"[\s,;()\[\]{}<>]+", str(entry)):
        stripped = raw.strip("`'\"*").rstrip(".,!?")
        if not stripped:
            continue
        head, _, suffix = stripped.partition(":")
        head = head.strip("`'\"*")
        if head.lower() in _TOKEN_SKIP or not _PATH_TOKEN_RE.match(head):
            continue
        files.append(head)
        if suffix and re.fullmatch(r"[A-Za-z_]\w*", suffix):
            symbols.append((head, suffix))
    return files, symbols


def _pointer_findings(root, tid, grounding):
    """C11: grounding is pointers into code that already exists. A path that resolves
    to no file, or a `<path>:<symbol>` whose file carries no such symbol, is a claim
    asserted rather than checked."""
    out = []
    for item in grounding:
        if isinstance(item, dict):
            continue  # dependency_commits and friends — C12's territory
        files, symbols = _grounding_pointers(item)
        for token in files:
            if not (root / token).is_file():
                out.append(f"{tid}: grounding cites '{token}', which is not a file in the "
                           f"repo — point at real source or drop the pointer")
        for token, symbol in symbols:
            target = root / token
            if target.is_file() and symbol not in target.read_text(errors="replace"):
                out.append(f"{tid}: grounding cites {token}:{symbol}, but {token} carries "
                           f"no '{symbol}' — verify the claim against the source")
    return out


def _declared_dependency_commits(entry):
    """{task id: sha} the contract itself declares under grounding."""
    out = {}
    for item in entry.get("grounding") or []:
        if isinstance(item, dict) and isinstance(item.get("dependency_commits"), dict):
            out.update(item["dependency_commits"])
    return out


def _merged_dependency_commits(config, entry):
    """{dep task id: merge_commit} for the entry's already-merged depends_on tasks,
    read from paths.pipeline_state — injected into the envelope so the build agent
    recovers a dependency's pattern from its merge diff (`git show <sha>`) instead of
    exploring the tree. Best-effort: a missing config key, absent state file, or
    unmerged dep simply contributes nothing."""
    deps = entry.get("depends_on") or []
    if not deps:
        return {}
    rel = (common.config_doc(config).get("paths") or {}).get("pipeline_state")
    if not rel:
        return {}
    state_path = (common.project_root(config) / rel).resolve()
    if not state_path.exists():
        return {}
    states = (common.load_yaml(state_path) or {}).get("task_states") or {}
    return {
        d: states[d]["merge_commit"]
        for d in deps
        if isinstance(states.get(d), dict) and states[d].get("merge_commit")
    }


def _slice_path(args):
    """Resolve the design-slice location without dying when the key is unset — an
    absent slice is a warning (A0) at check time, not a hard failure."""
    if getattr(args, "slice", None):
        return Path(args.slice)
    rel = (common.config_doc(args.config).get("paths") or {}).get("design_slice")
    if not rel:
        return None
    return (common.project_root(args.config) / rel).resolve()


# ---------------------------------------------------------------------------
# sprint task — the build envelope
# ---------------------------------------------------------------------------

# The order the build agent reads: where this increment is going, which driver the task
# serves, then what it builds, what proves it, what it may not touch, and what it can
# look at.
_ENVELOPE_ORDER = ["id", "increment", "title", "increment_narrative", "covers", "story",
                   "acceptance", "boundaries", "grounding", "system_tests"]


def _envelope(args, entry, slice_text):
    """The build envelope for one task: the increment's stage narrative, the driver the
    task serves, the four contract sections, and the task's one-line title. Ordering
    metadata (depends_on) stays behind."""
    out = {"id": entry.get("id")}
    number = entry.get("increment")
    if number is not None:
        out["increment"] = number
        narrative = slice_checks.increment_section(slice_text, number)
        if not narrative:
            common.die(f"{entry.get('id')}: the slice declares no '### Increment {number}' "
                       f"block — the build envelope carries the stage narrative")
        out["increment_narrative"] = narrative
    if entry.get("title") is not None:
        out["title"] = entry["title"]
    covers = _covers_of(entry)
    if covers:
        out["covers"] = covers
    for key in ("story", "acceptance", "boundaries"):
        if entry.get(key) is not None:
            out[key] = entry[key]

    grounding = [g for g in (entry.get("grounding") or [])
                 if not (isinstance(g, dict) and "dependency_commits" in g)]
    commits = _merged_dependency_commits(args.config, entry)
    if commits:
        grounding.append({"dependency_commits": commits})
    if grounding:
        out["grounding"] = grounding

    if entry.get("system_tests"):
        out["system_tests"] = entry["system_tests"]
    return {k: out[k] for k in _ENVELOPE_ORDER if k in out}


def _task(rest):
    """Emit (or --write) the build envelope for a single task. --write is an explicit
    path ARGUMENT used as-given (the driver passes the worktree-resolved current_task
    path), never re-anchored on the host config."""
    p = common.base_parser("sprint task")
    p.add_argument("task_id")
    p.add_argument("--slice", help="path to the design-slice (default: paths.design_slice)")
    p.add_argument("--write", help="write the envelope to this path instead of stdout")
    args = p.parse_args(rest)

    sprint = common.load_yaml(common.resolve_path(args.config, "sprint", None))
    tasks = sprint.get("tasks") or []
    entry = next(
        (t for t in tasks if isinstance(t, dict) and t.get("id") == args.task_id), None
    )
    if entry is None:
        common.die(f"task {args.task_id} not found in sprint")

    slice_text = ""
    if entry.get("increment") is not None:
        slice_path = _slice_path(args)
        if slice_path is None or not slice_path.exists():
            common.die(f"design slice not found at {slice_path} — the build envelope "
                       f"carries the increment's stage narrative")
        slice_text = slice_path.read_text()

    envelope = _envelope(args, entry, slice_text)

    if args.write:
        import yaml as _yaml

        out_path = Path(args.write)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            _yaml.safe_dump(envelope, sort_keys=False, default_flow_style=False,
                            allow_unicode=True)
        )
        common.emit({"task_id": args.task_id, "written": str(out_path)}, args.format)
    else:
        common.emit(envelope, args.format)
    return 0


# ---------------------------------------------------------------------------
# sprint materialize — inline the slice's SYS-TC text
# ---------------------------------------------------------------------------

_TC_HEAD_RE = re.compile(r"-\s+\*\*(SYS-TC-\d+):\*\*\s*(.*)")
_GWT_RE = re.compile(r"\*\*(Given|When|Then)\*\*\s*(.*)")

# A description that ends on one of these function words — or a comma — was cut
# mid-sentence: no complete clause ends here. Kept deliberately narrow (articles,
# conjunctions, common prepositions/subordinators) so a real scenario ending in a
# noun or verb is never flagged.
_DANGLING_TAIL = {
    "the", "a", "an", "and", "or", "but", "nor", "of", "to", "with", "for", "in",
    "on", "at", "by", "from", "into", "onto", "upon", "over", "under", "per", "via",
    "than", "as", "because", "that", "which", "while", "when", "where", "if", "so",
}


def _oneline(text):
    return " ".join(str(text).split())


def _truncated(desc):
    """True if a SYS-TC description was cut off mid-sentence — it ends on a comma
    or a dangling function word. Build stamps the description verbatim onto the
    [SYS-TC:] tag, so a truncated one must fail generation, not ship (L-065)."""
    s = (desc or "").rstrip()
    if not s:
        return False
    if s[-1] == ",":
        return True
    words = re.findall(r"[A-Za-z']+", s)
    return bool(words) and words[-1].lower() in _DANGLING_TAIL


def _unwrap_bullets(text):
    """Logical lines: each bullet (and each `**Covers:**` line) joined with the
    wrapped continuation lines beneath it. The case-title and Given/When/Then
    patterns both match to end-of-line, so an author's line break would cut the
    text there and the build would stamp the fragment onto the [SYS-TC:] tag —
    silently, whenever the cut lands on a word `_truncated` does not flag."""
    out = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            out.append("")
            continue
        opens = stripped.startswith("- ") or "**Covers:**" in stripped
        if opens or not out or not out[-1].strip():
            out.append(raw)
        else:
            out[-1] = f"{out[-1].rstrip()} {stripped}"
    return out


def _slice_system_testcases(text):
    """{SYS-TC-id: {description, covers}} from '## System test cases'. The
    description is the case title plus its Given/When/Then scenario, joined to
    one line — the build stamps it verbatim on the [SYS-TC:] tag."""
    out = {}
    cur = None
    for line in _unwrap_bullets(slice_checks.section(text, "System test cases")):
        head = _TC_HEAD_RE.search(line)
        if head:
            cur = {"title": _oneline(head.group(2)), "covers": [], "gwt": []}
            out[head.group(1)] = cur
            continue
        if cur is None:
            continue
        if "**Covers:**" in line:
            cur["covers"] = _DRIVER_ID_RE.findall(line.split("**Covers:**", 1)[1])
            continue
        gwt = _GWT_RE.search(line)
        if gwt:
            cur["gwt"].append(f"{gwt.group(1)} {_oneline(gwt.group(2))}")
    return {
        tc: {
            "description": " — ".join([c["title"], "; ".join(c["gwt"])]) if c["gwt"] else c["title"],
            "covers": c["covers"],
        }
        for tc, c in out.items()
    }


_TASK_FIELD_ORDER = ["id", "increment", "title", "depends_on", "covers",
                     "story", "acceptance", "boundaries", "grounding", "system_tests"]


def _ordered_task(task):
    """Rebuild a task dict in canonical field order (unknown fields keep their
    relative order at the end) so materialize output is stable and diffable."""
    out = {k: task[k] for k in _TASK_FIELD_ORDER if k in task}
    out.update({k: v for k, v in task.items() if k not in out})
    return out


def _system_test_entries(value):
    """Normalise `system_tests` — the TL authors bare ids (`[SYS-TC-44]`), the
    materialized form is a list of entries."""
    out = []
    for item in value or []:
        out.append(dict(item) if isinstance(item, dict) else {"id": str(item)})
    return out


def _materialize(rest):
    p = common.base_parser("sprint materialize")
    p.add_argument("--slice", help="path to the design-slice (default: paths.design_slice)")
    args = p.parse_args(rest)

    sprint_path = common.resolve_path(args.config, "sprint", None)
    sprint = common.load_yaml(sprint_path)
    slice_path = _slice_path(args)
    if slice_path is None or not slice_path.exists():
        common.die(f"design slice not found at {slice_path} — materialize needs the slice")
    text = slice_path.read_text()
    tcs = _slice_system_testcases(text)

    errors, n_tcs = [], 0
    tasks = sprint.get("tasks") or []
    for i, t in enumerate(tasks):
        tid = t.get("id", "<no-id>")
        systests = _system_test_entries(t.get("system_tests"))
        for st in systests:
            info = tcs.get(st.get("id"))
            if info is None:
                errors.append(f"{tid}: system_tests names {st.get('id')}, "
                              f"which is not a case in the slice")
                continue
            st["description"] = info["description"]
            if _truncated(st["description"]):
                errors.append(f"{tid}: system_tests {st.get('id')} description looks "
                              f"truncated (ends '…{st['description'][-40:].strip()}') — "
                              f"complete the case title/scenario in the slice")
            st["covers"] = list(info["covers"])
            n_tcs += 1
        if systests:
            t["system_tests"] = systests
        tasks[i] = _ordered_task(t)

    if errors:
        common.emit({"verdict": "fail", "errors": errors}, args.format)
        return 1

    import yaml as _yaml

    sprint["tasks"] = tasks
    rendered = _yaml.safe_dump(sprint, sort_keys=False, default_flow_style=False,
                               allow_unicode=True)
    if rendered != sprint_path.read_text():
        sprint_path.write_text(rendered)
    common.emit({
        "sprint_id": sprint.get("sprint_id"),
        "tasks": len(tasks),
        "system_tests": n_tcs,
        "verdict": "pass",
    }, args.format)
    return 0


# ---------------------------------------------------------------------------
# sprint prune — drop one increment's tasks
# ---------------------------------------------------------------------------


def _prune(rest):
    """Remove one increment's tasks from the cumulative file, and their entries from
    the run state. The tasks are edited out as TEXT: every other increment's entry
    (and the file's comments and unicode) survives byte-for-byte, and a no-op writes
    nothing (L-106)."""
    p = common.base_parser("sprint prune")
    p.add_argument("--increment", required=True,
                   help="the increment whose tasks a re-cut invalidated")
    args = p.parse_args(rest)

    sprint_path = common.resolve_path(args.config, "sprint", None)
    sprint = common.load_yaml(sprint_path, optional=True)
    tasks = [t for t in (sprint.get("tasks") or []) if isinstance(t, dict) and t.get("id")]
    key = slice_checks.increment_key(args.increment)
    victims = [t["id"] for t in tasks
               if slice_checks.increment_key(t.get("increment")) == key]

    merged = pipeline.merged_task_ids(args, victims)
    if merged:
        common.emit({
            "sprint_id": sprint.get("sprint_id"),
            "increment": args.increment,
            "errors": [f"{tid} has already merged — increment {args.increment} is part of "
                       f"the sprint's merge record and its tasks are never pruned"
                       for tid in merged],
            "verdict": "fail",
        }, args.format)
        return 1

    pruned = pipeline.drop_entries(sprint_path, victims) if victims else []
    common.emit({
        "sprint_id": sprint.get("sprint_id"),
        "increment": args.increment,
        "pruned": pruned,
        "task_states_pruned": pipeline.drop_task_states(args, pruned),
        "tasks": len(tasks) - len(pruned),
        "verdict": "pass",
    }, args.format)
    return 0


# ---------------------------------------------------------------------------
# sprint check — the contract gate
# ---------------------------------------------------------------------------


def _story_findings(tid, story):
    """B8: the story is the first field the build agent reads. A stub there means the
    change has no shape and every later field is read out of context."""
    text = _oneline(story or "")
    if not text:
        return [f"{tid}: no story — the contract opens with what this task builds, why, "
                f"how the change flows through the code, and what is new against what exists"]
    if _PLACEHOLDER_RE.search(text) or (text.startswith("<") and text.endswith(">")):
        return [f"{tid}: story is still a placeholder ('{text[:60]}…')"]
    if len(text.split()) < _STORY_MIN_WORDS:
        return [f"{tid}: story is {len(text.split())} words — under the "
                f"{_STORY_MIN_WORDS}-word floor for a contract's opening narrative"]
    return []


def _target_key(target):
    """(package, name) for a test target. `pkg/file.go:TestX` keys on its directory;
    a bare `TestX` keys on the unqualified bucket. Two contracts landing the same key
    collide at build time in separate worktrees (L-088)."""
    token = str(target).strip()
    path_part, sep, name = token.rpartition(":")
    if not sep:
        return ("", token)
    return (str(PurePosixPath(path_part).parent) if "/" in path_part else "", name)


def _cumulative_findings(tasks):
    """C17: the sprint file accumulates every increment's tasks in build order — each
    increment's Tech Lead appends. A reused task id (pipeline_state keys task state by
    id sprint-wide, so the second one returns pre-completed), an increment block
    positioned after a later increment's, or a task block absorbed into the previous
    task's grounding list is a broken append over already-merged work."""
    out, seen, highest = [], set(), None
    for t in tasks:
        tid = t.get("id")
        for item in (t.get("grounding") or []):
            if not isinstance(item, dict):
                continue
            stray = sorted(k for k in item if k != "dependency_commits")
            if stray:
                out.append(f"{tid}: grounding carries a mapping with {stray} — a task "
                           f"block absorbed into this task's list; append at the same "
                           f"indentation the file already uses")
        if tid is not None:
            if tid in seen:
                out.append(f"task id '{tid}' is used more than once — the sprint file "
                           f"carries every increment's tasks and the run state keys them "
                           f"by id, so a reused id returns pre-completed; give the task a "
                           f"fresh id")
            seen.add(tid)
        number = t.get("increment")
        if number is None:
            continue
        key = slice_checks.increment_key(number)
        if highest is not None and key < highest:
            out.append(f"{tid}: increment {number} is positioned after a later increment's "
                       f"tasks — append your increment's tasks at the end and leave earlier "
                       f"increments' entries exactly where they are")
        highest = max(key, highest) if highest is not None else key
    return out


def _known_driver_ids(config):
    """(ids, resolved) — the CAP/L ids currently open in the working set. `resolved` is
    False when no capabilities/learnings file could be read, so C16 stays quiet rather
    than failing every task in a repo that has not seeded them."""
    ids, resolved = set(), False
    paths = common.config_doc(config).get("paths") or {}
    root = common.project_root(config)
    for key, field in (("capabilities", "capabilities"), ("learnings", "learnings"),
                       ("wf_learnings", "learnings")):
        rel = paths.get(key)
        if not rel:
            continue
        path = (root / rel).resolve()
        if not path.exists():
            continue
        resolved = True
        for entry in (common.load_yaml(path, optional=True).get(field) or []):
            if isinstance(entry, dict) and entry.get("id"):
                ids.add(str(entry["id"]))
    return ids, resolved


def _check(rest):
    p = common.base_parser("sprint check")
    p.add_argument("--slice", help="path to the design-slice (default: paths.design_slice)")
    p.add_argument("--strict", action="store_true", help="treat warnings as errors")
    args = p.parse_args(rest)

    sprint = common.load_yaml(common.resolve_path(args.config, "sprint", None))
    tasks = [t for t in (sprint.get("tasks") or []) if isinstance(t, dict)]
    task_cap = slice_checks.limit(args.config, "tasks_per_increment")
    root = common.project_root(args.config)
    findings = []  # (severity, code, message)

    def err(code, msg):  findings.append(("error", code, msg))
    def warn(code, msg): findings.append(("warn", code, msg))

    task_ids = {t.get("id") for t in tasks}
    increment_of = {t.get("id"): t.get("increment") for t in tasks}
    by_increment = {}
    systc_ids = set()
    target_owners = {}   # (package, name) -> [task ids]
    known_ids, ids_resolved = _known_driver_ids(args.config)

    for t in tasks:
        tid = t.get("id", "<no-id>")
        by_increment.setdefault(t.get("increment"), []).append(tid)
        covers = _covers_of(t)
        acs = t.get("acceptance") or []
        systests = t.get("system_tests") or []

        # B8 — the opening narrative
        for msg in _story_findings(tid, t.get("story")):
            err("B8", msg)

        # B11 — the title: the build agent makes it the commit subject, so a task
        # without one has no way to name what it landed
        if not str(t.get("title") or "").strip():
            err("B11", f"{tid}: no title — one line naming what this task delivers, "
                       f"which the build makes its commit subject")

        # B9 — boundaries is ONE section, and the retired fields stay retired
        if not str(t.get("boundaries") or "").strip():
            err("B9", f"{tid}: no boundaries — one merged section declares out-of-scope, "
                      f"read-only files, and fixed interfaces")
        for field in _RETIRED_BOUNDARY_FIELDS:
            if field in t:
                err("B9", f"{tid}: carries '{field}' — boundaries is a single section, "
                          f"so nothing else may state a limit the build must honour")
        for field in _RETIRED_SPEC_FIELDS:
            if field in t:
                err("B9", f"{tid}: carries '{field}', a field the four-section contract "
                          f"schema retired (story / acceptance / boundaries / grounding)")

        # B10 — an acceptance entry IS the requirement; it needs an id and a criterion
        for ac in acs:
            if not isinstance(ac, dict):
                err("B10", f"{tid}: an acceptance entry is not a mapping")
                continue
            if not str(ac.get("id") or "").strip():
                err("B10", f"{tid}: an acceptance entry has no id")
            if not str(ac.get("criterion") or "").strip():
                err("B10", f"{tid}: acceptance {ac.get('id', '<no-id>')} has no criterion "
                           f"— the criterion is the requirement")

        # B3 — every criterion carries proof: a test, or a named inspection
        for ac in acs:
            if not isinstance(ac, dict):
                continue
            if not (ac.get("tests") or []) and not str(ac.get("verified_by") or "").strip():
                err("B3", f"{tid}: acceptance {ac.get('id', '<no-id>')} has no tests and no "
                          f"verified_by (silent hole)")

        # B7 — test entry shape: a known level, a named target, and the seam an
        # integration test crosses
        for ac in acs:
            if not isinstance(ac, dict):
                continue
            for te in (ac.get("tests") or []):
                if not isinstance(te, dict):
                    err("B7", f"{tid}: acceptance {ac.get('id')} has a test entry that is "
                              f"not a mapping")
                    continue
                lvl = te.get("level")
                if lvl not in ("unit", "integration"):
                    err("B7", f"{tid}: acceptance {ac.get('id')} test declares level "
                              f"'{lvl}' — must be unit or integration")
                if lvl == "integration" and not str(te.get("seam") or "").strip():
                    err("B7", f"{tid}: acceptance {ac.get('id')} integration test names no "
                              f"seam — say which boundary it crosses")
                target = str(te.get("target") or "").strip()
                if not target:
                    err("B7", f"{tid}: acceptance {ac.get('id')} test names no target")
                else:
                    target_owners.setdefault(_target_key(target), []).append(tid)

        # B6 + C4 — e2e task shape: the SYS-TC is the acceptance, and it proves a
        # capability
        if systests:
            for st in systests:
                if not isinstance(st, dict):
                    err("B6", f"{tid}: system_tests entry '{st}' is a bare id — run "
                              f"`wf sprint materialize`")
                    continue
                if st.get("id"):
                    systc_ids.add(st["id"])
                if not str(st.get("description") or "").strip():
                    err("B6", f"{tid}: system_tests entry {st.get('id')} has no "
                              f"description — run `wf sprint materialize`")
                for c in _covers_of(st):
                    if not c.startswith("CAP-"):
                        err("C4", f"{tid}: system_test {st.get('id')} covers {c} — a "
                                  f"scenario proves a capability")
            if acs:
                err("C4", f"{tid}: e2e task carries acceptance — the SYS-TC is the "
                          f"acceptance")

        # C6 — the trace anchor: task → increment → CAP/L
        if not covers:
            err("C6", f"{tid}: no covers — name the capability or learning this task serves")
        for cid in covers:
            if not _DRIVER_ID_RE.fullmatch(cid):
                err("C6", f"{tid}: covers '{cid}' — covers names CAP/L ids only")
            elif ids_resolved and cid not in known_ids:
                err("C16", f"{tid}: covers {cid}, which is not open in the capabilities "
                           f"or learnings working set")
        if covers and not ids_resolved:
            warn("C16", f"{tid}: covers ids unverified — no capabilities or learnings "
                        f"file resolved from config")

        # C11 — grounding points into code that already exists
        for msg in _pointer_findings(root, tid, t.get("grounding") or []):
            err("C11", msg)

        # C12 — a declared dependency commit must name a declared dependency
        deps = set(t.get("depends_on") or [])
        for dep in _declared_dependency_commits(t):
            if dep not in deps:
                err("C12", f"{tid}: grounding declares a dependency_commit for '{dep}', "
                           f"which is not in depends_on")

    # C1 — depends_on integrity: known, acyclic, and never forward across increments
    for t in tasks:
        tid = t.get("id")
        for d in (t.get("depends_on") or []):
            if d not in task_ids:
                err("C1", f"{tid}: depends_on unknown task '{d}'")
            elif _is_later(increment_of.get(d), increment_of.get(tid)):
                err("C1", f"{tid} (increment {increment_of.get(tid)}): depends_on '{d}' in "
                          f"increment {increment_of.get(d)} — an increment merges before "
                          f"the next one is planned")
    if _has_cycle(tasks):
        err("C1", "task graph has a dependency cycle")

    # C13 — two contracts writing the same test name in the same package collide in
    # separate worktrees and the second one silently loses (L-088)
    for (package, name), owners in sorted(target_owners.items()):
        distinct = sorted(set(owners))
        if len(distinct) > 1:
            where = f"in {package}" if package else "unqualified"
            err("C13", f"tasks {distinct} all target '{name}' {where} — give each contract "
                       f"its own test name")

    # C17 — the file is cumulative: appended to per increment, never rewritten
    for msg in _cumulative_findings(tasks):
        err("C17", msg)

    # C14 — the sizing backstop: an over-cap increment is a slice defect, not a big TL run
    for number, ids in sorted(by_increment.items(), key=lambda kv: str(kv[0])):
        if len(ids) > task_cap:
            err("C14", f"increment {number} decomposes into {len(ids)} tasks, over "
                       f"limits.tasks_per_increment ({task_cap}) — raise a slice defect")

    # ---- slice → sprint (needs the slice) ----
    slice_path = _slice_path(args)
    if slice_path and slice_path.exists():
        text = slice_path.read_text()
        declared = {n for n, _ in slice_checks.increments(text)}
        for t in tasks:
            number = t.get("increment")
            if number is None:
                err("C15", f"{t.get('id')}: no increment — every task traces to one of the "
                           f"slice's increments")
            elif number not in declared:
                err("C15", f"{t.get('id')}: increment {number} is not declared in the slice "
                           f"(declared: {sorted(declared)})")
        # A2 — a slice scenario with no e2e task. The sprint fills up increment by
        # increment, so this is an error only once every declared increment has tasks.
        slice_tcs = set(_SLICE_TC_RE.findall(
            slice_checks.section(text, "System test cases")))
        decomposed = declared <= {n for n in by_increment if n is not None}
        for tc in sorted(slice_tcs - systc_ids):
            msg = f"slice {tc} has no e2e task carrying it"
            (err if decomposed else warn)("A2", msg)
    else:
        warn("A0", "slice not found; ran contract checks only")

    errors = [f for f in findings if f[0] == "error"]
    warns = [f for f in findings if f[0] == "warn"]
    result = {
        "sprint_id": sprint.get("sprint_id"),
        "tasks": len(tasks),
        "increments": {str(k): len(v) for k, v in sorted(
            by_increment.items(), key=lambda kv: str(kv[0]))},
        "errors": [{"code": c, "msg": m} for _, c, m in errors],
        "warnings": [{"code": c, "msg": m} for _, c, m in warns],
        "verdict": "fail" if errors or (args.strict and warns) else "pass",
    }
    common.emit(result, args.format)
    return 1 if result["verdict"] == "fail" else 0


def _is_later(dep_increment, task_increment):
    """True when the dependency sits in a LATER increment than the task that needs it."""
    try:
        return int(dep_increment) > int(task_increment)
    except (TypeError, ValueError):
        return False


def _has_cycle(tasks):
    graph = {t.get("id"): (t.get("depends_on") or []) for t in tasks}
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {tid: WHITE for tid in graph}

    def visit(n):
        colour[n] = GREY
        for m in graph.get(n, []):
            if m not in colour:
                continue
            if colour[m] == GREY or (colour[m] == WHITE and visit(m)):
                return True
        colour[n] = BLACK
        return False

    return any(colour[n] == WHITE and visit(n) for n in graph)


COMMANDS = {
    ("sprint", "task"): _task,
    ("sprint", "materialize"): _materialize,
    ("sprint", "prune"): _prune,
    ("sprint", "check"): _check,
}
