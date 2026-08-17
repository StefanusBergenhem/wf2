"""wf stage — one cut: the design header plus that cut's task contracts.

``paths.stage`` is written whole by the design role at each cut and deleted when the
stage merges. Nothing appends to it, nothing amends it in flight, and it carries no
task ordering — a stage IS the set of tasks with no dependency between them.

``stage check`` is the single gate: a mechanical linter over the header the build
envelope carries (serves / goal / allocation / flow / checkpoint), the four-section
contract schema (story / acceptance / boundaries / grounding), and the pointers both
make into the repo. It verifies STRUCTURE, never meaning. Exits non-zero on any
error-severity finding.

``stage task`` builds ONE task's build envelope: this task's slice of the stage shape,
then covers → story → acceptance → boundaries → grounding, plus the title, the inlined
scenario text on an e2e task, its own ``nfr`` and ``supersedes`` obligations, and
``--prior-attempt`` when the driver rebased an earlier attempt's branch into the
worktree. Nothing else travels — every header field but ``goal`` is authored keyed by
task, and a build is handed its own key and no sibling's.

``stage materialize`` inlines the scenario text nested under each capability and
learning: the role authors bare ids and this fills in the verbatim description the
build stamps on the ``[SYS-TC:]`` tag. Idempotent; re-run before ``stage check``.
"""
from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

import yaml

import common
import mdread
import runstate

_DRIVER_ID_RE = re.compile(r"\b(?:CAP|L)-\d+\b")

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
# An edge or a level inside one stage contradicts what a stage is.
_FORBIDDEN_TASK_FIELDS = ("depends_on", "increment")

_PLACEHOLDER_RE = re.compile(r"\b(TODO|FIXME|TBD)\b", re.IGNORECASE)
_STORY_MIN_WORDS = 20

# The header fields that ride in every build envelope — the shape the task builds into.
_SHAPE_FIELDS = ("goal", "allocation", "flow", "checkpoint")
# `allocation` is bound by A12 instead: its components resolve, or the cut escalates.
_PROSE_SHAPE_FIELDS = ("goal", "flow", "checkpoint")

# A description that ends on one of these function words — or a comma — was cut
# mid-sentence: no complete clause ends here. Kept deliberately narrow (articles,
# conjunctions, common prepositions/subordinators) so a real scenario ending in a
# noun or verb is never flagged.
_DANGLING_TAIL = {
    "the", "a", "an", "and", "or", "but", "nor", "of", "to", "with", "for", "in",
    "on", "at", "by", "from", "into", "onto", "upon", "over", "under", "per", "via",
    "than", "as", "because", "that", "which", "while", "when", "where", "if", "so",
}


class _StageDumper(yaml.SafeDumper):
    """Keeps authored prose in literal block style — ``story: |`` is how the role
    wrote it, and re-quoting a paragraph turns every rewrite into a whole-file diff."""


def _represent_str(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", data,
                                   style="|" if "\n" in data else None)


_StageDumper.add_representer(str, _represent_str)


def _dump(doc):
    return yaml.dump(doc, Dumper=_StageDumper, sort_keys=False,
                     default_flow_style=False, allow_unicode=True)


def _oneline(text):
    return " ".join(str(text).split())


def _serves(doc):
    """The ids on the stage's `serves:` key, in order — the driver's PR-body input."""
    raw = doc.get("serves")
    if raw is None:
        return []
    return [str(x).strip() for x in (raw if isinstance(raw, list) else [raw])]


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


def _grounding_items(task):
    """The grounding entries that are pointers — a stray mapping names nothing."""
    return [g for g in (task.get("grounding") or []) if not isinstance(g, (dict, list))]


# ---------------------------------------------------------------------------
# stage task — the build envelope
# ---------------------------------------------------------------------------

# The order the build agent reads: what shape this stage is building, which driver
# the task serves, then what it builds, what proves it, what it may not touch, what
# it can look at, and finally what an earlier attempt already left in the worktree.
_ENVELOPE_ORDER = ["id", "title", "goal", "allocation", "flow", "checkpoint", "covers",
                   "story", "acceptance", "boundaries", "grounding", "system_tests",
                   "nfr", "supersedes", "prior_attempt"]


