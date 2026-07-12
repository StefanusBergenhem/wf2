# Testing & quality-gate infrastructure — audit reference

The target state for a repo's verification machinery, the smells that reveal a
gap, and the fix direction. Read this before judging a repo's test/gate setup:
compare what the repo does against each **Target**, name the **Smell** you see,
and propose the **Fix**. You do not enforce these as hard rules — several are
calibration calls (see "Judgement calls"); flagging a healthy choice as a defect
is worse than staying silent.

Each dimension names the wf config key that holds the gate, and the target-repo
tools that can implement it. Use `wf audit coverage` and the discover component
map as the mechanical evidence, never a hand-wave. Fixes you own directly are
config/gate/tool wiring; a fix that means writing product tests or product code
routes to the build pipeline.

**Dimensions:** 1. Coverage (per-component floors + patch coverage) · 2. Gate
tiering & parity · 3. Test taxonomy · 4. Static analysis gates · 5. Architectural
fitness · 6. Determinism & flakiness · 7. CI / PR merge gate · Judgement calls.

---

## 1. Coverage — two complementary gates

Coverage has two independent healthy gates. A repo wants **both**; they answer
different questions and neither substitutes for the other.

### 1a. Per-component floors (what `wf audit coverage` gives you)

**Target.** Every source component carries an explicit decision — a floor scaled
to how much logic it holds, or an exclusion with a stated reason — in
`paths.coverage_policy`, keyed by discover uid, reconciled against the live
component map every run.

**Smells.**
- A source component with no decision (`COV-UNDECIDED`) — it can regress to zero
  and nothing fails.
- A well-tested component with **no floor guarding it** — the most dangerous case,
  invisible to any single coverage number.
- A single global % as the only gate — a 90%-overall repo can hide a 40%-covered
  core module.
- A floor nothing measures (`COV-UNMEASURED`); an exclusion with no reason, or a
  reason that has gone stale (a "pure types" exclusion on code that grew logic).

**Fix.**
- Give every component a floor or reasoned exclusion; run `wf audit coverage`
  until green. Scale the floor to risk — core logic high, thin wiring/adapters
  low or excluded-with-reason.
- The repo's own gate should express floors per-component, not one global number.
  Native support exists: Jest `coverageThreshold` per-glob (a *negative* value =
  max uncovered count, a clean ratchet); nyc `--per-file`; `.NET coverlet`
  `ThresholdStat=Minimum` (per-module by default); go-test-coverage
  (`threshold.file`/`package`/`total` + per-package overrides). `pytest-cov`
  gates the global total only — layer diff-cover (below) or per-package CI jobs.

### 1b. Patch / diff coverage (did this change test itself?)

**Target.** The PR gate measures coverage on the **changed lines only** and
requires them to be well-tested, decoupled from the aggregate. This is the
"clean as you code" principle: hold new code to a high bar instead of chasing the
whole-repo number.

**Smells.**
- The only coverage gate is a whole-repo target — it fails PRs that merely
  refactor or *delete* code (both can lower the aggregate), and it lets new
  untested code in as long as the average stays up.
- Devs pad diffs with trivial tests to clear a global bar.

**Fix.**
- Add a patch-coverage gate: Codecov `codecov/patch` status, SonarQube/SonarCloud
  new-code quality gate, or **diff-cover** (language-agnostic — rides on any
  Cobertura/LCov/JaCoCo report, no SaaS: `diff-cover coverage.xml --fail-under=80`).
- Ratchet with a tolerance band, not a hard zero-drop — pair "never decrease" with
  a small threshold and a removed-code exemption, or it over-fires on every
  refactor and the team learns to ignore it.

### The caveat that governs both

Coverage answers "was this line executed," never "would a bug here be caught."
Empirically the two diverge hard — an AI-written suite can hit 100% line coverage
while catching ~4% of injected faults. So a floor is a floor, not a goal; do not
push toward 100%. Where fault-detection stakes are high, add **mutation testing**
(Stryker / PIT; kill-score floor ~60%, target ~80%) as the stronger signal. A
coverage gap is closed by a real behaviour test, a justified dead-code removal, or
a trace to a missing requirement — never by writing tests "to the code."

---

## 2. Gate tiering & parity

**Target.** Gates layer by speed. A **fast gate** (`commands.preflight`: lint +
type-check + unit + build) runs before every handoff in seconds. A **heavy gate**
(`commands.stage_check`: integration / e2e / contract) runs at the stage boundary
in minutes. The CI merge gate is a **superset** of both. Optionally a pre-commit
layer runs sub-second checks on staged files only.

