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
        prompt = driver_dispatch.build_prompt(cfg, "wf-designer", {"Mode": "resume"})
        self.assertIn(str(self.root / ".claude/skills/wf-designer/SKILL.md"), prompt)
        self.assertIn("and follow it", prompt)
        self.assertIn("Mode: resume", prompt)

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
        d.launch("wf-build", {}, task_id="T9", stage=7, mode="fix")
        rows = [json.loads(x) for x in cfg.path("telemetry").read_text().splitlines()]
        self.assertEqual(rows[-1]["event"], "dispatch")
        self.assertEqual(rows[-1]["role"], "wf-build")
        self.assertEqual(rows[-1]["agent"], "wf-build")
        self.assertEqual(rows[-1]["task"], "T9")
        self.assertEqual(rows[-1]["stage"], 7)
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
        return driver_dispatch.Launched("wf-designer", rc, 0, False, log, "cmd", {})

    def test_a_clean_exit_lets_the_caller_draw_its_own_conclusion(self):
        driver_dispatch.check_launch(self.launched(0, "fine"))       # no raise
        driver_dispatch.check_launch(None)                           # nothing dispatched

    def test_a_failed_launch_pauses_and_quotes_the_harness(self):
        with self.assertRaises(driver_runtime.Pause) as caught:
            driver_dispatch.check_launch(
                self.launched(1, "starting\n\nYou've hit your session limit\n"))
        self.assertEqual(caught.exception.reason, "launch_failed")
        self.assertIn("session limit", caught.exception.detail)
        self.assertIn("wf-designer exited 1", caught.exception.detail)

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


# The two shapes a rate-limited harness leaves in the log: the event it emits the
# moment it refuses, and the result line it exits on. Both are real, copied from the
# dems run that stopped the sprint.
def limit_event(resets_at) -> str:
    return ('{"type":"rate_limit_event","rate_limit_info":{"status":"rejected",'
            f'"resetsAt":{resets_at},"rateLimitType":"five_hour"}}}}')


def heartbeat(resets_at) -> str:
    """The rate_limit_event a harness that is still serving emits — same shape and
    same fields as a refusal, `allowed`, and present in nearly every dispatch log.
    `overageStatus` carries the word `rejected` in it on purpose: that is the real
    payload, and it is what a status-blind match trips over."""
    return ('{"type":"rate_limit_event","rate_limit_info":{"status":"allowed",'
            f'"resetsAt":{resets_at},"rateLimitType":"five_hour",'
            '"overageStatus":"rejected","isUsingOverage":false}}')


RESULT_LINE = ('{"is_error":true,"terminal_reason":"api_error","subtype":"success",'
               '"api_error_status":429,"result":"You\'ve hit your session limit '
               '· resets 8:40pm","type":"result"}')


