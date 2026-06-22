"""Shared helpers for the wf CLI.

Deliberately generic — no artifact-specific knowledge lives here. Every
subcommand module imports this and builds on it, so path resolution, YAML
loading, output formatting, and error reporting behave identically across the
whole CLI. This is the one config reader wf2 has (the C1 "shared config reader"
threshold): skills resolve paths from .wf/config.yaml as prose, scripts resolve
them through here.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - environment guard
    sys.stderr.write("wf: PyYAML is required (pip install pyyaml)\n")
    sys.exit(2)


def die(msg: str, code: int = 2) -> "NoReturn":  # type: ignore[name-defined]
    """Mechanical failure: message to stderr, exit non-zero."""
    sys.stderr.write(f"wf: {msg}\n")
    sys.exit(code)


def load_yaml(path: Path, optional: bool = False) -> dict:
    """Parse a YAML file to a dict. A missing file is fatal unless ``optional``,
    in which case it reads as an empty doc — so callers serving an inherently
    optional artifact (e.g. live state before the first mutation) need no
    existence guard of their own."""
    if not path.exists():
        if optional:
            return {}
        die(f"file not found: {path}")
    try:
        return yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        die(f"could not parse {path}: {exc}")


def config_doc(config_path: str) -> dict:
    return load_yaml(Path(config_path))


def project_root(config_path: str) -> Path:
    """config.yaml lives at <root>/.wf/config.yaml, so the project root is two
    levels up. Artifact paths in config are relative to that root."""
    return Path(config_path).resolve().parent.parent


def resolve_path(config_path: str, key: str, override: str | None = None) -> Path:
    """Resolve an artifact location: an explicit --<key> override wins;
    otherwise read paths.<key> from config and anchor it on the project root."""
    if override:
        return Path(override)
    rel = (config_doc(config_path).get("paths") or {}).get(key)
    if not rel:
        die(f"no override given and paths.{key} not set in {config_path}")
    return (project_root(config_path) / rel).resolve()


def host_root() -> Path:
    """The host checkout root — the parent of ``git rev-parse --git-common-dir``.
    A git worktree shares the main checkout's ``.git`` (and therefore its ``.wf/``
    config and live pipeline state), so anchoring here means the CLI resolves the
    SAME artifacts whether it is invoked from the main checkout or from a per-task
    worktree. Falls back to the current directory when not inside a git repo (e.g.
    a test harness's throwaway tmp project)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return Path.cwd()
    if not out:
        return Path.cwd()
    common_dir = Path(out)
    if not common_dir.is_absolute():
        common_dir = Path.cwd() / common_dir
    return common_dir.parent


def default_config() -> str:
    """Host-anchored default location of ``.wf/config.yaml`` (see ``host_root``).
    An explicit ``--config`` always overrides it — both the test harness and any
    worktree-local caller pass one — so this only governs the bare-invocation
    case, which must resolve the main checkout's state."""
    return str(host_root() / ".wf" / "config.yaml")


def base_parser(prog: str) -> argparse.ArgumentParser:
    """A parser pre-loaded with the flags every subcommand shares. Modules add
    their own arguments on top, then call parse_args(rest)."""
    p = argparse.ArgumentParser(prog=f"wf {prog}")
    p.add_argument("--format", choices=["yaml", "json"], default="yaml")
    p.add_argument(
        "--config",
        default=default_config(),
        help="path to .wf/config.yaml for paths.* resolution "
        "(default: host-anchored to the main checkout, even from a worktree)",
    )
    return p


def emit(obj, fmt: str = "yaml") -> None:
    """Write a query result to stdout — YAML by default (human/agent-readable,
    diffable), compact JSON when a caller wants structured routing."""
    if fmt == "json":
        print(json.dumps(obj, separators=(",", ":"), default=str))
    else:
        print(
            yaml.safe_dump(
                obj, sort_keys=False, default_flow_style=False, allow_unicode=True
            ).rstrip()
        )
