"""Live agent dispatch via the Claude Agent SDK.

Thin on purpose: it parses a rendered agent definition file
(``.claude/agents/<name>.md`` — YAML frontmatter + Markdown body) into the SDK's
``AgentDefinition``, then runs the agent to completion with its working directory
set to the task worktree. It returns nothing; the loop reads the verdict from
on-disk artifacts (see :mod:`orchestrator.dispatch`).

The SDK import is **lazy** — performed inside :meth:`dispatch`, never at module
import — so a plain ``wf pipeline next`` query (and the orchestrator's own
fake-backed tests) never require the optional ``claude-agent-sdk`` package.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from .dispatch import Dispatcher


class SdkUnavailableError(ImportError):
    """Raised when a live dispatch is attempted but the Claude Agent SDK is not
    installed. Carries the install hint for the orchestrator's optional deps."""


def _parse_agent_definition(agent_md: Path) -> tuple[dict, str]:
    """Split a rendered agent file into (frontmatter dict, body str). The format
    is the standard Claude agent definition: a YAML block fenced by ``---`` lines
    at the top, followed by the Markdown system prompt."""
    import yaml  # PyYAML is a hard dep of the CLI; safe to import here.

    text = agent_md.read_text()
    frontmatter: dict = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            frontmatter = yaml.safe_load(parts[1]) or {}
            body = parts[2].lstrip("\n")
    return frontmatter, body


class ClaudeSdkDispatcher(Dispatcher):
    """Drives a live agent through the Claude Agent SDK.

    :param agents_dir: directory holding rendered ``<agent_name>.md`` files
        (e.g. ``.claude/agents``). The agent's system prompt and tool/model
        metadata are parsed from there so the SDK run matches the installed agent
        definition exactly.
    """

    def __init__(self, agents_dir: str | Path = ".claude/agents"):
        self.agents_dir = Path(agents_dir)

    def _load_sdk(self):
        try:
            import claude_agent_sdk  # noqa: F401  (lazy, optional dependency)
        except ImportError as exc:  # pragma: no cover - exercised only without the SDK
            raise SdkUnavailableError(
                "the Claude Agent SDK is not installed; live agent dispatch is "
                "unavailable. Install it with:\n"
                "  pip install -r .wf/tools/cli/orchestrator/requirements.txt"
            ) from exc
        return claude_agent_sdk

    def _build_definition(self, sdk, agent_name: str):
        agent_md = self.agents_dir / f"{agent_name}.md"
        if not agent_md.exists():
            raise FileNotFoundError(
                f"agent definition not found: {agent_md} "
                f"(expected a rendered <agent>.md under {self.agents_dir})"
            )
        frontmatter, body = _parse_agent_definition(agent_md)
        # AgentDefinition's exact field set varies across SDK versions; pass only
        # the well-known ones, falling back gracefully.
        kwargs = {
            "description": frontmatter.get("description", agent_name),
            "prompt": body,
        }
        tools = frontmatter.get("tools")
        if tools:
            kwargs["tools"] = (
                [t.strip() for t in tools.split(",")] if isinstance(tools, str) else tools
            )
        model = frontmatter.get("model")
        if model:
            kwargs["model"] = model
        return sdk.AgentDefinition(**kwargs)

    async def _run(self, sdk, definition, agent_name: str, envelope: str, worktree: str | None) -> None:
        options = sdk.ClaudeAgentOptions(
            agents={agent_name: definition},
            cwd=worktree,  # None → SDK uses the current working directory (repo root)
        )
        # Run the agent to completion, draining its message stream. We discard the
        # messages: the verdict is read from on-disk artifacts afterwards.
        async for _ in sdk.query(prompt=envelope, options=options):
            pass

    def dispatch(self, agent_name: str, envelope: str, worktree: str | None) -> None:
        sdk = self._load_sdk()
        definition = self._build_definition(sdk, agent_name)
        asyncio.run(self._run(sdk, definition, agent_name, envelope, worktree))
