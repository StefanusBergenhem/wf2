"""wf sprint — read-side helpers over the sprint task DAG (sprint.yaml).

``sprint task`` extracts ONE task's contract into a worktree before build, so the
build agent reads a focused contract rather than the whole sprint.

``sprint check`` is the analyze gate: a mechanical consistency/coverage linter over
the sprint DAG (and, when present, the slice it was cut from). It verifies the
STRUCTURE of the requirement -> AC -> test trace, never its meaning — so the cheap
mechanical holes (an AC with no test, a dropped requirement, a cycle) are caught
before build, leaving the expensive human/adversarial review for the semantic ones.
Exits non-zero on any error-severity finding.
"""
from __future__ import annotations

import re
from pathlib import Path

import common
import slice as slice_checks

_AC_RE = re.compile(r"^(REQ-\d+)\.AC-\d+$")          # AC id -> owning REQ
_SLICE_REQ_RE = re.compile(r"\*\*(REQ-\d+)\*\*")     # slice "Component requirements" bullets
_SLICE_TC_RE = re.compile(r"\b(SYS-TC-\d+)\b")

# C3 test-file heuristic (deliberately simple + language-agnostic): a path is a
# plausible test home when a directory segment is a conventional test dir, or the
# filename carries test/spec as a delimited token (covers *_test.*, test_*.*,
# *.test.*, *.spec.*, *-test.* ...). The goal is catching a contract that lists NO
# test file at all, not policing which one.
_TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs", "testdata"}
_TEST_NAME_RE = re.compile(r"(^|[._-])(test|spec)s?([._-]|$)", re.IGNORECASE)

# C9 path-like note tokens (deliberately conservative): a whitespace-delimited token
# with an optional path prefix and a letter-led extension. Prose abbreviations that
# match the shape are skipped.
_NOTE_PATH_RE = re.compile(r"^[\w./-]*[\w-]\.[A-Za-z][A-Za-z0-9]{0,4}$")
_NOTE_SKIP = {"e.g", "i.e"}


def _is_test_path(path):
    parts = [seg for seg in str(path).replace("\\", "/").split("/") if seg]
    if not parts:
        return False
    if any(seg.lower() in _TEST_DIRS for seg in parts[:-1]):
        return True
    return bool(_TEST_NAME_RE.search(parts[-1]))


def _note_paths(note):
    """File paths a prose note names, per the conservative C9 token shape."""
    out = []
    for raw in re.split(r"[\s,;()\[\]{}<>]+", str(note)):
        tok = raw.strip("`'\"*").split(":", 1)[0].rstrip(".,!?").strip("`'\"*")
        if tok.lower() in _NOTE_SKIP:
            continue
        if _NOTE_PATH_RE.match(tok):
            out.append(tok)
    return out


def _serves_of(value):
    """A `serves` may be a scalar or a list; normalise to a list of strings."""
    if not value:
        return []
    return value if isinstance(value, list) else [value]


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
        ac_set = set(acs)
        tm = t.get("testing_mandate") or {}
        unit = tm.get("unit_tests") or []
        integ = tm.get("integration_tests") or []
        systests = tm.get("system_tests") or []
        files = set(t.get("files_to_touch") or [])

        for req in covers:
            covered_reqs.setdefault(req, []).append(tid)
        for ac in acs:
            ac_owner.setdefault(ac, []).append(tid)

        # B1 — every covered REQ carries a verbatim statement
        for req in covers:
            if req not in reqs:
                err("B1", f"{tid}: covers {req} but has no requirements[] entry for it")
            elif not reqs[req]:
                err("B1", f"{tid}: requirements entry {req} has an empty statement")

        # B2 — every covered REQ has >=1 AC
        for req in covers:
            if not any(_AC_RE.match(a or "") and _AC_RE.match(a).group(1) == req for a in acs):
                err("B2", f"{tid}: {req} has no acceptance criterion (REQ with no AC)")

        # test -> AC references (unit + integration only; system covers CAPs)
        test_ac_refs = []
        for u in unit:
            for tst in (u.get("tests") or []):
                test_ac_refs += _covers_of(tst)
        for it in integ:
            test_ac_refs += _covers_of(it)

        # B3 — every AC is referenced by >=1 test (the silent hole this gate exists to
        # catch). An AC verified by a named mechanical gate instead of a test declares
        # `verified_by: <gate/command>` and is exempt.
        gate_acs = {ac.get("id") for ac in ac_entries
                    if str(ac.get("verified_by") or "").strip()}
        for ac in acs:
            if ac not in test_ac_refs and ac not in gate_acs:
                err("B3", f"{tid}: AC {ac} is not referenced by any test (silent hole)")

        # B4 — every AC-shaped test ref names an AC that exists in this task
        for ref in test_ac_refs:
            if _AC_RE.match(ref or "") and ref not in ac_set:
                err("B4", f"{tid}: a test covers {ref}, which is not an AC in this task")

        # B5 — happy-path-only heuristic
        for req in covers:
            n = sum(1 for a in acs if _AC_RE.match(a or "") and _AC_RE.match(a).group(1) == req)
            if n == 1:
                warn("B5", f"{tid}: {req} has a single AC — verify failure/boundary is covered")

        # C2 — unit target files are in scope
        for u in unit:
            fpath = (u.get("target") or "").split(":", 1)[0].strip()
            if fpath and fpath not in files:
                err("C2", f"{tid}: unit target '{fpath}' not in files_to_touch")

        # C3 — mandated unit/integration tests need a file to live in
        if (unit or integ) and not any(_is_test_path(f) for f in files):
            err("C3", f"{tid}: testing_mandate names unit/integration tests but "
                      f"files_to_touch has no test file (*_test.*, test_*.*, *.test.*, "
                      f"*.spec.*, tests/ ...) — the mandated tests have no home")

        # C4 — e2e task shape
        if systests:
            for st in systests:
                if st.get("id"):
                    systc_ids.add(st["id"])
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
    ("sprint", "check"): _check,
}
