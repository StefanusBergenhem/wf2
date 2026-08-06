#!/usr/bin/env python3
"""Tests for role dispatch — prompt construction, the config-keyed launch template,
the process contract (exit code, never stdout), and dry-run.
Run: python3 tools/driver/tests/dispatch_test.py
wf2-source-only — never rendered into an install target.
"""
from __future__ import annotations

import json
import unittest

import support  # noqa: F401

import config as driver_config
import dispatch as driver_dispatch
import events as driver_events
import runtime as driver_runtime


class PromptTest(support.TempProject):
    def setUp(self):
        super().setUp()
        (self.root / ".claude/skills/wf-designer").mkdir(parents=True)
        (self.root / ".claude/skills/wf-designer/SKILL.md").write_text("skill\n")

    def cfg(self, **kw):
        return driver_config.load(str(support.write_config(self.root, **kw)))

    def test_prompt_names_the_role_file_and_its_parameters(self):
        cfg = self.cfg()
        prompt = driver_dispatch.build_prompt(cfg, "wf-designer", {"Mode": "originate"})
        self.assertIn(str(self.root / ".claude/skills/wf-designer/SKILL.md"), prompt)
        self.assertIn("and follow it", prompt)
        self.assertIn("Mode: originate", prompt)

    def test_prompt_for_an_uninstalled_role_is_fatal(self):
        cfg = self.cfg()
        with self.assertRaises(driver_dispatch.DispatchError):
            driver_dispatch.build_prompt(cfg, "wf-nowhere", {})

    def test_double_quoted_template_escapes_the_prompt_for_that_quoting(self):
        cmd = driver_dispatch.render_cmd('claude -p "{prompt}"', 'say "hi" $HOME `x`')
        self.assertEqual(cmd, 'claude -p "say \\"hi\\" \\$HOME \\`x\\`"')

    def test_single_quoted_template_escapes_for_single_quotes(self):
        cmd = driver_dispatch.render_cmd("opencode run '{prompt}'", "it's here")
        self.assertEqual(cmd, "opencode run 'it'\\''s here'")

    def test_unquoted_template_gets_a_shell_quoted_prompt(self):
        cmd = driver_dispatch.render_cmd("agent {prompt}", "two words")
        self.assertEqual(cmd, "agent 'two words'")

    def test_template_without_the_placeholder_is_fatal(self):
        with self.assertRaises(driver_dispatch.DispatchError):
            driver_dispatch.render_cmd("claude -p", "hello")


