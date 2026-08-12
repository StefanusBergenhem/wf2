"""wf workset — the mechanical gate over the durable open work-set.

The open work-set is two files: ``paths.capabilities`` (user-voice promises) and
``paths.learnings`` (actionable observations). An entry in either MAY carry, nested
under it, the SYS-TC scenario set that would prove it::

    capabilities:
      - id: CAP-004
        statement: "..."
        system_tests:                 # optional — absent means "not taken up yet"
          - id: SYS-TC-12
            title: "..."
            given: "..."
            when: "..."
            then: "..."

There is no ``covers:`` field — the nesting names what a scenario proves. A scenario
proving two entries is duplicated under both keeping the SAME id: one test, one
``[SYS-TC:id]`` tag, satisfying both sets at drain time. Shipped-ness is never stored;
it derives from the tags in the test tree.

``workset check`` verifies:

- **A10** — every scenario is readable: an ``id`` of the form ``SYS-TC-<n>``, and a
  ``title`` plus ``given``/``when``/``then`` that all hold real content. A scenario the
  machinery cannot read is a scenario that does not ship.
- **A11** — the duplication guard: when one id appears under more than one entry, every
  copy carries identical text. Two copies drifting apart is the one failure mode
  duplication introduces, and it is closed here rather than by wording.
- **A13** — no id above ``id_counters.sys_tc``. An id over the high-water mark was never
  minted, so the counter did not move and the next cut mints it again.
- **A14** — no id repeated inside a single entry.
- **A15** *(warn)* — an id already tagged in the test tree whose work-set text differs
  from the shipped test's own description. A warning, not an error: an unshipped scenario
  is amendable freely, and re-wording a shipped one is a human escalation, not a linter's
  call.
- **A16** — a work-set file that is present but cannot be read. An ABSENT file is silent:
  the work-set legitimately starts empty.

An entry carrying no ``system_tests`` is a silent pass — it has not been taken up yet.

Exits non-zero on any error finding.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

import common

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "reconcile"))
from reconcile import DEFAULT_TEST_GLOBS, harvest  # noqa: E402

# The two files the work-set spans, each with the list key its document carries.
_FILES = (("capabilities", "capabilities"), ("learnings", "learnings"))
_ID_RE = re.compile(r"^SYS-TC-(\d+)$")
_STUB_RE = re.compile(r"\b(TODO|FIXME|TBD)\b", re.IGNORECASE)
# Every field a scenario must hold, in the order a reader meets them.
_FIELDS = ("title", "given", "when", "then")
_WORD_RE = re.compile(r"\w+", re.UNICODE)
# The tag's description reads "<title> — Given …; When …; Then …", so the keywords are
# rendering, not wording — they are dropped from both sides before comparing.
_GWT_WORDS = {"given", "when", "then"}


def counter(config, key):
    """Read ``id_counters.<key>`` from ``paths.repo_state`` — a high-water mark with no
    in-code default, so a check and the role that mints ids can never disagree about
    where the lane stands.

    It lives in its own file rather than in the config because the minting roles WRITE
    it: config.yaml is read-only intent, and a role that has to open it to bump a
    counter pays the whole commented file to change one integer — and commits it.
    """
    state = common.resolve_path(config, "repo_state")
    if not state.is_file():
        common.die(f"paths.repo_state does not exist: {state} — the id high-water marks "
                   f"live there; run wf-init against this repo")
    counters = common.load_yaml(state).get("id_counters")
    if not isinstance(counters, dict) or key not in counters:
        common.die(f"id_counters.{key} not set in {state} — add it "
                   f"(see the repo-state template)")
    try:
        return int(counters[key])
    except (TypeError, ValueError):
        common.die(f"id_counters.{key} in {state} is not a number: {counters[key]!r}")


def _blank(value):
    """True when a scenario field holds nothing a reader could act on: empty, a ``<…>``
    template placeholder, or a TODO/FIXME/TBD stub."""
    text = " ".join(str(value if value is not None else "").split())
    if not text:
        return True
    if text.startswith("<") and text.endswith(">"):
        return True
    return bool(_STUB_RE.search(text))


def words(text):
    """A wording key: the lower-case word tokens of `text`, with the Given/When/Then
    keywords dropped. Two texts differ when their WORDS differ — punctuation and the
    separators a renderer chooses are not part of the scenario."""
    return tuple(w for w in _WORD_RE.findall(str(text).lower()) if w not in _GWT_WORDS)


def scenario_words(scenario):
    """The wording key over a scenario's four fields, read as one text."""
    return words(" ".join(str(scenario.get(f) or "") for f in _FIELDS))


