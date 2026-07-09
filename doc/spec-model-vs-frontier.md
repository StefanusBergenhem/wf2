# wf2 spec model vs. the agentic spec-driven-development frontier

**Date:** 2026-07-08 (updated 2026-07-09)
**Author:** review requested against the frontier (Spec Kit v0.8.7, Kiro, BMAD, Tessl, Microsoft "spec-first" guidance)
**Worked example:** the `dems` boundary-centric domain remodel slice (`.wf/transient/design-slice.md`, REQ-14..89, SYS-TC-2..18, ADR-001..006)

> **Update 2026-07-09:** the mechanical pre-build consistency check (the frontier's `/analyze`)
> has shipped as `wf sprint check`, gating the SWA→build seam. Its design, implementation, and
> validation sections have been removed from this doc. The open points below — the clarification
> gate, interface/data contracts, NFRs/authz, and a requirement register — remain for a later
> session.

---

## TL;DR

wf2 is **ahead of the frontier on traceability and rigor, and behind it on**:

1. a systematic **clarification / assumption gate** (the frontier's `/clarify` + `[NEEDS CLARIFICATION]`),
2. **up-front interface / data contracts** (data models + API contracts in the spec),
3. **measurable NFRs / an authz contract** per slice, and
4. a **durable, readable requirement register**.

Nothing in the model is broken. With the `/analyze`-style consistency gate now shipped
(`wf sprint check` on the SWA→build seam), the remaining structural gap is the **SA→SWA
handoff** — the clarification gate; the rest are SA-side under-specification and one downstream
view. This doc names each and maps it to a role.

---

## The pipeline, with the frontier overlaid

Actual wf2 flow: **discover → PO → SA → SWA → build → review** (retrospective / ship at closeout).

| Phase | output | Spec Kit / Kiro analog | Gated? |
|---|---|---|---|
| **discover** | brief + structure map (re-derived) | *Kiro steering — but wf's is derived, not hand-maintained* | input, refreshable |
| **PO** | CAPABILITIES.yaml | `/specify` + `/clarify` | ✅ human sign-off (wf-po Phases 5–7) |
| **SA** | REQ + ADR + SYS-TC + slice | `/specify` + `/clarify` + `/plan` | ❌ ungated handoff |
| **SWA** | sprint.yaml: AC + task DAG | `/tasks` + `/analyze` | ✅ `wf sprint check` gate |
| **build** | code + `[REQ]` tags | `/implement` | TDD self-check |
| **review** | adversarial gatekeep | *(no Spec Kit analog — wf is ahead)* | ✅ adversarial gate |

**Structural diagnosis:** the gates sit at the ends — PO human sign-off at the front,
adversarial review at the back — plus, now, the `wf sprint check` gate on the SWA→build seam.
The remaining ungated handoff is **SA→SWA**: the slice is transient and simply consumed, so the
interpretive risk of capability→REQ (SA) flows through a seam where nothing stops and checks.
The frontier's two newest commands are `/clarify` and `/analyze`; wf2 now has `/analyze`
(`wf sprint check`) but not `/clarify`.

Reframed by axis:

- **code↔spec grounding: wf is ahead, on both ends.** discover reads code→understanding at
  the front; `[REQ]`/`[SYS-TC]` tags + reconcile read spec→code at the back. That is *more*
  than Spec Kit, which only recently bolted on `/analyze`.
- **spec↔spec-internal consistency: wf is behind, at the SA→SWA seam.** discover grounds the SA
  in what *exists*, but a slice's job is to introduce what *doesn't* yet (here: `internal/compliance`,
  the widened `ImportResult`, new seams). discover can't pre-map those, and by the draining
  philosophy their structure only becomes real once discover re-derives it *after* build. So
  there is a window — SA→SWA→build — where the new interface is specified **nowhere**.
  (`wf sprint check` now checks that the AC→task→SYS-TC decomposition is internally complete at
  the SWA→build seam; the missing *interface contract* — #2 below — is what remains.)

---

## Where wf2 is genuinely ahead — keep these

1. **Tag-harvested reconcile beats prose links.** Spec Kit and Kiro *reference* requirement
   IDs in tasks as text. wf stamps `[REQ:REQ-N]` in the proving test and mechanically
   reconciles it — a drift-*proof* trace, not a drift-*prone* one. This is the strongest thing
   in the model and most SDD tools don't have it.
2. **EARS + INCOSE C1–C9 is more disciplined than freeform functional requirements.** The
   "one owning component," complementary-pair, and "no smuggled design" rules exceed the
   mainstream toolkits' requirement hygiene.
3. **"No mocks at seams" SYS-TC + allocation-completeness catches the nil-wire class.** Most
   spec tools stop at "tasks complete." wf's definition of done explicitly hunts the
   compiled-but-does-nothing bug. (The dems slice even names it: cmd/server wiring as "this
   slice's nil-dependency risk.")
4. **discover-first is real brownfield grounding.** Spec Kit is greenfield-biased with no
   "sense the existing system" step; Kiro's steering files are the only analog, and wf's is
   better because it is derived and re-run after moves rather than hand-maintained.
5. **ADR discipline** (3-condition threshold, immutability, density guidance) is tighter than
   most frameworks' decision records.

---

## The gaps, ranked, each mapped to a role

### 1. Clarification gate thins exactly where interpretive leverage is highest — *the SA*

The PO has a real disambiguation → open-questions → readback → sign-off loop (wf-po Phases
5–7). But the SA is where one capability becomes dozens of requirements + several ADRs, and it
clarifies only **reactively** (halt after 3 revisions). There is no `[NEEDS CLARIFICATION]`
marker and no assumptions register in the slice handover.

Evidence from the worked example: CAP-002's meaning was **recast** during design
("several boundaries of different purpose" → "requirements of several domains reach one derived
boundary"), and that reinterpretation survives only as a *buried note* in CAPABILITIES.yaml, not
as a gated, confirmed decision. For a 76-requirement remodel, one wrong capability reading
propagates through the whole slice before anything catches it.

**Fix:** an **"Assumptions requiring confirmation"** section in the design-slice template — the
`/clarify` idea adapted to the SA→SWA handover. The SA marks each interpretive leap; the slice
carries them; the human (or PO) confirms before the SWA decomposes. Cheap, and it turns the
CAP-002-style recast from a note into a decision.

### 2. Interface and data contracts are deferred to build — *the SA + the SWA*

The 2026 consensus spec carries "requirements, acceptance criteria, **data models, and API
contracts in a single document**." wf specifies REQ (behavior) + ADR (decision) + a diagram,
then tells the SWA to "read the source" and lets the build discover the actual struct shapes,
the new `internal/compliance` interface, and the new endpoint request/response schemas. The
worked slice **introduces a whole new component and widens the highest-fan-in one
(`internal/domain`)** — and the concrete seam for neither is written down. AC like "the import
result carries geometry fields" is behavior; "here is the `ImportResult` struct" is the
contract. wf has the first, not the second — and that missing seam is precisely where the
nil-wire bug the slice fears actually lives.

**Fix:** an optional **interface-contract block** in the task contract (or the ADR) for tasks
that introduce a new component or widen a shared seam — the signature/struct/endpoint shape,
not just the behavior.

### 3. Zero NFRs in a slice with visible performance cliffs and no authz — *the SA*

The requirement-syntax reference has an excellent measurable-NFR section (5 elements) — and the
worked slice uses **none of it**, while carrying at least two unbounded fan-outs: REQ-34
(re-evaluate compliance of *all* affected members on any membership change) and REQ-64 (startup
revalidation re-evaluates *every* active entity). Both are performance cliffs with no stated
envelope. Separately, REQ-53/54/57 ship a pile of new CRUD + attachment endpoints with **no
authorization contract**, in a product whose roadmap has enterprise auth. Only the SA can emit
these — the SWA can only write AC for requirements it is given.

**Fix:** the SA's requirement-authoring checklist should force an NFR/authz pass per slice, even
if the answer is an explicit, recorded deferral rather than a silent absence.

### 4. No durable, readable requirement register — *a generated view, post-review*

The draining model (capabilities drain, backlog drains, slice/sprint transient) means "what does
the system require, in total, right now?" has **no single answer document** — you reconstruct it
from `[REQ]` tags in tests + ADRs + re-derived discover. Defensible (draining is how wf avoids
spec-code drift, a real advantage over Tessl's heavy sync machinery). But the cost is sharp for a
compliance product: its own dev process discards its requirement narrative; a new engineer or
auditor cannot read the spec.

**Fix:** *generate* a read-only requirement register from the `[REQ]`/`[SYS-TC]` tags, the way
discover generates the structure view. Recovers the auditable spec without reintroducing a
hand-maintained doc that drifts.

**Net:** one gap is a *missing middle gate* (#1, the SA→SWA clarification seam), two are *SA
under-specification* (#2 contracts, #3 NFRs/authz), one is a *downstream view* (#4 register).
discover doesn't touch any of them — discover is about code that exists; the middle is about
specs for code that doesn't yet.

---

## Priority

1. **Slice "Assumptions requiring confirmation"** (gap #1) — a template section + an SA convention.
2. **Requirement register view** (gap #4) — generated from tags, peer of discover.
3. **Interface-contract block + NFR/authz pass** (gaps #2, #3) — SA-side authoring discipline;
   treat as slice-review notes rather than model surgery.

---

## Sources

- Martin Fowler — [Understanding SDD: Kiro, spec-kit, Tessl](https://martinfowler.com/articles/exploring-gen-ai/sdd-3-tools.html)
- GitHub Spec Kit (v0.8.7, May 2026) — `/constitution` `/specify` `/clarify` `/plan` `/tasks` `/analyze` `/implement` `/checklist`
- Microsoft for Developers — [Spec-Driven Development: AI-Native Engineering](https://developer.microsoft.com/blog/spec-driven-development-ai-native-engineering)
- TrueFoundry — [Spec-Driven Development for AI Agents: Governing Specs](https://www.truefoundry.com/blog/spec-driven-development-ai-agents)
- BCMS — [Spec-Driven Development: The Definitive 2026 Guide](https://thebcms.com/blog/spec-driven-development)