**Smells.**
- CI runs checks no local gate runs — a green local run gives false confidence and
  the failure only surfaces on the PR (a late, expensive round-trip).
- The fast gate is not fast (multi-minute) — developers stop running it, which
  starts the trust-erosion cycle.
- A tier CI runs (e.g. a migration-replay suite) that no `commands.*` key invokes.
- A pre-commit hook running the whole build / integration tests, or `eslint .`
  over the entire repo instead of staged files.

**Fix.**
- Make CI the source of truth, then ensure `commands.preflight` +
  `commands.stage_check` together cover everything CI gates. Any CI step with no
  local counterpart is the gap — wire it into the matching tier.
- Keep the fast gate genuinely fast. For a pre-commit layer use the `pre-commit`
  framework (scope slow checks to `stages: [pre-push]`/`[manual]`; pin every
  `rev`) or husky + lint-staged (runs on **staged files only**). In a monorepo,
  gate the affected set (Nx `affected`, Turborepo `--affected`) so the fast tier
  scales.
- Accept the double-accounting: hooks are skippable (`--no-verify`), so **every
  hook check must also run in CI**. The hook is a convenience; CI is the gate.

---

## 3. Test taxonomy

**Target.** Each tier does one job and only that job — **unit** (a component's
behaviour/contract; the bulk), **integration** (real collaborators wire
together), **contract** (an interface's promise across a boundary), **e2e** (a
user-visible flow; the fewest, because slow and fragile).

**Smells.**
- Logic branches covered *only* through slow e2e tests (the "ice-cream cone") —
  slow, flaky, and it hides which unit is actually untested.
- Unit tests asserting on internal call sequences / private structure instead of
  observable behaviour — they break on every refactor.
- E2e used as the way to hit coverage numbers on core logic.

**Fix.**
- Push each check down to the cheapest tier that can prove it — a logic branch
  belongs in a unit test, not an e2e path.
- Rewrite implementation-coupled tests to assert observable behaviour.

---

## 4. Static analysis gates

**Target.** Lint, format, and type-check are enforced gates inside
`commands.preflight`, not advisory. CI fails on new warnings so drift cannot
accumulate. Anything a deterministic tool can decide is decided by the tool, not
by review.

**Smells.**
- A linter/formatter/type-checker configured in the repo but wired into no gate
  (it runs in editors, never blocks a merge).
- Warnings tolerated in CI — they accumulate until they are noise.
- Review comments re-checking what a linter could enforce mechanically.

