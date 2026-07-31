#!/usr/bin/env python3
"""Tests for the convergence machinery — reading close-time adequacy digests and
parking a capability three consecutive inadequate verdicts could not prove.
Run: python3 tools/driver/tests/adequacy_test.py
wf2-source-only — never rendered into an install target.
"""
from __future__ import annotations

import unittest

import support  # noqa: F401

import adequacy
import config as driver_config

CAPS = """\
# CAPABILITIES — the durable why (committed). This comment must survive.
version: 1
capabilities:
  - id: "CAP-001"
    statement: "Users can patch a zone — ✓ unicode intact."
    status: planned
    notes: ""
  - id: "CAP-002"
    statement: "Users can bulk-patch."
"""


class DigestTest(support.TempProject):
    def setUp(self):
        super().setUp()
        self.cfg = driver_config.load(str(support.write_config(self.root)))
        self.cache = self.cfg.path("drill_cache")
        self.cache.mkdir(parents=True, exist_ok=True)

    def digest(self, cap, question, stamp, verdict):
        path = self.cache / f"adequacy-{cap}-{question}-{stamp}.md"
        path.write_text(f"# Adequacy: {cap} — {verdict}\n**Question:** {question}\n")
        return path

    def test_newest_full_promise_digest_wins(self):
        self.digest("CAP-001", "full-promise", "20260101T000000Z", "inadequate")
        newest = self.digest("CAP-001", "full-promise", "20260201T000000Z", "adequate")
        self.assertEqual(adequacy.newest_digest(self.cfg, "CAP-001"), newest)
        self.assertEqual(adequacy.verdict_of(newest), "adequate")

    def test_iteration_claim_digests_do_not_count_toward_the_park(self):
        self.digest("CAP-001", "iteration-claim", "20260101T000000Z", "inadequate")
        self.digest("CAP-001", "iteration-claim", "20260102T000000Z", "inadequate")
        self.digest("CAP-001", "iteration-claim", "20260103T000000Z", "inadequate")
        self.assertEqual(adequacy.consecutive_inadequate(self.cfg, "CAP-001"), 0)

    def test_consecutive_count_is_the_trailing_run_of_inadequate_verdicts(self):
        self.digest("CAP-001", "full-promise", "20260101T000000Z", "inadequate")
        self.digest("CAP-001", "full-promise", "20260102T000000Z", "adequate")
        self.digest("CAP-001", "full-promise", "20260103T000000Z", "inadequate")
        self.digest("CAP-001", "full-promise", "20260104T000000Z", "inadequate")
        self.assertEqual(adequacy.consecutive_inadequate(self.cfg, "CAP-001"), 2)
        self.digest("CAP-001", "full-promise", "20260105T000000Z", "inadequate")
        self.assertEqual(adequacy.consecutive_inadequate(self.cfg, "CAP-001"), 3)

    def test_no_digest_at_all_counts_zero(self):
        self.assertEqual(adequacy.consecutive_inadequate(self.cfg, "CAP-404"), 0)
        self.assertIsNone(adequacy.newest_digest(self.cfg, "CAP-404"))


class ParkTest(support.TempProject):
    def setUp(self):
        super().setUp()
        self.cfg = driver_config.load(str(support.write_config(self.root)))
        self.caps = self.cfg.path("capabilities")
        self.caps.parent.mkdir(parents=True, exist_ok=True)
        self.caps.write_text(CAPS)

    def test_park_rewrites_only_the_status_line_of_that_entry(self):
        self.assertTrue(adequacy.park_capability(self.cfg, "CAP-001"))
        text = self.caps.read_text()
        self.assertIn("# CAPABILITIES — the durable why", text)
        self.assertIn("✓ unicode intact", text)
        self.assertIn('  - id: "CAP-001"\n', text)
        entry = text.split('- id: "CAP-002"')[0]
        self.assertIn("status: parked", entry)
        self.assertNotIn("status: planned", text)
        self.assertIn('  - id: "CAP-002"\n    statement: "Users can bulk-patch."', text)

    def test_park_inserts_a_status_when_the_entry_carries_none(self):
        self.assertTrue(adequacy.park_capability(self.cfg, "CAP-002"))
        tail = self.caps.read_text().split('- id: "CAP-002"')[1]
        self.assertIn("status: parked", tail)

    def test_parking_an_already_parked_entry_is_a_no_op_write(self):
        adequacy.park_capability(self.cfg, "CAP-001")
        before = self.caps.stat().st_mtime_ns
        self.assertFalse(adequacy.park_capability(self.cfg, "CAP-001"))
        self.assertEqual(before, self.caps.stat().st_mtime_ns)

    def test_parking_an_unknown_id_changes_nothing(self):
        self.assertFalse(adequacy.park_capability(self.cfg, "CAP-404"))
        self.assertEqual(self.caps.read_text(), CAPS)


if __name__ == "__main__":
    unittest.main()
