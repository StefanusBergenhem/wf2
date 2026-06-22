---
name: wf-testing-anti-patterns
description: Test-quality anti-patterns catalogue — common mistakes that make tests misleading, brittle, or worthless. Read during build before writing a test and during review to reject a bad one.
---

# Testing Anti-Patterns — What NOT to Do in Tests

## How to use this skill

- **Build:** before writing any test, scan the Quick Reference. If a test matches an
  anti-pattern, restructure it before committing.
- **Review:** check every submitted test against the table. A match is grounds for
  rejection — cite the AP number.
- **Verification:** a test that exhibits these patterns does not count as valid evidence
  of correctness.

The Quick Reference table is the canonical hot-loop content. For worked code examples and
rationale, read `references/anti-patterns-detailed.md` only when the one-line check is not
enough.

## Quick Reference

| # | Anti-Pattern | One-Line Check |
|---|-------------|----------------|
| 1 | Testing implementation | Does refactoring break this test without changing behavior? |
| 2 | Mocking what you own | Am I mocking code I could use a real/fake implementation for? |
| 3 | Weak assertions | Would this pass if I deleted the implementation? |
| 4 | Testing private methods | Am I accessing `_` prefixed or internal-only members? |
| 5 | Snapshot overuse | Would I actually review this snapshot diff, or just update it? |
| 6 | Bad test names | Can I understand what broke from the name alone? |
| 7 | Shared mutable state | Can I run any test in isolation and get the same result? |
| 8 | Only happy path | Have I tested error paths, boundaries, and edge cases? |
| 9 | Copy-paste tests | Are these tests identical except for one or two values? |
| 10 | Coincidental field equality | Do all fields that can differ in production have distinct sentinel values? |
| 11 | Exact-count on shared state | Am I asserting an exact total count on a store other tests also write? |
| 12 | Bare negative-boundary test | Does this "does NOT" assertion have an AC annotation or allow-list form? |
| 13 | Depends on state seeded elsewhere | Could a sibling truncating the store, or a missing migration, make this assertion fail? |

## The meta-rule

A good test has three properties:

1. **It fails when the feature breaks.** (Not too weak.)
2. **It passes when the feature works, regardless of implementation.** (Not too coupled.)
3. **When it fails, the name and output tell you what went wrong.** (Not too vague.)

If a test lacks any of the three, it needs rework.