**Fix.**
- Fold the existing lint/format/type-check into `commands.preflight`.
- Set the CI/release profile to fail on warnings (keep local debug lenient so it
  doesn't block spikes).

---

## 5. Architectural fitness (dependency / boundary enforcement)

**Target.** Every load-bearing architectural rule — layer boundaries, allowed
dependency directions, "all X go through Y", acyclicity — is a **mechanical check
wired into a gate**, not prose in a design doc. The rule lives in a tool the repo
runs and fails the fast gate on violation. Manual review barely catches
system-wide dependency drift; encode the rule and let CI block violations.

**Fix families — pick per ecosystem:**
- **Compiler-level (unbypassable, zero maintenance, but only expresses
  containment/visibility):** Go `internal/` (the toolchain rejects outside
  imports, no override), Rust `pub(crate)`/`pub(super)`. Reach here first when the
  rule is "this stays private to X".
- **Test-based (expresses directional/layered rules the compiler cannot):**
  import-linter (Python — `forbidden`/`layers`/`independence` contracts),
  dependency-cruiser (JS/TS), deptrac (PHP, deny-by-default), ArchUnit (JVM —
  fitness rules as JUnit tests), ts-arch (TS), ESLint `boundaries` /
  `no-restricted-imports` / `import/no-restricted-paths`.

**Smells.**
- A dependency/boundary rule stated only in an ADR or design-slice soundness
  paragraph, enforced by nothing.
- A new cycle between components, or a dependency pointing from stable toward
  volatile, that no check would reject.
- Arch rules configured but all set to `warn` — a warning that never fails is not
  a gate.

**Fix.**
- For each durable boundary decision, name the tool rule that enforces it and wire
  it into `commands.preflight`. Prefer the compiler mechanism where it can state
  the rule; use a test-based tool for directional/layered constraints.
- **Adopting on a legacy repo:** do not try to reach zero violations first.
  Baseline the current violations and fail only *new* ones — ArchUnit
  `FreezingArchRule.freeze(...)`, import-linter `ignore_imports` as a visible debt
  ledger. The baseline shrinks over time; the gate holds the line from day one.

---

## 6. Determinism & flakiness

**Target.** Tests are deterministic and isolated — no wall-clock, no unseeded
randomness, no network reliance, no shared mutable state; any order and any subset
passes. Stateful integration tests disable the runner cache (a cached "pass" masks
live state it never re-exercised) and isolate fixtures per run/worktree.

**Smells.**
- Tests that pass alone and fail together (shared mutable state — a "test run
  war"). The five usual root causes: lack of isolation, async timing, remote
  services, time, resource leaks.
- Intermittent failures tolerated / re-run until green — flakiness erodes trust,
  then developers stop running the suite, then quality collapses.
- Integration tests relying on a runner cache for stateful resources.
- A quarantine folder with no owner, no ticket, no expiry — a graveyard that only
  grows.

**Fix.**
- Fix the root: seed randomness, inject clocks, isolate per-test fixtures, break
  up oversized tests (flake rate scales with test size). Bust the cache for
  stateful suites (e.g. Go `-count=1`); give parallel runs isolated resources
  (per-worktree DB names, suffixed log paths).
- **Quarantine with a leash, don't delete.** Move a flaky test off the
  merge-blocking path but keep it running, with an owner, a filed ticket, an
  expiry/cap, and auto-re-enable once it stops flaking. Auto-retry is allowed to
  *unblock and detect*, but every retry must record a flake event and a fix
  obligation — never let "green on retry" silently close the loop.

---

## 7. CI / PR merge gate

**Target.** The PR gate blocks a merge on any failure and is the enforced source
of truth: build, static analysis, unit + integration/e2e, coverage policy
(`wf audit coverage`) + patch coverage, and the architectural checks all run and
all must pass. Required status checks + branch protection make it non-bypassable
(admins included; "require branches up to date"; only **deterministic** checks are
required — quarantine the rest). At high merge rates, a merge queue re-tests each
PR against the latest base to catch semantic conflicts two green branches create.

**Smells.**
- Checks that run but don't block (informational-only status on things that matter).
- A gate a developer (or admin) can merge past.
- Coverage / architecture audits that run locally but are absent from CI.
- Flaky/non-deterministic checks in the *required* set — they train the team to
  click through red.

**Fix.**
- Make the wf gates (`preflight`, `stage_check`, `wf audit coverage`, boundary
  checks) required status checks on the protected branch; enable "up to date".
- On a broken mainline, the fastest fix is usually to revert the offending commit,
  not to fix forward under pressure. Run the *same* gate command locally and in CI.
- Affected-only on PRs is fine for speed, but run the full suite post-integration —
  affected-and-full, not either/or.

---

## Judgement calls (calibrate, do not dogmatically enforce)

- **Coverage as a number is Goodhart-bait.** Coverage finds *untested* code well;
  it is a poor statement of test *quality*, and a mandated 100% signals gaming.
  The well-known "80%" figure is folklore (its famous origin is a satirical post).
  Flag *unguarded* and *undecided* components; do not chase a percentage.
- **Patch-coverage friction is real.** A hard patch gate over-fires on tiny
  changes (hence Codecov's `informational` mode and Sonar's "ignore until ≥20 new
  lines"). Starting informational, then flipping to blocking once stable, is a
  legitimate path — don't insist on a hard gate from day one.
- **Test shape.** The classic pyramid (mostly unit) and the "testing trophy"
  (mostly integration) are both legitimate; the right shape depends on where the
  repo's risk lives. Don't flag a repo merely for having many integration tests.
- **Test doubles.** Prefer real implementations, then verified fakes, then stubs,
  then mocks (mocks only when the interaction *is* the behaviour). "Don't mock what
  you don't own" — wrap third-party deps behind an adapter and mock the adapter.
  Heavy mocking is a *design* smell, surfaced as advice, not a gate failure.
- **"Fitness function" is one consultancy's brand** (Thoughtworks) for a widely
  adopted idea; the enforcement tools are independent OSS. Sell the principle
  (encode the rule, let CI block it), not the vendor vocabulary or the
  anecdotal "N× fewer violations" numbers.
- **Delete vs fix vs quarantine.** Default is quarantine-then-fix; delete a flaky
  test only as an explicit, tracked decision when it covers no critical path.