def _names_task(entry, task_id) -> bool:
    """Whether a header entry is keyed to this task. ``tasks:`` is a list because one
    component is often allocated to several tasks in a stage; ``task:`` is the single
    form the per-task fields use."""
    if not isinstance(entry, dict):
        return False
    listed = entry.get("tasks")
    if isinstance(listed, (list, tuple)):
        return str(task_id) in [str(t) for t in listed]
    return str(entry.get("task") or "") == str(task_id)


def _mine(entries, task_id) -> list:
    return [e for e in (entries or []) if _names_task(e, task_id)]


def _shape_for(doc, task_id) -> dict:
    """This task's slice of the stage header.

    Every field here is authored keyed by task, and the build is handed its own key and
    no one else's: a sibling's allocation, flow and checkpoint describe work this task's
    own `boundaries` forbid it to touch. `goal` is the exception — one line, and the
    only genuinely stage-wide framing.
    """
    out = {}
    if doc.get("goal") is not None:
        out["goal"] = doc["goal"]
    allocation = [{k: v for k, v in a.items() if k not in ("task", "tasks")}
                  for a in _mine(doc.get("allocation"), task_id)]
    if allocation:
        out["allocation"] = allocation
    flow = [str(f.get("does") or "").rstrip() for f in _mine(doc.get("flow"), task_id)]
    flow = [f for f in flow if f]
    if flow:
        out["flow"] = "\n\n".join(flow) + "\n"
    checkpoint = [str(c.get("observable") or "")
                  for c in _mine(doc.get("checkpoint"), task_id)]
    checkpoint = [c for c in checkpoint if c]
    if checkpoint:
        out["checkpoint"] = checkpoint
    return out


def _obligations_for(doc, task_id) -> dict:
    """The two per-task obligations the header carries beyond the shape: the measurable
    envelope this task must hold, and the shipped behaviour it retires. Both are already
    authored against a task id — an acceptance criterion citing "the stage's NFR
    envelope" names a section the build never received without this."""
    out = {}
    nfr = [str(n.get("envelope") or "") for n in _mine(doc.get("nfr"), task_id)]
    nfr = [n for n in nfr if n]
    if nfr:
        out["nfr"] = nfr
    supersedes = [{k: s[k] for k in ("retires", "proven_by", "reason") if k in s}
                  for s in (doc.get("supersessions") or [])
                  if isinstance(s, dict) and str(s.get("successor") or "") == str(task_id)]
    if supersedes:
        out["supersedes"] = supersedes
    return out


def _envelope(doc, entry, prior_attempt=None):
    """The build envelope for one task: this task's slice of the stage shape, the driver
    it serves, the four contract sections, and the task's one-line title."""
    out = {"id": entry.get("id")}
    if entry.get("title") is not None:
        out["title"] = entry["title"]
    task_id = entry.get("id")
    out.update(_shape_for(doc, task_id))
    covers = runstate.covers_of(entry)
    if covers:
        out["covers"] = covers
    for key in ("story", "acceptance", "boundaries"):
        if entry.get(key) is not None:
            out[key] = entry[key]
    for key in ("grounding", "system_tests"):
        if entry.get(key):
            out[key] = entry[key]
    out.update(_obligations_for(doc, task_id))
    if prior_attempt:
        out["prior_attempt"] = prior_attempt
    return {k: out[k] for k in _ENVELOPE_ORDER if k in out}


def _task(rest):
    """Emit (or --write) the build envelope for a single task. --write is an explicit
    path ARGUMENT used as-given (the driver passes the worktree-resolved current_task
    path), never re-anchored on the host config."""
    p = common.base_parser("stage task")
    p.add_argument("task_id")
    p.add_argument("--write", help="write the envelope to this path instead of stdout")
    p.add_argument("--prior-attempt", dest="prior_attempt",
                   help="the branch and block reason of an earlier attempt rebased into "
                        "this task's worktree")
    args = p.parse_args(rest)

    doc = common.load_yaml(common.resolve_path(args.config, "stage", None))
    entry = next((t for t in (doc.get("tasks") or [])
                  if isinstance(t, dict) and t.get("id") == args.task_id), None)
    if entry is None:
        common.die(f"task {args.task_id} not found in the stage")

    envelope = _envelope(doc, entry, args.prior_attempt)
    if args.write:
        out_path = Path(args.write)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(_dump(envelope))
        common.emit({"task_id": args.task_id, "written": str(out_path)}, args.format)
    else:
        common.emit(envelope, args.format)
    return 0


