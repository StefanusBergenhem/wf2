#!/usr/bin/env python3
"""claude_hook_register.py — register the telemetry usage hook in a Claude Code
settings.json (run by install.sh, claude target only).

Create-or-merge, idempotent: ensures `hooks.Stop` and `hooks.SubagentStop` each
carry one command entry for the given command, preserving every other setting
and any foreign hooks. A malformed settings.json is left untouched (non-zero
exit) — never clobber user settings. Stdlib only.

Usage: claude_hook_register.py <settings.json> <command>
"""
import json
import os
import sys

EVENTS = ("Stop", "SubagentStop")


def _has_command(entries, command):
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        hooks = entry.get("hooks")
        if not isinstance(hooks, list):
            continue
        if any(isinstance(h, dict) and h.get("command") == command for h in hooks):
            return True
    return False


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print("usage: claude_hook_register.py <settings.json> <command>", file=sys.stderr)
        return 2
    path, command = argv

    data, existed = {}, os.path.isfile(path)
    if existed:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except ValueError as e:
            print(f"claude_hook_register: {path} is not valid JSON ({e}) — left untouched",
                  file=sys.stderr)
            return 1
    if not isinstance(data, dict):
        print(f"claude_hook_register: {path} is not a JSON object — left untouched",
              file=sys.stderr)
        return 1

    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        print(f"claude_hook_register: 'hooks' in {path} is not an object — left untouched",
              file=sys.stderr)
        return 1

    changed = False
    for event in EVENTS:
        entries = hooks.setdefault(event, [])
        if not isinstance(entries, list):
            print(f"claude_hook_register: 'hooks.{event}' in {path} is not a list — left untouched",
                  file=sys.stderr)
            return 1
        if not _has_command(entries, command):
            entries.append({"hooks": [{"type": "command", "command": command}]})
            changed = True

    if changed or not existed:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
