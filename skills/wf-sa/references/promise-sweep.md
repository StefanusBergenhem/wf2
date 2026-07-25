# The promise sweep — where a capability breaks while its happy path passes

Every universal in a capability's statement (*every* entity, *when* anything changes,
*no longer* evaluated, *any* user) is a quantifier its scenario set must reach. Sweep
each class below against the quantifiers; each is a class where the promise fails
while every existing scenario stays green:

- **Every write path** to the state the promise governs — not just the read/query path
  a scenario naturally exercises: handlers, importers, batch/auto paths,
  migrations/seeds.
- **Composition and startup** — what is wired, cached, or memoized at process start
  and never refreshed; what a fresh process does differently.
- **Empty and default state** — a brand-new project/tenant/user with no config rows
  yet; absent optional data; zero-item collections.
- **Each kind** the promise quantifies over — every entity type, rule form, or
  channel — whenever the system dispatches per kind.
- **The negative half** — what the promise says must *stop* happening, exercised
  after the stopping condition, not only before it.