class RateLimitReadTest(support.TempProject):
    """Reading a refused launch's log: was it a rate limit, and when does it lift?"""

    NOW = 1_786_000_000

    def log(self, body):
        path = self.root / "role.log"
        path.write_text(body)
        return path

    def wait(self, body, *, now=None, cap_s=18000):
        return driver_dispatch.rate_limit_wait_s(
            self.log(body), now=self.NOW if now is None else now, cap_s=cap_s)

    def test_the_wait_runs_to_the_reset_the_harness_named_plus_the_margin(self):
        self.assertEqual(self.wait(limit_event(self.NOW + 600) + "\n" + RESULT_LINE),
                         600 + driver_dispatch.RATE_LIMIT_MARGIN_S)

    def test_the_margin_is_two_minutes_past_the_reset(self):
        # The reset is the moment the window rolls; relaunching on it races the roll.
        self.assertEqual(driver_dispatch.RATE_LIMIT_MARGIN_S, 120)

    def test_a_launch_that_failed_for_any_other_reason_is_not_waited_out(self):
        self.assertIsNone(self.wait('{"type":"result","is_error":true,'
                                    '"result":"command not found"}'))

    def test_an_empty_or_missing_log_is_not_a_rate_limit(self):
        self.assertIsNone(self.wait(""))
        self.assertIsNone(driver_dispatch.rate_limit_wait_s(
            self.root / "nope.log", now=self.NOW, cap_s=18000))

    def test_the_last_reset_in_the_log_is_the_one_that_counts(self):
        # A long dispatch can be told about the limit more than once; the final word
        # is the only one still true when it exits.
        body = "\n".join([limit_event(self.NOW + 60), RESULT_LINE,
                          limit_event(self.NOW + 900)])
        self.assertEqual(self.wait(body), 900 + driver_dispatch.RATE_LIMIT_MARGIN_S)

    def test_a_reset_already_past_waits_only_the_margin(self):
        self.assertEqual(self.wait(limit_event(self.NOW - 5000) + "\n" + RESULT_LINE),
                         driver_dispatch.RATE_LIMIT_MARGIN_S)

    def test_a_rate_limit_naming_no_reset_falls_back_to_the_configured_cap(self):
        self.assertEqual(self.wait(RESULT_LINE, cap_s=18000), 18000)

    def test_a_reset_beyond_the_cap_is_not_waited_out_at_all(self):
        # A weekly limit resets days out. Sleeping the cap would burn hours and still
        # relaunch into a refusal; the human is told instead.
        self.assertIsNone(self.wait(limit_event(self.NOW + 400000) + "\n" + RESULT_LINE,
                                    cap_s=18000))

    def test_the_routine_allowed_heartbeat_is_not_a_refusal(self):
        # The heartbeat is in nearly every log, so matching the event's shape rather
        # than its status reads EVERY failed dispatch as rate-limited — and a role that
        # died for its own reasons then sleeps to the next window rollover twice over
        # instead of surfacing.
        self.assertIsNone(self.wait("\n".join([
            heartbeat(self.NOW + 9000),
            '{"type":"result","is_error":true,"terminal_reason":"aborted_streaming"}',
        ])))

    def test_a_refusal_among_heartbeats_is_read_from_the_refusal(self):
        # The heartbeats name the window the harness was still serving from; only the
        # refusal names the one being waited out.
        body = "\n".join([heartbeat(self.NOW + 9000), heartbeat(self.NOW + 9000),
                          limit_event(self.NOW + 600), RESULT_LINE])
        self.assertEqual(self.wait(body), 600 + driver_dispatch.RATE_LIMIT_MARGIN_S)

    def test_whitespace_in_the_harness_json_does_not_hide_the_limit(self):
        self.assertEqual(
            self.wait('{"rate_limit_info": {"status": "rejected", '
                      f'"resetsAt": {self.NOW + 300}}}, '
                      '"api_error_status" : 429}'),
            300 + driver_dispatch.RATE_LIMIT_MARGIN_S)


def result_line(**over):
    """A harness result line, shaped as the real one is — the `type` key is NOT first,
    so anything hunting for a `{"type":"result"` prefix misses it."""
    row = {"ttft_ms": 1910, "num_turns": 34, "type": "result", "subtype": "success",
           "is_error": False, "duration_ms": 185269, "total_cost_usd": 0.9588514,
           "usage": {"input_tokens": 4199, "output_tokens": 9158,
                     "cache_read_input_tokens": 1602358,
                     "cache_creation_input_tokens": 54581}}
    row.update(over)
    return json.dumps(row)


