"""wf sprint — read-side helpers over the sprint task DAG (sprint.yaml).

``sprint task`` extracts ONE task's contract into a worktree before build, so the
build agent reads a focused contract rather than the whole sprint.

``sprint materialize`` inlines the slice's verbatim fields into the sprint: the
TL authors thin references (covers ids, interface_contract_ref, system_tests
ids) and this fills in requirement statements, per-requirement drivers, the
serves union, interface-contract shapes, and SYS-TC scenarios — mechanical
copying stays out of the LLM's output budget. Idempotent; re-run after any
sprint edit, before ``sprint check``.

``sprint check`` is the analyze gate: a mechanical consistency/coverage linter over
the sprint DAG (and, when present, the slice it was cut from). It verifies the
STRUCTURE of the requirement -> AC -> test trace, never its meaning — so the cheap
mechanical holes (an AC with no test, a dropped requirement, a cycle) are caught
before build, leaving the expensive human/adversarial review for the semantic ones.
Exits non-zero on any error-severity finding.
"""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

import common
import slice as slice_checks

_AC_RE = re.compile(r"^(REQ-\d+)\.AC-\d+$")          # AC id -> owning REQ
_SLICE_REQ_RE = re.compile(r"\*\*(REQ-\d+)\*\*")     # slice "Component requirements" bullets
_SLICE_TC_RE = re.compile(r"\b(SYS-TC-\d+)\b")

# C9 path-like note tokens (deliberately conservative): a whitespace-delimited token
# with an optional path prefix and a lower-case, letter-led extension. An upper-case-led
# final segment (uuid.UUID, d.ID, ElementRepository.List) is a qualified symbol, not a
# file path, and does not match. Prose abbreviations that match the shape are skipped.
_NOTE_PATH_RE = re.compile(r"^[\w./-]*[\w-]\.[a-z][a-z0-9]{0,4}$")
_NOTE_SKIP = {"e.g", "i.e"}

# C11 source-claim pointers: `<path>:<symbol>`, the grounding a claim about existing
# behaviour must carry. A digit-led suffix is a line number, not a symbol, and is skipped.
_POINTER_RE = re.compile(r"([\w./-]*[\w-]\.[a-z][a-z0-9]{0,4}):([A-Za-z_]\w*)")


_is_test_path = common.is_test_path  # C3's test-home heuristic, shared with `wf impact`


def _note_paths(note):
    """File paths a prose note names, per the conservative C9 token shape. A token
    prefixed ``read-only:`` is a declared read-only reference pointer (task-contract.md),
    not a write target, so it is skipped rather than checked against files_to_touch."""
    out = []
    for raw in re.split(r"[\s,;()\[\]{}<>]+", str(note)):
        stripped = raw.strip("`'\"*")
        if stripped.lower().startswith("read-only:"):
            continue
        tok = stripped.split(":", 1)[0].rstrip(".,!?").strip("`'\"*")
        if tok.lower() in _NOTE_SKIP:
            continue
        if _NOTE_PATH_RE.match(tok):
            out.append(tok)
    return out


def _pointer_findings(root, tid, texts, files):
    """C11: a `<path>:<symbol>` pointer into a file the task does NOT touch is a claim
    about code that already exists — if the file carries no such symbol, the claim was
    asserted rather than checked. A pointer into a file the task edits is skipped: that
    symbol may be one the build is about to write."""
    out = []
    for text in texts:
        for path_token, symbol in _POINTER_RE.findall(str(text)):
            if path_token in files or any(f.endswith("/" + path_token) for f in files):
                continue
            target = root / path_token
            if not target.is_file():
                continue  # unresolvable path — C9's territory, not a symbol claim
            if symbol not in target.read_text(errors="replace"):
                out.append(f"{tid}: cites {path_token}:{symbol}, but {path_token} carries "
                           f"no '{symbol}' — verify the claim against the source")
    return out


def _serves_of(value):
    """A `serves` may be a scalar or a list; normalise to a list of strings."""
    if not value:
        return []
    return value if isinstance(value, list) else [value]