# ---------------------------------------------------------------------------
# stage materialize — inline the scenario text the working set carries
# ---------------------------------------------------------------------------

_TASK_FIELD_ORDER = ["id", "title", "covers", "story", "acceptance", "boundaries",
                     "grounding", "system_tests"]


def _ordered_task(task):
    """Rebuild a task dict in canonical field order (unknown fields keep their
    relative order at the end) so materialize output is stable and diffable."""
    out = {k: task[k] for k in _TASK_FIELD_ORDER if k in task}
    out.update({k: v for k, v in task.items() if k not in out})
    return out


def _system_test_entries(value):
    """Normalise `system_tests` — the role authors bare ids (`[SYS-TC-44]`), the
    materialized form is a list of entries."""
    return [dict(item) if isinstance(item, dict) else {"id": str(item)}
            for item in (value or [])]


def _description(scenario):
    """The one line the build stamps verbatim on the `[SYS-TC:]` tag: the scenario's
    title followed by its Given/When/Then, each joined to one line."""
    gwt = [f"{key.capitalize()} {_oneline(scenario.get(key))}"
           for key in ("given", "when", "then")
           if str(scenario.get(key) or "").strip()]
    title = _oneline(scenario.get("title") or "")
    return f"{title} — {'; '.join(gwt)}" if gwt else title


def _scenarios(config):
    """{SYS-TC id: description} over every scenario nested under a capability or a
    learning. A scenario proving two entries is duplicated under both keeping one id,
    so the first reading wins — `workset check` owns the drift between the copies."""
    out = {}
    paths = common.config_doc(config).get("paths") or {}
    root = common.project_root(config)
    for key in ("capabilities", "learnings"):
        rel = paths.get(key)
        path = (root / rel).resolve() if rel else None
        if not path or not path.is_file():
            continue
        for entry in (common.load_yaml(path, optional=True).get(key) or []):
            if not isinstance(entry, dict):
                continue
            for scenario in (entry.get("system_tests") or []):
                if isinstance(scenario, dict) and scenario.get("id"):
                    out.setdefault(str(scenario["id"]), _description(scenario))
    return out


def _truncated(desc):
    """True if a description was cut off mid-sentence — it ends on a comma or a
    dangling function word. Build stamps it verbatim onto the [SYS-TC:] tag, so a
    truncated one must fail generation, not ship (L-065).
    Terminal punctuation ends the check: a scenario may legitimately close on a
    function word ("...nobody authored it onto."), and a cut string does not end on a
    full stop. Punctuating is the escape hatch — without one the only way past the lint
    is rewording a durable capability scenario to satisfy it (L-138)."""
    s = (desc or "").rstrip()
    if not s:
        return False
    if s[-1] in ".!?":
        return False
    if s[-1] == ",":
        return True
    words = re.findall(r"[A-Za-z']+", s)
    return bool(words) and words[-1].lower() in _DANGLING_TAIL


def _leading_comments(text):
    """The file's opening comment block — re-prepended on rewrite so the header the
    role authored survives a machine edit."""
    out = []
    for line in text.splitlines(keepends=True):
        if not line.lstrip().startswith("#"):
            break
        out.append(line)
    return "".join(out)


def _materialize(rest):
    p = common.base_parser("stage materialize")
    args = p.parse_args(rest)

    stage_path = common.resolve_path(args.config, "stage", None)
    doc = common.load_yaml(stage_path)
    scenarios = _scenarios(args.config)

    errors, count = [], 0
    tasks = doc.get("tasks") or []
    for i, t in enumerate(tasks):
        if not isinstance(t, dict):
            continue
        tid = t.get("id", "<no-id>")
        systests = _system_test_entries(t.get("system_tests"))
        for st in systests:
            desc = scenarios.get(str(st.get("id")))
            if desc is None:
                errors.append(f"{tid}: system_tests names {st.get('id')}, which no "
                              f"capability or learning carries a scenario for")
                continue
            st["description"] = desc
            if _truncated(desc):
                errors.append(f"{tid}: system_tests {st.get('id')} description looks "
                              f"truncated (ends '…{desc[-40:].strip()}') — complete the "
                              f"scenario where it is authored")
            count += 1
        if systests:
            t["system_tests"] = systests
        tasks[i] = _ordered_task(t)

    if errors:
        common.emit({"verdict": "fail", "errors": errors}, args.format)
        return 1

    doc["tasks"] = tasks
    old = stage_path.read_text()
    rendered = _leading_comments(old) + _dump(doc)
    if rendered != old:
        stage_path.write_text(rendered)
    common.emit({"stage": doc.get("stage"), "tasks": len(tasks),
                 "system_tests": count, "verdict": "pass"}, args.format)
    return 0


