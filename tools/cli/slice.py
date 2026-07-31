"""wf slice — mechanical checks over the design-slice (the designer→TL handover).

``slice check`` verifies what a script can verify before any increment is decomposed:

- **A4/A5** — every ``ADR-NNN`` the slice cites resolves to exactly one ADR file.
  A legacy repo can carry a second ADR namespace outside ``paths.adrs`` with
  colliding ids, so the index is built from every ADR-shaped file in the tree; a
  colliding id must be cited with its path. Resolved citations are echoed with the
  ADR's own title, so a citation pointing at the wrong decision is visible.
- **A6** — the ``## Design narrative`` section exists and holds prose (comments and
  blanks do not count).
- **A7** — the ``**Serves:**`` header exists and names its CAP/L ids in full.
- **A8** — the ``## Claimed scope`` section exists and holds prose: an iteration is
  deliberately partial, so what it claims of each served promise is the input the
  design-time adequacy review judges against.
- **A9** — ``## Increments`` holds ``### Increment <n>`` blocks numbered 1..N in
  order, and N is within ``limits.increments_per_sprint``.
- **A10** — every ``## System test cases`` case carries a ``**Covers:**`` line
  naming at least one CAP or L id (a learning-driven scenario covers an L id).
- **A11** — every CAP on the ``**Serves:**`` header is covered by at least one case
  the parser read. A10 speaks only for cases it recognised, so a slice whose case
  syntax drifted yields zero scenarios and would otherwise pass in silence. L-ids
  are exempt.

Exits non-zero on any error finding.
"""
from __future__ import annotations

import re
from pathlib import Path

import common

_NARRATIVE_HEADER = "Design narrative"
_CLAIMED_SCOPE_HEADER = "Claimed scope"
_INCREMENTS_HEADER = "Increments"
_SYSTEM_TESTS_HEADER = "System test cases"
_SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "dist", "build"}
_ADR_FILE_RE = re.compile(r"^ADR-(\d+)")
_ADR_CITE_RE = re.compile(r"(?:(?P<dir>[\w./-]+)/)?ADR-(?P<num>\d+)")
# A dir capture that is itself a bare spec id (the `CAP-216/ADR-009` two-id shorthand) is
# not a filesystem path — strip it so the citation resolves as a bare ADR-NNN.
_SPEC_ID_RE = re.compile(r"(?:ADR|CAP|SYS-TC|L)-\d+")
_SERVES_RE = re.compile(r"^\s*\*\*Serves:\*\*\s*(.*)$", re.MULTILINE)
_DRIVER_ID_RE = re.compile(r"\b(?:CAP|L)-\d+\b")
_INCREMENT_HEAD_RE = re.compile(r"^###\s+Increment\s+(\d+)\b(.*)$", re.MULTILINE)
_TC_HEAD_RE = re.compile(r"-\s+\*\*(SYS-TC-\d+):\*\*\s*(.*)")


def section(text, header):
    """The markdown block under `## <header>` up to the next `## ` heading (or EOF)."""
    m = re.search(rf"^##\s+{re.escape(header)}\s*$", text, re.MULTILINE)
    if not m:
        return ""
    rest = text[m.end():]
    nxt = re.search(r"^##\s", rest, re.MULTILINE)
    return rest[: nxt.start()] if nxt else rest


def _prose(body):
    """The section body with html comments stripped — what "holds prose" means."""
    return re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL).strip()


def serves_ids(text):
    """The CAP/L ids on the slice's `**Serves:**` header line(s), in order."""
    out = []
    for line in _SERVES_RE.findall(text):
        for ident in _DRIVER_ID_RE.findall(line):
            if ident not in out:
                out.append(ident)
    return out


def increment_key(value):
    """Sort key over increment labels: an absent label first, then numbers in numeric
    order, then any non-numeric label alphabetically — so increment 10 follows 9, not 1."""
    if value is None:
        return (-1, 0, "")
    try:
        return (0, int(value), "")
    except (TypeError, ValueError):
        return (1, 0, str(value))


def increments(text):
    """[(n, title)] for every `### Increment <n>` block under `## Increments`, in
    file order."""
    return [(int(n), rest.lstrip(" —-").strip())
            for n, rest in _INCREMENT_HEAD_RE.findall(section(text, _INCREMENTS_HEADER))]


