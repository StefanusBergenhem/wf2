"""Shared markdown readers and the architecture bind.

Three unrelated callers need to read a markdown block by heading — the ruling brief's
``## Ruling``, an adequacy digest's ``## Residuals``, and a stage's PR-body material —
so ``section``/``_prose`` live here rather than in any one of them.

The rest is the architecture bind that outlived the design-slice:

- **A4/A5** — every ``ADR-NNN`` a stage cites resolves to exactly one ADR file. A legacy
  repo can carry a second ADR namespace outside ``paths.adrs`` with colliding ids, so the
  index is built from every ADR-shaped file in the tree; a colliding id must be cited with
  its path. Resolved citations are echoed with the ADR's own title, so a citation pointing
  at the wrong decision is visible.
- **A12** — every component a stage's ``allocation:`` names is one the repo already carries
  (discover's derived model at ``paths.discover_model``) or the architecture map at
  ``paths.architecture`` names (the planned delta). A component in neither is an
  architecture change, which is the SA session's call, not the design's.

This module exposes no verb. It is read-side only.
"""
from __future__ import annotations

import json
import re

import common

_SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "dist", "build"}
_ADR_FILE_RE = re.compile(r"^ADR-(\d+)")
_ADR_CITE_RE = re.compile(r"(?:(?P<dir>[\w./-]+)/)?ADR-(?P<num>\d+)")
# A dir capture that is itself a bare spec id (the `CAP-216/ADR-009` two-id shorthand) is
# not a filesystem path — strip it so the citation resolves as a bare ADR-NNN.
_SPEC_ID_RE = re.compile(r"(?:ADR|CAP|SYS-TC|L)-\d+")
_COMPONENTS_HEADER = "Components"
_COMPONENT_RE = re.compile(r"^\s*-\s+\*\*([^*]+)\*\*")
# A `(planned)` state marker, or the guidance parenthetical an author wraps onto the
# component line — neither is part of the component id.
_TRAILING_PAREN_RE = re.compile(r"\s*\([^()]*\)\s*$")
_PLACEHOLDER_RE = re.compile(r"^<.*>$")


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


def _component_id(raw):
    """One component id as written: emphasis, backticks and a trailing path separator
    stripped, a trailing marker or parenthetical dropped. '' for a template placeholder —
    an unauthored map names no component."""
    cid = _TRAILING_PAREN_RE.sub("", str(raw).strip().strip("`*").strip()).strip().strip("/")
    return "" if _PLACEHOLDER_RE.match(cid) else cid


def architecture_components(text):
    """The component ids the architecture map's `## Components` section carries — one per
    `- **<id>**` bullet, `(planned)` entries included. Deduped, in file order."""
    out = []
    for line in section(text, _COMPONENTS_HEADER).splitlines():
        m = _COMPONENT_RE.match(line)
        cid = _component_id(m.group(1)) if m else ""
        if cid and cid not in out:
            out.append(cid)
    return out


def _config_file(config, key):
    """The file `paths.<key>` names — None when the key is unset or nothing is there."""
    rel = (common.config_doc(config).get("paths") or {}).get(key)
    path = (common.project_root(config) / rel) if rel else None
    return path if path and path.is_file() else None


def architecture_text(config):
    """The architecture map's contents — '' when `paths.architecture` is unset or the file
    is absent. The map holds only the planned delta, so an empty one is a legitimate state."""
    path = _config_file(config, "architecture")
    return path.read_text() if path else ""


def repo_components(config):
    """(ids, present) from discover's model at `paths.discover_model` — the derived half of
    A12's inventory, re-read from the toolchain every cut instead of stored. Both the
    `id` the brief prints and the repo-relative `path` count: an allocation may name
    either. `present` is False for an absent, unreadable or empty model."""
    path = _config_file(config, "discover_model")
    try:
        model = json.loads(path.read_text()) if path else None
    except (ValueError, OSError):
        model = None
    nodes = (model or {}).get("nodes")
    if not isinstance(nodes, dict) or not nodes:
        return set(), False
    ids = set()
    for node in nodes.values():
        for key in ("id", "path"):
            value = (node or {}).get(key)
            if isinstance(value, str) and value.strip():
                ids.add(value.strip().strip("/"))
    return ids, True


def architecture_findings(allocated, arch_text, repo_ids, model_present):
    """The A12 findings over a stage's allocated component ids: one the repo does not carry
    and the architecture map does not name. A stage with no allocation is silent — there is
    nothing to bind. Nothing unresolved ever passes for want of the derived inventory: with
    the model missing, an unresolved component names the run that would settle it."""
    wanted = [cid for cid in (_component_id(c) for c in allocated) if cid]
    if not wanted:
        return []
    carried = set(architecture_components(arch_text))
    unresolved = []
    for cid in wanted:
        if cid not in carried and cid not in repo_ids and cid not in unresolved:
            unresolved.append(cid)
    if not unresolved:
        return []
    if not model_present:
        named = ", ".join(sorted(unresolved))
        return [f"stage: the allocation names components the architecture map "
                f"(paths.architecture) does not carry ({named}) and the derived component "
                f"inventory (paths.discover_model) is absent, so whether they exist cannot "
                f"be told — run wf-discover, then re-check"]
    return [f"stage: allocates '{cid}', which neither the repo (per paths.discover_model) "
            f"nor the architecture map (paths.architecture) carries — allocate only "
            f"components one of them names; a new component, a split or merge, or a new "
            f"dependency edge is an architecture change and routes through an SA session "
            f"(escalation criterion 5)"
            for cid in unresolved]


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


def adr_index(root, transient=None):
    """{ADR-NNN: [(relpath, title)]} over every ADR-shaped file in the tree, not just
    paths.adrs — a legacy repo can hold a second, id-colliding ADR set.

    `transient` (paths.transient) is pruned: it is derived and disposable, and it holds
    the per-task worktrees — each a whole checkout carrying its own copy of every ADR.
    Walking it finds the repo N+1 times over and reports the repo as colliding with
    itself."""
    skip_rel = None
    if transient:
        try:
            skip_rel = (root / transient).resolve().relative_to(root.resolve()).parts
        except ValueError:      # configured outside the repo — nothing to prune
            skip_rel = None
    index = {}
    for path in sorted(root.rglob("ADR-*.md")):
        rel = path.relative_to(root)
        if any(seg in _SKIP_DIRS for seg in rel.parts):
            continue
        if skip_rel and rel.parts[:len(skip_rel)] == skip_rel:
            continue
        m = _ADR_FILE_RE.match(path.name)
        if m:
            index.setdefault(f"ADR-{m.group(1)}", []).append((str(rel), _adr_title(path)))
    return index


def adr_citations(text, index):
    """(errors, resolved) for the ADR citations in `text`, against the tree's index."""
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
            errors.append({"code": "A4", "msg": f"stage: cites {adr_id}, which no ADR "
                                                f"file in the repo defines"})
            continue
        if cited_dir:
            hit = [d for d in defs if d[0].startswith(f"{cited_dir}/")]
            if not hit:
                where = ", ".join(d[0] for d in defs)
                errors.append({"code": "A4", "msg": f"stage: cites {cited_dir}/{adr_id}, "
                                                    f"which does not exist — {adr_id} is "
                                                    f"defined at {where}"})
                continue
            defs = hit
        elif len(defs) > 1:
            where = ", ".join(d[0] for d in defs)
            errors.append({"code": "A5", "msg": f"stage: {adr_id} is defined in more than "
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