def read_file(config, key, list_key):
    """``{key, path, present, items, error}`` for one work-set file. A missing file is a
    silent empty; a present one that will not parse carries the A16 message instead of
    raising, so a broken work-set is reported like any other finding."""
    path = common.resolve_path(config, key, None)
    info = {"key": key, "path": str(path), "present": path.exists(),
            "items": [], "error": None}
    if not info["present"]:
        return info
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
    except (yaml.YAMLError, OSError) as exc:
        detail = " ".join(str(exc).split())[:200]
        info["error"] = (f"paths.{key} ({path}) will not parse — {detail}; the work-set "
                         f"cannot be gated until the file reads")
        return info
    if doc is None:
        return info
    if not isinstance(doc, dict):
        info["error"] = (f"paths.{key} ({path}) is not a YAML mapping — the file carries a "
                         f"top-level '{list_key}:' list of entries")
        return info
    raw = doc.get(list_key)
    if raw is None:
        return info
    if not isinstance(raw, list):
        info["error"] = (f"paths.{key} ({path}): '{list_key}:' is not a list — the entries "
                         f"are a YAML sequence")
        return info
    info["items"] = raw
    return info


def _label(entry, key, index):
    """What a finding calls an entry: its own id, or its position when it has none."""
    ident = entry.get("id") if isinstance(entry, dict) else None
    return str(ident).strip() if ident else f"{key}[{index}]"


def entry_findings(label, entry, high_water):
    """(records, findings) for one entry's scenario set — the A10/A13/A14 pass. An entry
    with no ``system_tests`` returns nothing at all: it has not been taken up yet."""
    raw = entry.get("system_tests")
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return [], [("error", "A10", f"{label}: system_tests is not a list — the scenario "
                                     f"set is a sequence of id/title/given/when/then "
                                     f"entries")]
    records, findings, seen = [], [], set()
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            findings.append(("error", "A10", f"{label}: system_tests[{index}] is not a "
                                             f"mapping ({item!r}) — a scenario carries an "
                                             f"id, a title and given/when/then"))
            continue
        sid = str(item.get("id") or "").strip()
        where = sid or f"system_tests[{index}]"
        match = _ID_RE.match(sid)
        if not match:
            findings.append(("error", "A10", f"{label}: {where} has id '{sid}' — a scenario "
                                             f"id reads SYS-TC-<n>, minted from "
                                             f"id_counters.sys_tc"))
        elif sid in seen:
            findings.append(("error", "A14", f"{label}: names {sid} twice — one id is one "
                                             f"scenario; a second scenario needs its own id"))
        elif int(match.group(1)) > high_water:
            findings.append(("error", "A13", f"{label}: {sid} is above id_counters.sys_tc "
                                             f"({high_water}) — that id was never minted, "
                                             f"so the next cut mints it again; bump the "
                                             f"counter or renumber the scenario"))
        empty = [f for f in _FIELDS if _blank(item.get(f))]
        if empty:
            findings.append(("error", "A10", f"{label}: {where} leaves {', '.join(empty)} "
                                             f"empty — a scenario the machinery cannot read "
                                             f"is a scenario that does not ship"))
        if match:
            seen.add(sid)
            records.append({"id": sid, "label": label, "words": scenario_words(item)})
    return records, findings


def duplication_findings(records):
    """The A11 findings: one id under more than one ENTRY is one test satisfying both
    sets, so every copy must read the same. Differing text is two scenarios wearing one
    id — whichever copy the build reads decides what ships, silently."""
    groups = {}
    for rec in records:
        groups.setdefault(rec["id"], []).append(rec)
    out = []
    for sid in sorted(groups, key=_id_key):
        group = groups[sid]
        if len({rec["label"] for rec in group}) < 2:
            continue
        variants = {}
        for rec in group:
            variants.setdefault(rec["words"], [])
            if rec["label"] not in variants[rec["words"]]:
                variants[rec["words"]].append(rec["label"])
        if len(variants) < 2:
            continue
        where = " vs ".join(", ".join(labels) for labels in variants.values())
        out.append(("error", "A11", f"{sid}: its copies under {where} carry different text "
                                    f"— a scenario duplicated across entries is ONE test, "
                                    f"so keep title/given/when/then identical, or give the "
                                    f"second scenario its own id"))
    return out