def increment_section(text, number):
    """The verbatim `### Increment <number>` block — the stage narrative that rides in
    every task envelope. '' when the slice declares no such increment."""
    body = section(text, _INCREMENTS_HEADER)
    for m in _INCREMENT_HEAD_RE.finditer(body):
        if int(m.group(1)) != int(number):
            continue
        rest = body[m.start():]
        nxt = _INCREMENT_HEAD_RE.search(rest, m.end() - m.start())
        return (rest[: nxt.start()] if nxt else rest).strip("\n")
    return ""


def missing_narrative(text):
    """The A6 findings: the Design narrative section is absent, or holds nothing but
    comments and blank lines."""
    if not re.search(rf"^##\s+{_NARRATIVE_HEADER}\s*$", text, re.MULTILINE):
        return ["slice: no '## Design narrative' section — write the change's story "
                "(shape, end-to-end flow with wiring, each component's role) before handover"]
    if not _prose(section(text, _NARRATIVE_HEADER)):
        return ["slice: '## Design narrative' holds no prose — comments are not a narrative"]
    return []


def missing_serves(text):
    """The A7 findings: no `**Serves:**` header, or one naming no CAP/L id. The header
    is the drain anchor at sprint close, so an id left out never drains."""
    if not _SERVES_RE.search(text):
        return ["slice: no '**Serves:**' header — name every capability and learning "
                "this sprint serves, written out in full (CAP-n / L-n)"]
    if not serves_ids(text):
        return ["slice: the '**Serves:**' header names no CAP/L id — write the ids out in full"]
    return []


def missing_claimed_scope(text):
    """The A8 findings: no `## Claimed scope`, or one holding no prose."""
    if not re.search(rf"^##\s+{_CLAIMED_SCOPE_HEADER}\s*$", text, re.MULTILINE):
        return ["slice: no '## Claimed scope' section — state what this iteration delivers "
                "of each served promise, and what it knowingly leaves"]
    if not _prose(section(text, _CLAIMED_SCOPE_HEADER)):
        return ["slice: '## Claimed scope' holds no prose — an iteration that claims "
                "nothing cannot be judged adequate"]
    return []


def increment_findings(text, cap):
    """The A9 findings: increments present, numbered 1..N in order, and within the cap."""
    if not re.search(rf"^##\s+{_INCREMENTS_HEADER}\s*$", text, re.MULTILINE):
        return ["slice: no '## Increments' section — cut the sprint into ordered "
                "'### Increment <n>' blocks, each with a goal, an allocation, the "
                "end-to-end flow, and an observable checkpoint"]
    found = increments(text)
    if not found:
        return ["slice: '## Increments' declares no '### Increment <n>' block"]
    msgs = []
    numbers = [n for n, _ in found]
    if numbers != list(range(1, len(numbers) + 1)):
        msgs.append(f"slice: increments are numbered {numbers} — number them 1..{len(numbers)} "
                    f"in the order they are built")
    if len(found) > cap:
        msgs.append(f"slice: {len(found)} increments exceeds limits.increments_per_sprint "
                    f"({cap}) — cut a smaller sprint")
    return msgs


def testcases(text):
    """{SYS-TC id: [CAP/L ids]} for every `## System test cases` case the parser reads. A
    case whose head syntax drifted is absent from the map — the sprint's materializer
    reads the same shape, so what does not parse here does not ship either."""
    cases, current = {}, None
    for line in section(text, _SYSTEM_TESTS_HEADER).splitlines():
        head = _TC_HEAD_RE.search(line)
        if head:
            current = head.group(1)
            cases.setdefault(current, [])
            continue
        if current and "**Covers:**" in line:
            cases[current] += _DRIVER_ID_RE.findall(line.split("**Covers:**", 1)[1])
    return cases


def testcase_covers_findings(text):
    """The A10 findings: a SYS-TC case with no `**Covers:**` line naming a driver. A
    scenario proves a capability or a learning; what it covers is what the close-time
    adequacy gate reads."""
    return [f"slice: {tc} carries no '**Covers:**' line — name the capability or "
            f"learning the scenario proves"
            for tc, ids in testcases(text).items() if not ids]


def serves_coverage_findings(text):
    """The A11 findings: a CAP on the `**Serves:**` header that no PARSED case covers.
    A10 only speaks for cases it read, so a slice whose case syntax drifted yields zero
    scenarios and passes in silence; this names the capability that would ship unproven.
    L-ids are exempt — a learning needs no scenario."""
    covered = {i for ids in testcases(text).values() for i in ids}
    return [f"slice: {cid} is served but no parsed system test case covers it — write a "
            f"'- **SYS-TC-<n>:** <case>' case with a '**Covers:** {cid}' line"
            for cid in serves_ids(text)
            if cid.startswith("CAP-") and cid not in covered]