def _dependency_commits(config, entry):
    """{dep task id: merge_commit} for the entry's already-merged depends_on tasks,
    read from paths.pipeline_state — injected into the extracted contract so the build
    agent recovers a dependency's pattern from its merge diff (`git show <sha>`) instead
    of exploring the tree. Best-effort: a missing config key, absent state file, or
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


def _task(rest):
    """Emit (or --write) the contract for a single task from sprint.yaml. --write is an
    explicit path ARGUMENT used as-given (the orchestrator passes the worktree-resolved
    current_task path), never re-anchored on the host config."""
    p = common.base_parser("sprint task")
    p.add_argument("task_id")
    p.add_argument("--write", help="write the contract to this path instead of stdout")
    args = p.parse_args(rest)

    sprint = common.load_yaml(common.resolve_path(args.config, "sprint", None))
    tasks = sprint.get("tasks") or []
    entry = next(
        (t for t in tasks if isinstance(t, dict) and t.get("id") == args.task_id), None
    )
    if entry is None:
        common.die(f"task {args.task_id} not found in sprint")

    dep_commits = _dependency_commits(args.config, entry)
    if dep_commits:
        entry = dict(entry)
        entry["dependency_commits"] = dep_commits

    if args.write:
        import yaml as _yaml

        out_path = Path(args.write)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            _yaml.safe_dump(entry, sort_keys=False, default_flow_style=False, allow_unicode=True)
        )
        common.emit({"task_id": args.task_id, "written": str(out_path)}, args.format)
    else:
        common.emit(entry, args.format)
    return 0


def _covers_of(item):
    """A `covers` field may be a scalar or a list; normalise to a list of strings."""
    c = item.get("covers")
    if c is None:
        return []
    return c if isinstance(c, list) else [c]


def _section(text, header):
    """The markdown block under `## <header>` up to the next `## ` heading (or EOF)."""
    m = re.search(rf"^##\s+{re.escape(header)}\s*$", text, re.MULTILINE)
    if not m:
        return ""
    rest = text[m.end():]
    nxt = re.search(r"^##\s", rest, re.MULTILINE)
    return rest[: nxt.start()] if nxt else rest


def _slice_path(args):
    """Resolve the design-slice location without dying when the key is unset — an
    absent slice is a warning (A0), not a hard failure (the intra-sprint checks still run)."""
    if args.slice:
        return Path(args.slice)
    rel = (common.config_doc(args.config).get("paths") or {}).get("design_slice")
    if not rel:
        return None
    return (common.project_root(args.config) / rel).resolve()


# ---------------------------------------------------------------------------
# sprint materialize — slice parsing + verbatim-field inlining
# ---------------------------------------------------------------------------

# One "Component requirements" bullet: id, statement (may wrap), owner parenthetical.
_REQ_FULL_RE = re.compile(
    r"-\s+\*\*(REQ-\d+)\*\*\s+—\s+(.*?)\s*\*\(owner:\s*(.*?)\)\*", re.DOTALL
)
_DRIVER_ID_RE = re.compile(r"\b(?:CAP|L|ADR|D)-\w+\b")
_TC_HEAD_RE = re.compile(r"-\s+\*\*(SYS-TC-\d+):\*\*\s*(.*)")
_GWT_RE = re.compile(r"\*\*(Given|When|Then)\*\*\s*(.*)")
_IC_HEAD_RE = re.compile(r"-\s+\*\*(.+?)\*\*")


def _oneline(text):
    return " ".join(str(text).split())


def _slice_requirements(text):
    """{REQ-id: {statement, serves}} from '## Component requirements'. The driver
    is the FIRST id-shaped token after the owner component — later tokens
    (binding ADRs, secondary annotations) are context, not drivers."""
    out = {}
    for rid, stmt, owner in _REQ_FULL_RE.findall(_section(text, "Component requirements")):
        chunks = owner.split("·")[1:]  # chunk 0 is the owning component
        driver = None
        for chunk in chunks:
            m = _DRIVER_ID_RE.search(chunk)
            if m:
                driver = m.group(0)
                break
        out[rid] = {"statement": _oneline(stmt), "serves": driver}
    return out


def _slice_system_testcases(text):
    """{SYS-TC-id: {description, covers}} from '## System test cases'. The
    description is the case title plus its Given/When/Then scenario, joined to
    one line — the build stamps it verbatim on the [SYS-TC:] tag."""
    out = {}
    cur = None
    for line in _section(text, "System test cases").splitlines():
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


def _slice_interface_contracts(text):
    """{name: shape} from '## Interface contracts'. Each bullet's name is its
    bold text; its shape is the indented block beneath it, dedented verbatim."""
    out = {}
    name, block = None, []

    def flush():
        if name is not None:
            out[name] = textwrap.dedent("\n".join(block)).strip("\n")

    for line in _section(text, "Interface contracts").splitlines():
        head = _IC_HEAD_RE.match(line.strip()) if line.lstrip().startswith("- ") else None
        if head:
            flush()
            name, block = head.group(1).strip(), []
        elif name is not None and (not line.strip() or line[:1].isspace()):
            block.append(line)
    flush()
    return out


_TASK_FIELD_ORDER = [
    "id", "title", "depends_on", "covers", "requirements", "serves",
    "files_to_touch", "acceptance_criteria", "system_tests", "out_of_scope",
    "implementation_notes", "interface_contract_ref", "interface_contract",
]


def _ordered_task(task):
    """Rebuild a task dict in canonical field order (unknown fields keep their
    relative order at the end) so materialize output is stable and diffable."""
    out = {k: task[k] for k in _TASK_FIELD_ORDER if k in task}
    out.update({k: v for k, v in task.items() if k not in out})
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

    reqs = _slice_requirements(text)
    tcs = _slice_system_testcases(text)
    contracts = _slice_interface_contracts(text)

    errors = []
    # A bullet that carries its whole shape inline parses as an empty shape; inlining
    # that would hand the task an empty interface_contract and trip B6 at check time.
    for name, shape in contracts.items():
        if not shape.strip():
            errors.append(f"interface contract '{name}' has no shape: put it on indented "
                          f"lines beneath the bullet, not inline on the bullet itself")
    n_reqs = n_ics = n_tcs = 0
    tasks = sprint.get("tasks") or []
    for i, t in enumerate(tasks):
        tid = t.get("id", "<no-id>")

        systests = t.get("system_tests") or []
        if systests:
            serves = []
            for st in systests:
                info = tcs.get(st.get("id"))
                if info is None:
                    errors.append(f"{tid}: system_tests names {st.get('id')}, "
                                  f"which is not a case in the slice")
                    continue
                st["description"] = info["description"]
                st["covers"] = list(info["covers"])
                serves += [c for c in info["covers"] if c not in serves]
                n_tcs += 1
            if serves:
                t["serves"] = serves

        covers = _covers_of(t)
        if covers:
            # The slice supplies each entry; a covers id the slice lacks keeps a
            # hand-carried requirements[] entry when one exists (a fix-mode
            # follow-up on code shipped by an earlier sprint carries its
            # statement from the origin task) and errors only when neither has it.
            carried = {r.get("id"): r for r in (t.get("requirements") or [])}
            entries, serves = [], []
            for rid in covers:
                info = reqs.get(rid)
                if info is None and rid in carried:
                    entry = carried[rid]
                elif info is None:
                    errors.append(f"{tid}: covers {rid}, which is not a requirement in the slice")
                    continue
                else:
                    entry = {"id": rid, "statement": info["statement"],
                             "serves": info["serves"]}
                entries.append(entry)
                for s in _serves_of(entry.get("serves")):
                    if s and s not in serves:
                        serves.append(s)
                n_reqs += 1
            t["requirements"] = entries
            t["serves"] = serves

        ref = t.get("interface_contract_ref")
        if ref:
            names = ref if isinstance(ref, list) else [ref]
            blocks = []
            for name in names:
                if name not in contracts:
                    valid = ", ".join(f"'{c}'" for c in contracts) or "none"
                    errors.append(f"{tid}: interface_contract_ref '{name}' is not a "
                                  f"contract in the slice (valid: {valid})")
                else:
                    blocks.append(contracts[name])
            if len(blocks) == len(names):
                t["interface_contract"] = "\n\n".join(blocks)
                n_ics += 1

        tasks[i] = _ordered_task(t)

    if errors:
        common.emit({"verdict": "fail", "errors": errors}, args.format)
        return 1

    import yaml as _yaml

    sprint["tasks"] = tasks
    sprint_path.write_text(
        _yaml.safe_dump(sprint, sort_keys=False, default_flow_style=False, allow_unicode=True)
    )
    common.emit({
        "sprint_id": sprint.get("sprint_id"),
        "tasks": len(tasks),
        "requirements": n_reqs,
        "interface_contracts": n_ics,
        "system_tests": n_tcs,
        "verdict": "pass",
    }, args.format)
    return 0


def _check(rest):
    p = common.base_parser("sprint check")
    p.add_argument("--slice", help="path to the design-slice (default: paths.design_slice)")
    p.add_argument("--strict", action="store_true", help="treat warnings as errors")
    args = p.parse_args(rest)

    sprint = common.load_yaml(common.resolve_path(args.config, "sprint", None))
    tasks = sprint.get("tasks") or []
    findings = []  # (severity, code, message)

    def err(code, msg):  findings.append(("error", code, msg))
    def warn(code, msg): findings.append(("warn", code, msg))

    # ---- Family B + C: intra-sprint (self-contained in sprint.yaml) ----
    task_ids = {t.get("id") for t in tasks}
    covered_reqs = {}  # REQ -> [task ids]  (union — a REQ MAY span tasks if its ACs partition)
    ac_owner = {}      # AC id -> [task ids] (the real "exactly one task" invariant, C7)
    systc_ids = set()

    for t in tasks:
        tid = t.get("id", "<no-id>")
        covers = _covers_of(t)
        reqs = {r.get("id"): (r.get("statement") or "").strip()
                for r in (t.get("requirements") or [])}
        ac_entries = t.get("acceptance_criteria") or []
        acs = [ac.get("id") for ac in ac_entries]
        systests = t.get("system_tests") or []
        files = set(t.get("files_to_touch") or [])

        for req in covers:
            covered_reqs.setdefault(req, []).append(tid)
        for ac in acs:
            ac_owner.setdefault(ac, []).append(tid)

        # B1 — every covered REQ carries a verbatim statement
        for req in covers:
            if req not in reqs:
                err("B1", f"{tid}: covers {req} but has no requirements[] entry for it "
                          f"— run `wf sprint materialize`")
            elif not reqs[req]:
                err("B1", f"{tid}: requirements entry {req} has an empty statement")

        # B2 — every covered REQ has >=1 AC
        for req in covers:
            if not any(_AC_RE.match(a or "") and _AC_RE.match(a).group(1) == req for a in acs):
                err("B2", f"{tid}: {req} has no acceptance criterion (REQ with no AC)")

        # B3 — every AC carries >=1 test entry (the silent hole this gate exists to
        # catch). An AC verified by a named mechanical gate instead of a test declares
        # `verified_by: <gate/command>` and is exempt.
        for ac in ac_entries:
            if not (ac.get("tests") or []) and not str(ac.get("verified_by") or "").strip():
                err("B3", f"{tid}: AC {ac.get('id')} has no tests and no verified_by "
                          f"(silent hole)")

        # B5 — happy-path-only heuristic
        for req in covers:
            n = sum(1 for a in acs if _AC_RE.match(a or "") and _AC_RE.match(a).group(1) == req)
            if n == 1:
                warn("B5", f"{tid}: {req} has a single AC — verify failure/boundary is covered")

        # B6 — thin references the materializer has not inlined yet
        if t.get("interface_contract_ref") and not str(t.get("interface_contract") or "").strip():
            err("B6", f"{tid}: interface_contract_ref present but interface_contract "
                      f"is not inlined — run `wf sprint materialize`")

        # B7 + C2 — test entries: a known level; a unit test's target file declared in
        # the expected write set (C2 is a planning-quality hint — files_to_touch is
        # advisory, so an undeclared target warns, never fails)
        for ac in ac_entries:
            for te in (ac.get("tests") or []):
                lvl = te.get("level")
                if lvl not in ("unit", "integration"):
                    err("B7", f"{tid}: AC {ac.get('id')} test declares level '{lvl}' "
                              f"— must be unit or integration")
                elif lvl == "unit":
                    fpath = (te.get("target") or "").split(":", 1)[0].strip()
                    if not fpath:
                        warn("C2", f"{tid}: AC {ac.get('id')} unit test names no target")
                    elif fpath not in files:
                        warn("C2", f"{tid}: unit target '{fpath}' not in files_to_touch "
                                   f"— declare it so overlaps become ordering edges")

        # C3 — mandated tests should declare a file home (planning-quality hint)
        if any(ac.get("tests") for ac in ac_entries) and not any(_is_test_path(f) for f in files):
            warn("C3", f"{tid}: acceptance criteria mandate tests but files_to_touch "
                       f"declares no test file (*_test.*, test_*.*, *.test.*, *.spec.*, "
                       f"tests/ ...) — declare the test home in the expected write set")

        # C4 — e2e task shape (B6 when the case text is not materialized yet)
        if systests:
            for st in systests:
                if st.get("id"):
                    systc_ids.add(st["id"])
                if not str(st.get("description") or "").strip():
                    err("B6", f"{tid}: system_tests entry {st.get('id')} has no "
                              f"description — run `wf sprint materialize`")
                for c in _covers_of(st):
                    if str(c).startswith("REQ-"):
                        err("C4", f"{tid}: system_test {st.get('id')} covers {c} — must cover a CAP, not a REQ")
            if covers:
                err("C4", f"{tid}: e2e task also has covers {covers} — an e2e task proves a SYS-TC, not a REQ")
            if acs:
                err("C4", f"{tid}: e2e task has acceptance_criteria — the SYS-TC is the acceptance")

        # C5 — sizing (line count isn't knowable pre-build)
        if not systests and len(files) > 5:
            warn("C5", f"{tid}: touches {len(files)} files (> 5) — consider splitting")

        # C6 — serves present
        if not t.get("serves"):
            err("C6", f"{tid}: no serves (driver) declared")

        # C8 — per-requirement driver mapping: every requirements[] entry declares its
        # serves, and the task-level serves is exactly their union (no "primary driver"
        # fudge in either direction). Scoped to tasks WITH requirements — an e2e task's
        # serves is its SYS-TC's capability.
        req_entries = t.get("requirements") or []
        if req_entries:
            task_serves = _serves_of(t.get("serves"))
            declared = set()
            for r in req_entries:
                r_serves = _serves_of(r.get("serves"))
                if not r_serves:
                    err("C8", f"{tid}: requirement {r.get('id', '<no-id>')} declares no "
                              f"serves (per-requirement driver)")
                declared.update(r_serves)
            for d in sorted(declared - set(task_serves)):
                err("C8", f"{tid}: requirement driver {d} missing from task serves")
            for s in sorted(set(task_serves) - declared):
                err("C8", f"{tid}: serves {s} is not the driver of any requirement in this task")

        # C9 — implementation_notes naming a file outside files_to_touch (heuristic
        # extraction over prose, so a warning, not an error). Notes often name a bare
        # filename while files_to_touch carries the full path — a noted token is in
        # scope when any files_to_touch entry ends with it.
        noted = set()
        for note in (t.get("implementation_notes") or []):
            noted.update(_note_paths(note))
        for fp in sorted(noted):
            if fp not in files and not any(f.endswith("/" + fp) for f in files):
                warn("C9", f"{tid}: implementation_notes name '{fp}', which is not in files_to_touch")

        # C11 — a source-claim pointer must resolve. The pointer is what makes a claim
        # about existing behaviour checkable; one naming a symbol its file does not
        # contain means the claim was asserted, not verified.
        claim_texts = list(t.get("implementation_notes") or []) + \
            list(t.get("out_of_scope") or []) + \
            [ac.get("check", "") for ac in (t.get("acceptance_criteria") or [])
             if isinstance(ac, dict)]
        for msg in _pointer_findings(common.project_root(args.config), tid, claim_texts, files):
            err("C11", msg)

    # C1 — depends_on integrity + acyclicity
    for t in tasks:
        for d in (t.get("depends_on") or []):
            if d not in task_ids:
                err("C1", f"{t.get('id')}: depends_on unknown task '{d}'")
    if _has_cycle(tasks):
        err("C1", "task graph has a dependency cycle")

    # C7 — the "exactly one task" invariant is on CRITERIA, not requirements. A REQ may
    # legitimately span tasks when its ACs partition across them.
    for ac, owners in ac_owner.items():
        if len(owners) > 1:
            err("C7", f"AC {ac} claimed by multiple tasks {owners} — a criterion lands in exactly one")

    # C10 — two tasks whose declared write sets overlap and carry no dependency edge
    # can land in the same parallel stage and edit the same file in separate worktrees.
    # A planning-quality hint: the choice is to add an edge OR accept the merge-conflict
    # risk (the stage boundary repairs a conflicted merge on demand). A transitive edge
    # either direction orders them and quiets the warning.
    reach = _reachable(tasks)
    files_by = {t.get("id"): set(t.get("files_to_touch") or []) for t in tasks}
    ordered = [t.get("id") for t in tasks]
    for i in range(len(ordered)):
        for j in range(i + 1, len(ordered)):
            a, b = ordered[i], ordered[j]
            shared = files_by.get(a, set()) & files_by.get(b, set())
            if shared and b not in reach.get(a, set()) and a not in reach.get(b, set()):
                warn("C10", f"{a} and {b} share files_to_touch {sorted(shared)} with no "
                            f"dependency edge — add a depends_on edge to order them, or "
                            f"accept the merge-conflict risk at the stage boundary")

    # ---- Family A: slice -> sprint completeness (needs the slice) ----
    slice_path = _slice_path(args)
    if slice_path and slice_path.exists():
        text = slice_path.read_text()
        slice_reqs = set(_SLICE_REQ_RE.findall(_section(text, "Component requirements")))
        slice_tcs = set(_SLICE_TC_RE.findall(_section(text, "System test cases")))
        for req in sorted(slice_reqs - set(covered_reqs)):
            err("A1", f"slice {req} is not covered by any task (dropped requirement)")
        for tc in sorted(slice_tcs - systc_ids):
            err("A2", f"slice {tc} has no e2e task carrying it")
        # A3 — every interpretive assumption the SA recorded must be human-confirmed
        # before build (backstop; `wf slice check` gates it earlier, at the SA's own
        # handoff).
        for msg in slice_checks.unconfirmed_assumptions(text):
            err("A3", msg)
    else:
        warn("A0", "slice not found; ran intra-sprint checks only (B/C)")

    # ---- verdict ----
    errors = [f for f in findings if f[0] == "error"]
    warns = [f for f in findings if f[0] == "warn"]
    result = {
        "sprint_id": sprint.get("sprint_id"),
        "tasks": len(tasks),
        "errors": [{"code": c, "msg": m} for _, c, m in errors],
        "warnings": [{"code": c, "msg": m} for _, c, m in warns],
        "verdict": "fail" if errors or (args.strict and warns) else "pass",
    }
    common.emit(result, args.format)
    return 1 if result["verdict"] == "fail" else 0


def _reachable(tasks):
    """Map each task id to the set of task ids reachable via depends_on (transitive).
    Cycle-safe: the `seen` set bounds the walk even on a cyclic graph (C1 flags cycles)."""
    graph = {t.get("id"): list(t.get("depends_on") or []) for t in tasks}

    def walk(start):
        seen, stack = set(), list(graph.get(start, []))
        while stack:
            n = stack.pop()
            if n in seen or n not in graph:
                continue
            seen.add(n)
            stack.extend(graph.get(n, []))
        return seen

    return {n: walk(n) for n in graph}


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
    ("sprint", "check"): _check,
}