class ResultUsageTest(support.TempProject):
    """The harness writes what a dispatch cost on the last line of its own log, and the
    log is transient — so the driver reads it at close or the number is gone."""

    def usage(self, body):
        path = self.root / "role.log"
        path.write_text(body)
        return driver_dispatch.result_usage(path)

    def test_cost_turns_and_the_token_breakdown_come_off_the_result_line(self):
        self.assertEqual(self.usage(result_line()), {
            "cost_usd": 0.9588514, "num_turns": 34,
            "input": 4199, "output": 9158,
            "cache_read": 1602358, "cache_creation": 54581})

    def test_the_result_line_is_found_under_the_streams_own_output(self):
        body = "\n".join(['{"type":"assistant","message":{"content":"working"}}',
                          '{"type":"user","message":{"content":"tool result"}}',
                          result_line()])
        self.assertEqual(self.usage(body)["cost_usd"], 0.9588514)

    def test_a_dispatch_the_harness_refused_has_no_result_line_and_costs_nothing_known(self):
        # Telemetry is observability, never correctness — an unreadable log reports
        # nothing rather than guessing a zero that would read as "this run was free".
        self.assertEqual(self.usage("You've hit your session limit\n"), {})
        self.assertEqual(self.usage(""), {})
        self.assertEqual(driver_dispatch.result_usage(self.root / "nope.log"), {})

    def test_a_truncated_or_non_json_last_line_is_not_a_crash(self):
        self.assertEqual(self.usage('{"type":"result","total_cost_usd":'), {})

    def test_fields_the_harness_omitted_are_left_out_rather_than_zeroed(self):
        got = self.usage(json.dumps({"type": "result", "total_cost_usd": 0.5}))
        self.assertEqual(got, {"cost_usd": 0.5})


class DispatchRowTest(support.TempProject):
    """What the dispatch telemetry row carries — the row is written either way, so the
    cost rides it rather than being stored anywhere new."""

    def setUp(self):
        super().setUp()
        (self.root / ".claude/agents").mkdir(parents=True)
        (self.root / ".claude/agents/wf-build.md").write_text("agent\n")

    def rows(self, stream):
        """Run a fake agent whose whole stdout is `stream`, and return the rows it left."""
        out = self.root / "agent-stdout.txt"
        out.write_text(stream)
        cfg = driver_config.load(str(support.write_config(
            self.root, agent_cmd=f'cat {out} #{{prompt}}')))
        driver_dispatch.Dispatcher(cfg, driver_events.Telemetry(cfg)) \
                       .launch("wf-build", {})
        return [json.loads(x) for x in
                cfg.path("telemetry").read_text().splitlines() if x.strip()]

    def test_the_dispatch_row_carries_the_cost_and_usage_the_log_reported(self):
        row = self.rows(result_line() + "\n")[0]
        self.assertEqual(row["event"], "dispatch")
        self.assertEqual(row["cost_usd"], 0.9588514)
        self.assertEqual(row["num_turns"], 34)
        self.assertEqual(row["output"], 9158)
        self.assertEqual(row["cache_read"], 1602358)

    def test_a_log_with_no_result_line_still_writes_the_row_it_always_wrote(self):
        row = self.rows("You've hit your session limit\n")[0]
        self.assertEqual(row["event"], "dispatch")
        self.assertNotIn("cost_usd", row)
        self.assertIn("duration_s", row)


