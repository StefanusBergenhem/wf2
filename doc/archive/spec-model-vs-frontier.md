# wf2 spec model vs. the agentic spec-driven-development frontier

**Date:** 2026-07-08 (updated 2026-07-09)
**Author:** review requested against the frontier (Spec Kit v0.8.7, Kiro, BMAD, Tessl, Microsoft "spec-first" guidance)
**Worked example:** the `dems` boundary-centric domain remodel slice (`.wf/transient/design-slice.md`, REQ-14..89, SYS-TC-2..18, ADR-001..006)

> **Update 2026-07-09 (i):** the mechanical pre-build consistency check (the frontier's
> `/analyze`) has shipped as `wf sprint check`, gating the SWA→build seam. Its design,
> implementation, and validation sections have been removed from this doc.
>
> **Update 2026-07-09 (ii):** the four open points have shipped; their gap sections are
> removed per the same convention. Where each landed:
>
> 1. **Clarification gate** — the design-slice carries an *Assumptions requiring
>    confirmation* section; wf-sa marks each interpretive leap (Phase 3), the human
>    ratifies it at alignment (Phase 4), and the SA gates its own handoff with
>    `wf slice check` before cutting the slice loose — with `wf sprint check`
>    (finding A3, same implementation) as the build-seam backstop. The SWA carries
>    no check of its own: the producer validates its output, and the consumer's
>    existing `sprint check` gate catches a slice that slipped through.
> 2. **Interface / data contracts** — the slice carries an *Interface contracts* section
>    (SA-authored concrete shapes for new components / widened seams); the task contract
>    carries an optional `interface_contract` field copied verbatim from it; the build
>    treats deviation as a contract design issue.
> 3. **NFRs / authz** — wf-sa Phase 3 runs a per-slice NFR & authz pass (scaling triggers
>    get a measurable envelope, new entry points an authz requirement — or an explicit
>    recorded deferral in the slice's *NFR & authz* section, never a silent absence).
> 4. **Requirement register** — `tools/reconcile/register.py` derives a read-only markdown
>    register (id, statement, proving tests, both lanes) from the `[REQ]`/`[SYS-TC]` tags
>    on demand — generated like discover's structure view, never hand-maintained.

---

## TL;DR

wf2 is **ahead of the frontier on traceability and rigor**. The four gaps this review
found — clarification gate, up-front interface/data contracts, per-slice NFRs/authz, a
readable requirement register — **shipped 2026-07-09** (see the update note for where
each landed). What remains of this doc is the pipeline comparison and the "keep these"
list, as the standing record of where wf2 stands relative to the frontier.

---

## The pipeline, with the frontier overlaid

Actual wf2 flow: **discover → PO → SA → SWA → build → review** (retrospective / ship at closeout).

| Phase | output | Spec Kit / Kiro analog | Gated? |
|---|---|---|---|
| **discover** | brief + structure map (re-derived) | *Kiro steering — but wf's is derived, not hand-maintained* | input, refreshable |
| **PO** | CAPABILITIES.yaml | `/specify` + `/clarify` | ✅ human sign-off (wf-po Phases 5–7) |
| **SA** | REQ + ADR + SYS-TC + slice | `/specify` + `/clarify` + `/plan` | ✅ assumptions ratified at Phase 4; `wf slice check` on its own handoff |
| **SWA** | sprint.yaml: AC + task DAG | `/tasks` + `/analyze` | ✅ `wf sprint check` gate |
| **build** | code + `[REQ]` tags | `/implement` | TDD self-check |
| **review** | adversarial gatekeep | *(no Spec Kit analog — wf is ahead)* | ✅ adversarial gate |

**Structural diagnosis:** every seam is now gated — PO human sign-off at the front,
adversarial review at the back, `wf sprint check` on the SWA→build seam, and the
assumptions-requiring-confirmation loop on SA→SWA (human ratification at wf-sa Phase 4,
`wf slice check` run by the SA on its own handoff, `sprint check` A3 as the build-seam
backstop). The
frontier's two newest commands are `/clarify` and `/analyze`; wf2 has equivalents of
both — `/clarify` as the assumptions loop, `/analyze` as `wf sprint check`.

Reframed by axis:

- **code↔spec grounding: wf is ahead, on both ends.** discover reads code→understanding at
  the front; `[REQ]`/`[SYS-TC]` tags + reconcile read spec→code at the back. That is *more*
  than Spec Kit, which only recently bolted on `/analyze`.
- **spec↔spec-internal consistency: closed at both remaining seams.** discover grounds the SA
  in what *exists*, but a slice's job is to introduce what *doesn't* yet (here: `internal/compliance`,
  the widened `ImportResult`, new seams). discover can't pre-map those, and by the draining
  philosophy their structure only becomes real once discover re-derives it *after* build. The
  window where a new interface was specified **nowhere** is now covered by the slice's
  *Interface contracts* section (carried verbatim into the task contract); `wf sprint check`
  covers the AC→task→SYS-TC decomposition at the SWA→build seam.

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

## Sources

- Martin Fowler — [Understanding SDD: Kiro, spec-kit, Tessl](https://martinfowler.com/articles/exploring-gen-ai/sdd-3-tools.html)
- GitHub Spec Kit (v0.8.7, May 2026) — `/constitution` `/specify` `/clarify` `/plan` `/tasks` `/analyze` `/implement` `/checklist`
- Microsoft for Developers — [Spec-Driven Development: AI-Native Engineering](https://developer.microsoft.com/blog/spec-driven-development-ai-native-engineering)
- TrueFoundry — [Spec-Driven Development for AI Agents: Governing Specs](https://www.truefoundry.com/blog/spec-driven-development-ai-agents)
- BCMS — [Spec-Driven Development: The Definitive 2026 Guide](https://thebcms.com/blog/spec-driven-development)