def _adr_title(path):
    """The ADR's own title — what makes a mis-pointed citation visible. The canonical
    shape carries it in the frontmatter; a legacy set may lead with a heading instead."""
    heading = ""
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("title:"):
            return line.split(":", 1)[1].strip().strip("\"'")
        if not heading and line.startswith("# "):
            heading = line[2:].strip()
    return heading


def adr_index(root):
    """{ADR-NNN: [(relpath, title)]} over every ADR-shaped file in the tree, not just
    paths.adrs — a legacy repo can hold a second, id-colliding ADR set."""
    index = {}
    for path in sorted(root.rglob("ADR-*.md")):
        rel = path.relative_to(root)
        if any(seg in _SKIP_DIRS for seg in rel.parts):
            continue
        m = _ADR_FILE_RE.match(path.name)
        if m:
            index.setdefault(f"ADR-{m.group(1)}", []).append((str(rel), _adr_title(path)))
    return index


def adr_citations(text, index):
    """(errors, resolved) for the slice's ADR citations, against the tree's index."""
    errors, resolved, seen = [], [], set()
    for m in _ADR_CITE_RE.finditer(text):
        adr_id = f"ADR-{m.group('num')}"
        cited_dir = m.group("dir")
        if cited_dir and _SPEC_ID_RE.fullmatch(cited_dir):
            cited_dir = None
        key = (cited_dir, adr_id)
        if key in seen:
            continue
        seen.add(key)
        defs = index.get(adr_id) or []
        if not defs:
            errors.append({"code": "A4", "msg": f"slice: cites {adr_id}, which no ADR "
                                                f"file in the repo defines"})
            continue
        if cited_dir:
            hit = [d for d in defs if d[0].startswith(f"{cited_dir}/")]
            if not hit:
                where = ", ".join(d[0] for d in defs)
                errors.append({"code": "A4", "msg": f"slice: cites {cited_dir}/{adr_id}, "
                                                    f"which does not exist — {adr_id} is "
                                                    f"defined at {where}"})
                continue
            defs = hit
        elif len(defs) > 1:
            where = ", ".join(d[0] for d in defs)
            errors.append({"code": "A5", "msg": f"slice: {adr_id} is defined in more than "
                                                f"one ADR set ({where}) — cite it with its "
                                                f"path so the decision is unambiguous"})
            continue
        resolved.append({"id": adr_id, "path": defs[0][0], "title": defs[0][1]})
    return errors, resolved


def limit(config, key):
    """Read limits.<key> — a trust knob with no in-code default (the config template
    owns the value, so a check and a role never disagree about the cap)."""
    limits = common.config_doc(config).get("limits")
    if not isinstance(limits, dict) or key not in limits:
        common.die(f"limits.{key} not set in {config} — add it (see the config template)")
    return int(limits[key])


def _check(rest):
    p = common.base_parser("slice check")
    p.add_argument("--slice", help="path to the design-slice (default: paths.design_slice)")
    args = p.parse_args(rest)

    path = common.resolve_path(args.config, "design_slice", args.slice)
    if not path.exists():
        common.die(f"design-slice not found: {path}")

    text = path.read_text()
    errors = [{"code": "A6", "msg": m} for m in missing_narrative(text)]
    errors += [{"code": "A7", "msg": m} for m in missing_serves(text)]
    errors += [{"code": "A8", "msg": m} for m in missing_claimed_scope(text)]
    errors += [{"code": "A9", "msg": m}
               for m in increment_findings(text, limit(args.config, "increments_per_sprint"))]
    errors += [{"code": "A10", "msg": m} for m in testcase_covers_findings(text)]
    errors += [{"code": "A11", "msg": m} for m in serves_coverage_findings(text)]
    index = adr_index(common.project_root(args.config))
    adr_errors, citations = adr_citations(text, index)
    errors += adr_errors
    adr_sets = sorted({str(Path(p).parent) for defs in index.values() for p, _ in defs})
    result = {
        "slice": str(path),
        "serves": serves_ids(text),
        "increments": [{"n": n, "title": t} for n, t in increments(text)],
        "adr_sets": adr_sets,
        "adr_citations": citations,
        "errors": errors,
        "verdict": "fail" if errors else "pass",
    }
    common.emit(result, args.format)
    return 1 if errors else 0


COMMANDS = {
    ("slice", "check"): _check,
}