class RateLimitWaitTest(support.TempProject):
    """A rate-limited dispatch waits the limit out and goes again, in the one place
    every role's launch passes through."""

    def setUp(self):
        super().setUp()
        (self.root / ".claude/agents").mkdir(parents=True)
        (self.root / ".claude/agents/wf-build.md").write_text("agent\n")
        self.slept = []

    def dispatcher(self, agent_cmd, *, now=1_786_000_000):
        cfg = driver_config.load(str(support.write_config(self.root,
                                                          agent_cmd=agent_cmd)))
        self.cfg_obj = cfg
        d = driver_dispatch.Dispatcher(cfg, driver_events.Telemetry(cfg))
        d.clock = lambda: now
        d.sleep = self.slept.append
        return d

    def refusing_agent(self, *, resets_in=600, fail_times=1):
        """An agent that writes a rate-limited log and exits 1 the first ``fail_times``
        launches, then succeeds — what waiting the limit out is supposed to reach."""
        counter = self.root / "runs"
        script = self.root / "limited-agent.sh"
        script.write_text(
            f"""#!/usr/bin/env bash
n=$(cat {counter} 2>/dev/null || echo 0); n=$((n+1)); echo $n > {counter}
if [ "$n" -le {fail_times} ]; then
cat <<'WF_EOF'
{limit_event(1_786_000_000 + resets_in)}
{RESULT_LINE}
WF_EOF
  exit 1
fi
echo done
""")
        script.chmod(0o755)
        self.runs = counter
        return f'{script} "{{prompt}}"'

    def test_a_rate_limited_dispatch_sleeps_to_the_reset_and_launches_again(self):
        d = self.dispatcher(self.refusing_agent(resets_in=600))
        result = d.launch("wf-build", {}, task_id="T1")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(self.runs.read_text().strip(), "2")
        self.assertEqual(sum(self.slept), 600 + driver_dispatch.RATE_LIMIT_MARGIN_S)

    def test_the_returned_launch_points_at_the_log_of_the_attempt_that_ran(self):
        d = self.dispatcher(self.refusing_agent())
        result = d.launch("wf-build", {}, task_id="T1")
        self.assertIn("done", result.log_path.read_text())
        self.assertNotIn("api_error_status", result.log_path.read_text())

    def test_waiting_is_bounded_and_the_refusal_is_handed_back_to_the_caller(self):
        d = self.dispatcher(self.refusing_agent(fail_times=99))
        result = d.launch("wf-build", {}, task_id="T1")
        self.assertNotEqual(result.exit_code, 0)
        self.assertEqual(int(self.runs.read_text().strip()),
                         driver_dispatch.RATE_LIMIT_WAITS + 1)
        with self.assertRaises(driver_runtime.Pause):
            driver_dispatch.check_launch(result)

    def test_a_stop_asked_for_during_the_wait_ends_it_immediately(self):
        d = self.dispatcher(self.refusing_agent(fail_times=99))
        stop_file = self.cfg_obj.stop_file
        stop_file.parent.mkdir(parents=True, exist_ok=True)
        d.sleep = lambda s: (self.slept.append(s), stop_file.touch())[0]
        result = d.launch("wf-build", {}, task_id="T1")
        self.assertNotEqual(result.exit_code, 0)
        self.assertEqual(int(self.runs.read_text().strip()), 1)  # never relaunched

    def test_the_wait_is_polled_in_slices_so_a_stop_is_seen_within_one(self):
        d = self.dispatcher(self.refusing_agent(resets_in=600, fail_times=99))
        d.launch("wf-build", {}, task_id="T1")
        self.assertTrue(all(s <= driver_dispatch.RATE_LIMIT_POLL_S for s in self.slept))

    def test_a_clean_launch_never_waits(self):
        d = self.dispatcher('bash -c "true" "{prompt}"')
        self.assertEqual(d.launch("wf-build", {}).exit_code, 0)
        self.assertEqual(self.slept, [])

    def test_a_failure_that_is_not_a_rate_limit_never_waits(self):
        d = self.dispatcher('bash -c "echo boom; exit 7" "{prompt}"')
        self.assertEqual(d.launch("wf-build", {}).exit_code, 7)
        self.assertEqual(self.slept, [])

    def test_a_timed_out_dispatch_is_never_mistaken_for_a_rate_limit(self):
        # The bound killed it; the log may still carry an earlier limit event.
        d = self.dispatcher(self.refusing_agent())
        timed_out = driver_dispatch.Launched("wf-build", 124, 60, True,
                                             self.root / "role.log", "cmd", {})
        with self.assertRaises(driver_runtime.Pause) as caught:
            driver_dispatch.check_launch(timed_out)
        self.assertEqual(caught.exception.reason, "launch_timeout")

    def test_the_wait_is_recorded_in_telemetry(self):
        d = self.dispatcher(self.refusing_agent(resets_in=600))
        d.launch("wf-build", {}, task_id="T1")
        rows = [json.loads(x) for x in
                self.cfg_obj.path("telemetry").read_text().splitlines()]
        waits = [r for r in rows if r["event"] == "rate_limit_wait"]
        self.assertEqual(len(waits), 1)
        self.assertEqual(waits[0]["role"], "wf-build")
        self.assertEqual(waits[0]["wait_s"], 600 + driver_dispatch.RATE_LIMIT_MARGIN_S)

    def test_dry_run_never_waits(self):
        d = self.dispatcher(self.refusing_agent())
        d.dry_run = True
        self.assertEqual(d.launch("wf-build", {}).exit_code, 0)
        self.assertEqual(self.slept, [])


if __name__ == "__main__":
    unittest.main()