# ---------------------------------------------------------------------------
# stage check — the single gate
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


def _contract_findings(t):
    """B8/B11/B9/B10 — the four-section contract's shape."""
    tid = t.get("id", "<no-id>")
    out = [("error", "B8", m) for m in _story_findings(tid, t.get("story"))]
    if not str(t.get("title") or "").strip():
        out.append(("error", "B11", f"{tid}: no title — one line naming what this task "
                                    f"delivers, which the build makes its commit subject"))
    if not str(t.get("boundaries") or "").strip():
        out.append(("error", "B9", f"{tid}: no boundaries — one merged section declares "
                                   f"out-of-scope, read-only files, and fixed interfaces"))
    for field in _RETIRED_BOUNDARY_FIELDS:
        if field in t:
            out.append(("error", "B9", f"{tid}: carries '{field}' — boundaries is a single "
                                       f"section, so nothing else may state a limit the "
                                       f"build must honour"))
    for field in _RETIRED_SPEC_FIELDS:
        if field in t:
            out.append(("error", "B9", f"{tid}: carries '{field}', a field the four-section "
                                       f"contract schema retired (story / acceptance / "
                                       f"boundaries / grounding)"))
    for ac in (t.get("acceptance") or []):
        if not isinstance(ac, dict):
            out.append(("error", "B10", f"{tid}: an acceptance entry is not a mapping"))
            continue
        if not str(ac.get("id") or "").strip():
            out.append(("error", "B10", f"{tid}: an acceptance entry has no id"))
        if not str(ac.get("criterion") or "").strip():
            out.append(("error", "B10", f"{tid}: acceptance {ac.get('id', '<no-id>')} has no "
                                        f"criterion — the criterion is the requirement"))
    return out


def _proof_findings(t, target_owners):
    """B3 — every criterion carries proof; B7 — every test entry names its shape;
    B6/C4 — an e2e task's scenarios are materialized and it carries no acceptance."""
    tid = t.get("id", "<no-id>")
    out = []
    for ac in (t.get("acceptance") or []):
        if not isinstance(ac, dict):
            continue
        acid = ac.get("id", "<no-id>")
        tests = ac.get("tests") or []
        if not tests and not str(ac.get("verified_by") or "").strip():
            out.append(("error", "B3", f"{tid}: acceptance {acid} has no tests and no "
                                       f"verified_by (silent hole)"))
        for te in tests:
            if not isinstance(te, dict):
                out.append(("error", "B7", f"{tid}: acceptance {acid} has a test entry that "
                                           f"is not a mapping"))
                continue
            lvl = te.get("level")
            if lvl not in ("unit", "integration"):
                out.append(("error", "B7", f"{tid}: acceptance {acid} test declares level "
                                           f"'{lvl}' — must be unit or integration"))
            if lvl == "integration" and not str(te.get("seam") or "").strip():
                out.append(("error", "B7", f"{tid}: acceptance {acid} integration test names "
                                           f"no seam — say which boundary it crosses"))
            target = str(te.get("target") or "").strip()
            if not target:
                out.append(("error", "B7", f"{tid}: acceptance {acid} test names no target"))
            else:
                target_owners.setdefault(_target_key(target), []).append(tid)
    systests = t.get("system_tests") or []
    for st in systests:
        if not isinstance(st, dict):
            out.append(("error", "B6", f"{tid}: system_tests entry '{st}' is a bare id — "
                                       f"run `wf stage materialize`"))
        elif not str(st.get("description") or "").strip():
            out.append(("error", "B6", f"{tid}: system_tests entry {st.get('id')} has no "
                                       f"description — run `wf stage materialize`"))
    if systests and (t.get("acceptance") or []):
        out.append(("error", "C4", f"{tid}: e2e task carries acceptance — the SYS-TC is "
                                   f"the acceptance"))
    return out


def _pointer_findings(root, tid, task):
    """C11: grounding is pointers into code that already exists. A path that resolves
    to no file, or a `<path>:<symbol>` whose file carries no such symbol, is a claim
    asserted rather than checked."""
    out = []
    for item in _grounding_items(task):
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