def test_roots(config):
    """The directories ``paths.tests`` names that exist — the roots the tag scanner
    sweeps. A polyglot repo splits its test tree, so this is a list."""
    cfg = (common.config_doc(config).get("paths") or {}).get("tests")
    roots = [cfg] if isinstance(cfg, str) else list(cfg or [])
    base = common.project_root(config)
    return [d for d in ((base / r) for r in roots) if d.is_dir()]


def shipped_findings(records, harvested):
    """The A15 findings: an id already tagged in the test tree whose work-set wording no
    longer matches the tag's own description. A warning — an unshipped scenario is
    amendable freely, and re-wording a shipped one is the human's escalation."""
    groups = {}
    for rec in records:
        if rec["id"] in harvested:
            groups.setdefault((rec["id"], rec["words"]), []).append(rec["label"])
    out = []
    for sid, key in sorted(groups, key=lambda k: _id_key(k[0])):
        shipped = [s for s in harvested[sid]["statements"] if str(s).strip()]
        if not shipped or any(words(s) == key for s in shipped):
            continue
        labels = ", ".join(dict.fromkeys(groups[(sid, key)]))
        out.append(("warn", "A15", f"{sid}: the text under {labels} differs from the "
                                   f"description its shipped test carries "
                                   f"('{' '.join(str(shipped[0]).split())[:120]}') — the "
                                   f"work-set and the test tree disagree about what "
                                   f"shipped"))
    return out


def _id_key(sid):
    """Numeric ordering over scenario ids, so SYS-TC-2 sorts before SYS-TC-10."""
    m = _ID_RE.match(str(sid))
    return (int(m.group(1)) if m else float("inf"), str(sid))


def _scan(config, high_water):
    """(files, entries, records, findings) over the whole work-set. `entries` is
    {entry label: scenarios it carries} — most entries carry none, so the count is one
    line each rather than a block."""
    files, entries, records, findings = [], {}, [], []
    for key, list_key in _FILES:
        info = read_file(config, key, list_key)
        if info["error"]:
            findings.append(("error", "A16", info["error"]))
        count = 0
        for index, entry in enumerate(info["items"], 1):
            label = _label(entry, key, index)
            if not isinstance(entry, dict):
                findings.append(("error", "A16", f"paths.{key}: entry {index} is not a "
                                                 f"mapping ({entry!r}) — every entry carries "
                                                 f"an id and its statement"))
                continue
            recs, found = entry_findings(label, entry, high_water)
            records += recs
            findings += found
            count += len(recs)
            entries[label] = len(recs)
        files.append({"key": key, "path": info["path"], "present": info["present"],
                      "entries": len(info["items"]), "scenarios": count})
    return files, entries, records, findings


def _check(rest):
    p = common.base_parser("workset check")
    args = p.parse_args(rest)

    high_water = counter(args.config, "sys_tc")
    files, entries, records, findings = _scan(args.config, high_water)
    findings += duplication_findings(records)
    roots = test_roots(args.config)
    if records and roots:
        findings += shipped_findings(records, harvest(roots, DEFAULT_TEST_GLOBS))

    errors = [f for f in findings if f[0] == "error"]
    warns = [f for f in findings if f[0] == "warn"]
    result = {
        "files": files,
        "sys_tc_counter": high_water,
        "scenarios": len(records),
        "entries": entries,
        "errors": [{"code": c, "msg": m} for _, c, m in errors],
        "warnings": [{"code": c, "msg": m} for _, c, m in warns],
        "verdict": "fail" if errors else "pass",
    }
    common.emit(result, args.format)
    return 1 if result["verdict"] == "fail" else 0


COMMANDS = {
    ("workset", "check"): _check,
}
