#!/usr/bin/env bash
#
# Envelope parity — every dispatched role declares the config keys it reads, and reads
# every key it declares. The dispatch prompt renders the declared set and nothing else,
# so an undeclared key is one the role cannot resolve, and an unread declaration is
# context every dispatch of that role pays for.
# Run: bash tools/cli/tests/envelope_parity_test.sh          (exit 0 = all pass)
#      bash tools/cli/tests/envelope_parity_test.sh --fix    (rewrite the declarations)
# wf2-source-only — never rendered into an install target.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
PYTHON="$(command -v python3)"
[ -x "$ROOT/tools/.venv/bin/python" ] && PYTHON="$ROOT/tools/.venv/bin/python"

"$PYTHON" - "$ROOT" "$@" <<'PY'
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
template = root / "skills/wf-init/assets/config.yaml.tmpl"
# The blocks the dispatch prompt can carry, and the single keys outside them —
# envelope.py's own _BLOCKS and _KEYS. Both are counted, or a role naming a key from
# outside the blocks declares nothing and the prompt renders nothing.
BLOCKS = ("paths", "commands", "limits", "hygiene")
KEYS = ("project.name",)
KEY_RE = re.compile(r"\b(?:(?:" + "|".join(BLOCKS) + r")\.[a-z_]+|"
                    + "|".join(re.escape(k) for k in KEYS) + r")\b")

pass_n = fail_n = 0


def ok(msg):
    global pass_n
    pass_n += 1
    print(f"  ok   - {msg}")


def bad(msg, detail):
    global fail_n
    fail_n += 1
    print(f"  FAIL - {msg}")
    print(f"         {detail}")


def frontmatter(path):
    """The role file's YAML frontmatter as raw text, or '' when it has none."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[3:end] if end > 0 else ""


def declared(path):
    """The keys the role's `envelope:` frontmatter list names, in file order."""
    block = frontmatter(path)
    match = re.search(r"^envelope:\s*$((?:\n\s+-\s+\S+)*)", block, re.M)
    if not match:
        return None
    return [line.strip().lstrip("- ").strip()
            for line in match.group(1).splitlines() if line.strip()]


def template_keys():
    keys, block = set(), None
    for line in template.read_text(encoding="utf-8").splitlines():
        if re.match(r"^[a-z_]+:", line):
            block = line.split(":", 1)[0]
            continue
        found = re.match(r"^  ([a-z_]+):", line)
        if not found:
            continue
        name = f"{block}.{found.group(1)}"
        if block in BLOCKS or name in KEYS:
            keys.add(name)
    return keys


def body(path):
    """A role file below its frontmatter. The declaration lists the very keys being
    counted, so scanning it would make every declared key look read and no unused one
    could ever be found."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    return text[end + 4:] if end > 0 else text


def role_text(role_path):
    """Everything the role reads: its own body, its skill dir, and each shared skill it
    names — the whole text the executing agent ends up holding."""
    files = [role_path]
    text = body(role_path)
    skill_dir = root / "skills" / role_path.stem
    if skill_dir.is_dir():
        files += sorted(skill_dir.rglob("*.md"))
        text += "\n".join(body(f) for f in files[1:])
    for name in sorted(set(re.findall(r"\b(wf-[a-z-]+)/SKILL\.md", text))):
        shared = root / "skills" / name / "SKILL.md"
        if shared.is_file() and shared not in files:
            files.append(shared)
            text += body(shared)
    return text


roles = sorted(p for p in (root / "agents").glob("*.md"))
roles += [p for p in [root / "skills/wf-designer/SKILL.md"] if p.is_file()]
tkeys = template_keys()
if not tkeys:
    bad("config template", f"no {'/'.join(BLOCKS)} keys parsed from {template}")

def rewrite(role, keys):
    """Replace the role's `envelope:` list with `keys` — the declaration is derived from
    the role's own text, so it is written rather than hand-kept in step with it."""
    text = role.read_text(encoding="utf-8")
    block = "envelope:\n" + "".join(f"  - {k}\n" for k in keys)
    if re.search(r"^envelope:\s*$(?:\n\s+-\s+\S+)*\n", text, re.M):
        text = re.sub(r"^envelope:\s*$(?:\n\s+-\s+\S+)*\n", block, text, count=1,
                      flags=re.M)
    else:
        end = text.find("\n---", 3)
        text = text[:end] + "\n" + block.rstrip("\n") + text[end:]
    role.write_text(text, encoding="utf-8")


fix = "--fix" in sys.argv[2:]
for role in roles:
    name = role.stem if role.name != "SKILL.md" else role.parent.name
    want = declared(role)
    used = set(KEY_RE.findall(role_text(role)))
    unknown = sorted(used - tkeys)
    if unknown:
        bad(f"{name}: names only keys the config carries", f"not in template: {unknown}")
        continue
    if fix and sorted(used) != (want or []):
        rewrite(role, sorted(used))
        ok(f"{name}: envelope rewritten ({len(used)} keys)")
        continue
    if want is None:
        bad(f"{name}: declares an envelope", "no `envelope:` list in its frontmatter")
        continue
    # The declaration is what the prompt renders, so a key the role's text names but does
    # not declare arrives nowhere — the role reads a line that is not there.
    missing = sorted(used - set(want))
    extra = sorted(set(want) - used)
    if missing or extra:
        detail = []
        if missing:
            detail.append(f"read but undeclared: {missing}")
        if extra:
            detail.append(f"declared but unread: {extra}")
        bad(f"{name}: envelope parity",
            "; ".join(detail) + " — re-run with --fix to write the declaration")
    else:
        ok(f"{name}: envelope parity ({len(want)} keys)")

print("")
print(f"  envelope parity: {pass_n} passed, {fail_n} failed")
sys.exit(1 if fail_n else 0)
PY
