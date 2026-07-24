"""wf slice — mechanical checks over the design-slice (the SA→TL handover).

``slice check`` verifies two things, both surfaced while the SA session (and the
human) is still there rather than after a full decomposition:

- **A3** — no assumption is still marked UNCONFIRMED (the same finding the sprint
  gate reports later).
- **A4/A5** — every ``ADR-NNN`` the slice cites resolves to exactly one ADR file.
  A legacy repo can carry a second ADR namespace outside ``paths.adrs`` with
  colliding ids, so the index is built from every ADR-shaped file in the tree; a
  colliding id must be cited with its path. Resolved citations are echoed with the
  ADR's own title, so a citation pointing at the wrong decision is visible.

Exits non-zero on any error finding.
"""
from __future__ import annotations

import re
from pathlib import Path

import common

_ASSUMPTIONS_HEADER = "Assumptions requiring confirmation"
_SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "dist", "build"}
_ADR_FILE_RE = re.compile(r"^ADR-(\d+)")
_ADR_CITE_RE = re.compile(r"(?:(?P<dir>[\w./-]+)/)?ADR-(?P<num>\d+)")
# A dir capture that is itself a bare spec id (the `REQ-216/ADR-009` two-id shorthand) is
# not a filesystem path — strip it so the citation resolves as a bare ADR-NNN.
_SPEC_ID_RE = re.compile(r"(?:REQ|ADR|CAP|SYS-TC|L)-\d+")


def section(text, header):
    """The markdown block under `## <header>` up to the next `## ` heading (or EOF)."""
    m = re.search(rf"^##\s+{re.escape(header)}\s*$", text, re.MULTILINE)
    if not m:
        return ""
    rest = text[m.end():]
    nxt = re.search(r"^##\s", rest, re.MULTILINE)
    return rest[: nxt.start()] if nxt else rest


def unconfirmed_assumptions(text):
    """The A3 findings for a slice: one message per UNCONFIRMED assumption line."""
    msgs = []
    for line in section(text, _ASSUMPTIONS_HEADER).splitlines():
        if re.search(r"\bUNCONFIRMED\b", line):
            ident = re.search(r"\b(A-\d+)\b", line)
            who = ident.group(1) if ident else "an assumption"
            msgs.append(f"slice: {who} is UNCONFIRMED — close SA alignment before build")
    return msgs


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


def _check(rest):
    p = common.base_parser("slice check")
    p.add_argument("--slice", help="path to the design-slice (default: paths.design_slice)")
    args = p.parse_args(rest)

    path = common.resolve_path(args.config, "design_slice", args.slice)
    if not path.exists():
        common.die(f"design-slice not found: {path}")

    text = path.read_text()
    errors = [{"code": "A3", "msg": m} for m in unconfirmed_assumptions(text)]
    index = adr_index(common.project_root(args.config))
    adr_errors, citations = adr_citations(text, index)
    errors += adr_errors
    adr_sets = sorted({str(Path(p).parent) for defs in index.values() for p, _ in defs})
    result = {
        "slice": str(path),
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