def _trace_findings(root, t, known_ids, ids_resolved):
    """C6/C16 — the task names a driver that is open in the working set; C11 —
    grounding resolves; C19 — a stage carries no ordering."""
    tid = t.get("id", "<no-id>")
    out = []
    covers = runstate.covers_of(t)
    if not covers:
        out.append(("error", "C6", f"{tid}: no covers — name the capability or learning "
                                   f"this task serves"))
    for cid in covers:
        if not _DRIVER_ID_RE.fullmatch(cid):
            out.append(("error", "C6", f"{tid}: covers '{cid}' — covers names CAP/L ids only"))
        elif ids_resolved and cid not in known_ids:
            out.append(("error", "C16", f"{tid}: covers {cid}, which is not open in the "
                                        f"capabilities or learnings working set"))
    if covers and not ids_resolved:
        out.append(("warn", "C16", f"{tid}: covers ids unverified — no capabilities or "
                                   f"learnings file resolved from config"))
    out += [("error", "C11", m) for m in _pointer_findings(root, tid, t)]
    for field in _FORBIDDEN_TASK_FIELDS:
        if field in t:
            out.append(("error", "C19", f"{tid}: carries '{field}' — a stage IS the set of "
                                        f"tasks with no dependency between them, so an edge "
                                        f"inside one is a decomposition error: push the "
                                        f"dependent task to the next stage"))
    return out


def _target_key(target):
    """(package, name) for a test target. `pkg/file.go:TestX` keys on its directory;
    a bare `TestX` keys on the unqualified bucket. Two contracts landing the same key
    collide at build time in separate worktrees (L-088)."""
    token = str(target).strip()
    path_part, sep, name = token.rpartition(":")
    if not sep:
        return ("", token)
    return (str(PurePosixPath(path_part).parent) if "/" in path_part else "", name)


def _collision_findings(target_owners):
    """C13: two contracts writing the same test name in the same package collide in
    separate worktrees and the second one silently loses (L-088)."""
    out = []
    for (package, name), owners in sorted(target_owners.items()):
        distinct = sorted(set(owners))
        if len(distinct) > 1:
            where = f"in {package}" if package else "unqualified"
            out.append(f"tasks {distinct} all target '{name}' {where} — give each contract "
                       f"its own test name")
    return out


def _overlap_findings(tasks):
    """C20: the file partition, as far as a mechanical check reaches — two tasks
    centred on one file collide at merge. A warning, not an error: grounding is
    pointers, and an incidental shared read is legitimate."""
    owners = {}
    for t in tasks:
        tid = t.get("id", "<no-id>")
        for item in _grounding_items(t):
            for token in _grounding_pointers(item)[0]:
                if tid not in owners.setdefault(token, []):
                    owners[token].append(tid)
    return [f"tasks {sorted(ids)} both ground on '{token}' — two tasks in a stage may not "
            f"centre on the same file: factor the shared part out, or push one to the next "
            f"stage (an incidental shared read is legitimate)"
            for token, ids in sorted(owners.items()) if len(ids) > 1]


def _shape_text(value):
    """One line of a design field's content. `flow` and `checkpoint` are lists of
    per-task entries, so each reads as its authored prose with the task key dropped —
    the id is routing, not content, and would read as authored text to the placeholder
    check."""
    if isinstance(value, (list, tuple)):
        parts = [" ".join(str(v) for k, v in item.items() if k not in ("task", "tasks"))
                 if isinstance(item, dict) else str(item) for item in value]
        return _oneline("; ".join(parts))
    return _oneline(value or "")


def _shape_findings(doc):
    """A6: the design fields the build envelope carries hold real content. Without
    them the build agent has no shape to build into and reads its contract blind."""
    out = []
    for key in _PROSE_SHAPE_FIELDS:
        text = _shape_text(doc.get(key))
        if not text:
            out.append(f"stage: no '{key}' — the build envelope carries the stage's shape "
                       f"(goal, allocation, flow, checkpoint)")
        elif _PLACEHOLDER_RE.search(text) or "<" in text and ">" in text:
            out.append(f"stage: '{key}' is still a template placeholder ('{text[:60]}…')")
    return out


# The header fields authored per task, and what each one leaves a build without when it
# names no task: the envelope is filtered by task id, so an unnamed task gets the key
# omitted entirely. `nfr` and `authz` may instead carry an explicit deferral, which is a
# statement about the whole stage and names no task by design.
_KEYED_SHAPE = ("allocation", "flow", "checkpoint")
_KEYED_OPTIONAL = ("nfr", "authz")