class LaunchTest(support.TempProject):
    def setUp(self):
        super().setUp()
        (self.root / ".claude/agents").mkdir(parents=True)
        (self.root / ".claude/agents/wf-build.md").write_text("agent\n")
        self.marker = self.root / "agent-ran.json"

    def dispatcher(self, agent_cmd, dry_run=False):
        cfg = driver_config.load(str(support.write_config(self.root, agent_cmd=agent_cmd)))
        self.cfg_obj = cfg
        return driver_dispatch.Dispatcher(
            cfg, driver_events.Telemetry(cfg, dry_run=dry_run), dry_run=dry_run)

    def test_launch_runs_the_template_and_returns_exit_code_and_duration(self):
        script = self.root / "fake-agent.sh"
        script.write_text('#!/usr/bin/env bash\nprintf "%s" "$1" > "$2"\nexit 0\n')
        script.chmod(0o755)
        d = self.dispatcher(f'{script} "{{prompt}}" {self.marker}')
        result = d.launch("wf-build", {"task_id": "T1"}, task_id="T1")
        self.assertEqual(result.exit_code, 0)
        self.assertGreaterEqual(result.duration_s, 0)
        self.assertIn("task_id: T1", self.marker.read_text())

    def test_a_failing_agent_surfaces_its_exit_code(self):
        d = self.dispatcher('bash -c "exit 7" "{prompt}"')
        self.assertEqual(d.launch("wf-build", {}).exit_code, 7)

    def test_agent_stdout_goes_to_a_log_file_and_is_not_returned(self):
        script = self.root / "chatty-agent.sh"
        script.write_text("#!/usr/bin/env bash\necho VERDICT-IN-PROSE\n")
        script.chmod(0o755)
        d = self.dispatcher(f'{script} "{{prompt}}"')
        result = d.launch("wf-build", {}, task_id="T2")
        self.assertFalse(hasattr(result, "stdout"))
        self.assertIn("VERDICT-IN-PROSE", result.log_path.read_text())

    def test_timeout_is_bounded_by_the_configured_agent_timeout(self):
        script = self.root / "slow-agent.sh"
        script.write_text("#!/usr/bin/env bash\nsleep 5\n")
        script.chmod(0o755)
        cfg_path = support.write_config(self.root, agent_cmd=f'{script} "{{prompt}}"')
        cfg_path.write_text(cfg_path.read_text().replace("agent_timeout_s: 60",
                                                         "agent_timeout_s: 1"))
        cfg = driver_config.load(str(cfg_path))
        d = driver_dispatch.Dispatcher(cfg, driver_events.Telemetry(cfg))
        result = d.launch("wf-build", {})
        self.assertTrue(result.timed_out)
        self.assertNotEqual(result.exit_code, 0)

    def test_dry_run_records_the_planned_dispatch_without_launching(self):
        d = self.dispatcher(f'bash -c "touch {self.marker}" "{{prompt}}"', dry_run=True)
        result = d.launch("wf-build", {"task_id": "T1"}, task_id="T1")
        self.assertEqual(result.exit_code, 0)
        self.assertFalse(self.marker.exists())
        self.assertEqual([p["role"] for p in d.planned], ["wf-build"])

    def test_dispatch_appends_a_telemetry_row(self):
        cfg = driver_config.load(str(support.write_config(
            self.root, agent_cmd='bash -c "true" "{prompt}"')))
        tele = driver_events.Telemetry(cfg)
        d = driver_dispatch.Dispatcher(cfg, tele)
        d.launch("wf-build", {}, task_id="T9", increment=2, mode="fix")
        rows = [json.loads(x) for x in cfg.path("telemetry").read_text().splitlines()]
        self.assertEqual(rows[-1]["event"], "dispatch")
        self.assertEqual(rows[-1]["role"], "wf-build")
        self.assertEqual(rows[-1]["agent"], "wf-build")
        self.assertEqual(rows[-1]["task"], "T9")
        self.assertEqual(rows[-1]["increment"], 2)
        self.assertEqual(rows[-1]["mode"], "fix")
        self.assertEqual(rows[-1]["rc"], 0)
        self.assertTrue(rows[-1]["started_at"].endswith("Z"))
        self.assertTrue(rows[-1]["ended_at"].endswith("Z"))

    def test_a_pinned_role_launches_through_its_own_template(self):
        script = self.root / "pinned-agent.sh"
        script.write_text('#!/usr/bin/env bash\nprintf "pinned" > "$2"\n')
        script.chmod(0o755)
        (self.root / ".claude/agents/wf-designer.md").write_text("agent\n")
        cfg = driver_config.load(str(support.write_config(
            self.root, agent_cmd=f'bash -c "true" "{{prompt}}"',
            agent_cmd_overrides={"wf-designer": f'{script} "{{prompt}}" {self.marker}'})))
        d = driver_dispatch.Dispatcher(cfg, driver_events.Telemetry(cfg))
        d.launch("wf-designer", {})
        self.assertEqual(self.marker.read_text(), "pinned")
        self.assertEqual(cfg.agent_cmd_for("wf-build"), cfg.agent_cmd)

    def test_a_role_with_no_override_falls_back_to_the_shared_template(self):
        cfg = driver_config.load(str(support.write_config(
            self.root, agent_cmd=f'bash -c "touch {self.marker}" "{{prompt}}"',
            agent_cmd_overrides={"wf-designer": "never-run {prompt}"})))
        d = driver_dispatch.Dispatcher(cfg, driver_events.Telemetry(cfg))
        self.assertEqual(d.launch("wf-build", {}).exit_code, 0)
        self.assertTrue(self.marker.exists())


class CheckLaunchTest(support.TempProject):
    """The blame helper: called where a role left nothing to route on, it decides whether
    the caller may draw a conclusion about the WORK at all."""

    def launched(self, rc, body=None):
        log = self.root / "role.log"
        if body is not None:
            log.write_text(body)
        return driver_dispatch.Launched("wf-tl", rc, 0, False, log, "cmd", {})

    def test_a_clean_exit_lets_the_caller_draw_its_own_conclusion(self):
        driver_dispatch.check_launch(self.launched(0, "fine"))       # no raise
        driver_dispatch.check_launch(None)                           # nothing dispatched

    def test_a_failed_launch_pauses_and_quotes_the_harness(self):
        with self.assertRaises(driver_runtime.Pause) as caught:
            driver_dispatch.check_launch(
                self.launched(1, "starting\n\nYou've hit your session limit\n"))
        self.assertEqual(caught.exception.reason, "launch_failed")
        self.assertIn("session limit", caught.exception.detail)
        self.assertIn("wf-tl exited 1", caught.exception.detail)

    def test_a_failed_launch_with_no_log_still_pauses(self):
        with self.assertRaises(driver_runtime.Pause):
            driver_dispatch.check_launch(self.launched(1))           # log never created

    def test_a_timeout_says_so_and_names_the_budget_to_raise(self):
        timed_out = driver_dispatch.Launched("wf-designer", 124, 7200, True,
                                             self.root / "role.log", "cmd", {})
        with self.assertRaises(driver_runtime.Pause) as caught:
            driver_dispatch.check_launch(timed_out)
        self.assertEqual(caught.exception.reason, "launch_timeout")
        self.assertIn("agent_timeout_s", caught.exception.detail)
        self.assertIn("2h00m", caught.exception.detail)
        self.assertNotIn("never ran", caught.exception.detail)

    def test_the_quoted_line_is_bounded(self):
        long_line = "x" * 5000
        self.assertEqual(len(driver_dispatch.last_line(
            self.write_log(long_line))), 200)

    def test_last_line_of_a_missing_log_is_empty(self):
        self.assertEqual(driver_dispatch.last_line(self.root / "nope.log"), "")

    def write_log(self, body):
        path = self.root / "role.log"
        path.write_text(body)
        return path


if __name__ == "__main__":
    unittest.main()
