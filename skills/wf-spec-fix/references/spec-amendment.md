# wf-spec-fix — spec amendment

You classified the issue as **`spec_amendment`**: a requirement or ADR is wrong — unbuildable,
self-contradictory, or contradicting the capability that drives it, while that capability is
sound. Amend the spec, then return to SKILL.md Step 4.

**Load `{{WF_SKILLS_DIR}}/wf-sa/references/requirement-syntax.md` before rewording any
requirement**, and `{{WF_SKILLS_DIR}}/wf-sa/references/adr-rules.md` before touching an ADR.

Make the **smallest** change that resolves the issue, in **every** artifact that carries the
defective statement — a stale copy reproduces the defect:

- the requirement's entry in `paths.design_slice` **and** the same requirement in its
  `paths.design_backlog` design — amend both; the two must not diverge;
- the ADR in `paths.adrs`, when the defect is a recorded decision;
- where the amended statement appears verbatim in the `task_id` contract's `requirements[]` in
  `$sprint_artifact` (skip when `task_id` is `null`) — update that copy to match. Change nothing
  else in the contract: its acceptance criteria and task shape are not yours on this path.

Never reshape a boundary, add requirements, or redesign beyond the defect — a fix that needs
re-design is the over-scope halt (SKILL.md Step 2), not a bigger amendment.

If your amendment retires or changes an already-shipped requirement (one the test tree tags
`[REQ:<id>]` / `[SYS-TC:<id>]`), name it on the report's **Superseded** line in Step 4.

Return to SKILL.md Step 4.