def _entry_tasks(entry) -> list:
    listed = entry.get("tasks")
    if isinstance(listed, (list, tuple)):
        return [str(t) for t in listed]
    return [str(entry["task"])] if entry.get("task") else []


def _keying_findings(doc, task_ids) -> list:
    """A19: every per-task header entry names a task this stage carries, and every task
    is named by every field that shapes it."""
    out = []
    for key in _KEYED_SHAPE + _KEYED_OPTIONAL:
        entries = [e for e in (doc.get(key) or []) if isinstance(e, dict)]
        for entry in entries:
            named = _entry_tasks(entry)
            if not named:
                if key in _KEYED_OPTIONAL and ("deferred" in entry or "why" in entry):
                    continue
                out.append(f"stage: a '{key}' entry names no task — the build envelope is "
                           f"filtered by task id, so this entry reaches no build")
                continue
            for task in named:
                if task not in task_ids:
                    out.append(f"stage: '{key}' names task {task}, which this stage does "
                               f"not carry — it reaches no build")
        if key in _KEYED_SHAPE:
            covered = {t for e in entries for t in _entry_tasks(e)}
            for task in sorted(task_ids - covered):
                out.append(f"{task}: no '{key}' entry names it — its contract would ship "
                           f"without the shape it builds into")
    supersessions = [s for s in (doc.get("supersessions") or []) if isinstance(s, dict)]
    for entry in supersessions:
        successor = str(entry.get("successor") or "")
        if successor and successor not in task_ids and "no successor" not in successor:
            out.append(f"stage: a supersession names successor {successor}, which this "
                       f"stage does not carry")
    return out


def _serves_findings(served):
    """A7: the serves header names what this cut is for. The driver folds it into the
    PR body, so an id left out never reaches the review."""
    if not served:
        return ["stage: no 'serves:' — name every capability and learning this stage "
                "serves, written out in full (CAP-n / L-n)"]
    return [f"stage: serves '{s}', which is not a CAP/L id — write the ids out in full "
            f"(CAP-n / L-n)" for s in served if not _DRIVER_ID_RE.fullmatch(s)]


# Statuses that take a capability off the table for a design cut. `parked` is waiting on a
# PO session to re-word a promise nobody could prove; `proposed` was minted by the residue
# exit from a defect residual and carries machine-written words, so it waits on a PO
# session too. Designing against either is designing against something no human has agreed.
NOT_CUTTABLE = ("parked", "proposed")


def _open_capabilities(config) -> list:
    """The capability ids a cut could actually have taken."""
    paths = common.config_doc(config).get("paths") or {}
    rel = paths.get("capabilities")
    if not rel:
        return []
    path = (common.project_root(config) / rel).resolve()
    if not path.exists():
        return []
    return [str(e["id"]) for e in (common.load_yaml(path, optional=True)
                                   .get("capabilities") or [])
            if isinstance(e, dict) and e.get("id")
            and str(e.get("status") or "").strip() not in NOT_CUTTABLE]


def _capability_rank_findings(config, doc, served):
    """A20: capability work outranks learnings. A stage serving only learnings while an
    unparked capability is open advances no promise, and nothing else in the gate can
    see that — every such stage is individually well-formed. `no_capability_reason:`
    is the deliberate escape: state why and the cut stands."""
    if any(str(s).upper().startswith("CAP-") for s in served):
        return []
    if str(doc.get("no_capability_reason") or "").strip():
        return []
    open_caps = _open_capabilities(config)
    if not open_caps:
        return []
    return [f"stage: serves no capability while {len(open_caps)} is/are open and "
            f"unparked ({', '.join(open_caps[:5])}"
            f"{', …' if len(open_caps) > 5 else ''}) — cut capability work, or say why "
            f"this stage cannot in `no_capability_reason:`"]


def _design_issue_findings(config, task_ids):
    """C18: the design role drains an issue by naming the successor task that answers
    it, so a resolution pointing at a task this stage does not carry resolved nothing.
    `fix_kind: no_change` is the legitimate nothing-to-do. An absent or unreadable file
    is silent — the issues are transient run state, not a gate input."""
    rel = (common.config_doc(config).get("paths") or {}).get("design_issues")
    path = (common.project_root(config) / rel).resolve() if rel else None
    if not path or not path.is_file():
        return []
    try:
        doc = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return []
    out = []
    for item in (doc.get("issues") or []) if isinstance(doc, dict) else []:
        if not isinstance(item, dict) or str(item.get("status") or "") != "resolved":
            continue
        if str(item.get("fix_kind") or "") == "no_change":
            continue
        successor = str(item.get("task") or "").strip()
        if not successor:
            out.append(f"{item.get('id')}: resolved with no successor task — a design "
                       f"issue drains by authoring the task that answers it and naming "
                       f"it here (`task: S<n>-T<n>`), or by `fix_kind: no_change` with "
                       f"a reason")
        elif successor not in task_ids:
            out.append(f"{item.get('id')}: resolved naming successor task '{successor}', "
                       f"which this stage does not carry — a design issue drains by "
                       f"authoring the task that answers it (or fix_kind: no_change)")
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


def _header_findings(args, doc, text, tasks):
    """(findings, adr_sets, citations) for the stage header: A7, A6, A12, C14, C18,
    C20, and A4/A5 over every ADR the stage cites."""
    out = [("error", "A7", m) for m in _serves_findings(_serves(doc))]
    out += [("error", "A20", m) for m in
            _capability_rank_findings(args.config, doc, _serves(doc))]
    out += [("error", "A6", m) for m in _shape_findings(doc)]
    out += [("error", "A19", m) for m in
            _keying_findings(doc, {str(t.get("id")) for t in tasks if t.get("id")})]

    allocated = [a.get("component") if isinstance(a, dict) else a
                 for a in (doc.get("allocation") or [])]
    out += [("error", "A12", m) for m in mdread.architecture_findings(
        allocated, mdread.architecture_text(args.config),
        *mdread.repo_components(args.config))]

    cap = mdread.limit(args.config, "tasks_per_stage")
    if len(tasks) > cap:
        out.append(("error", "C14", f"the stage decomposes into {len(tasks)} tasks, over "
                                    f"limits.tasks_per_stage ({cap}) — cut a narrower stage"))
    out += [("error", "C18", m) for m in
            _design_issue_findings(args.config, {t.get("id") for t in tasks})]
    out += [("warn", "C20", m) for m in _overlap_findings(tasks)]

    paths = common.config_doc(args.config).get("paths") or {}
    index = mdread.adr_index(common.project_root(args.config), paths.get("transient"))
    adr_errors, citations = mdread.adr_citations(text, index)
    out += [("error", e["code"], e["msg"]) for e in adr_errors]
    adr_sets = sorted({str(Path(p).parent) for defs in index.values() for p, _ in defs})
    return out, adr_sets, citations


def _check(rest):
    p = common.base_parser("stage check")
    p.add_argument("--strict", action="store_true", help="treat warnings as errors")
    args = p.parse_args(rest)

    path = common.resolve_path(args.config, "stage", None)
    if not path.exists():
        common.die(f"stage not found: {path}")
    text = path.read_text()
    doc = common.load_yaml(path)
    tasks = [t for t in (doc.get("tasks") or []) if isinstance(t, dict)]
    root = common.project_root(args.config)

    findings, target_owners = [], {}
    known_ids, ids_resolved = _known_driver_ids(args.config)
    for t in tasks:
        findings += _contract_findings(t)
        findings += _proof_findings(t, target_owners)
        findings += _trace_findings(root, t, known_ids, ids_resolved)
    findings += [("error", "C13", m) for m in _collision_findings(target_owners)]

    header, adr_sets, citations = _header_findings(args, doc, text, tasks)
    findings += header

    errors = [f for f in findings if f[0] == "error"]
    warns = [f for f in findings if f[0] == "warn"]
    result = {
        "stage": doc.get("stage"),
        "serves": _serves(doc),
        "tasks": len(tasks),
        "adr_sets": adr_sets,
        "adr_citations": citations,
        "errors": [{"code": c, "msg": m} for _, c, m in errors],
        "warnings": [{"code": c, "msg": m} for _, c, m in warns],
        "verdict": "fail" if errors or (args.strict and warns) else "pass",
    }
    common.emit(result, args.format)
    return 1 if result["verdict"] == "fail" else 0


COMMANDS = {
    ("stage", "task"): _task,
    ("stage", "materialize"): _materialize,
    ("stage", "check"): _check,
}
