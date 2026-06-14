# Brainstorm Patterns

When the user's input has obvious gaps, a vague verb is dangling, or you can offer concrete alternatives they may not have considered, propose 2–4 candidate capabilities as a bounded choice and let the user pick, override, or write their own.

## The boundary

Brainstorming surfaces ideas as **user-capability statements** (candidate CAP-NNN entries), never as components, scopes, technology choices, or architectural decisions.

| Surface as a CAP-NNN candidate | Out of scope for brainstorming |
|---|---|
| "User can search across order history." | "You'll need Elasticsearch for full-text search." |
| "Admins can view who changed what, when." | "You'll want an audit-log microservice." |
| "Operator gets paged on a failed payment retry." | "Use PagerDuty for paging." |

## When to brainstorm

Trigger whenever any of these surface during conversation:

### 1. Vague improvement verb

User says "improve X", "streamline X", "make X better" without specifics.
→ Brainstorm 2–4 concrete capabilities that "improve X" might mean.

### 2. Missing coverage of an obvious category

User describes the happy path but not error handling. Or the read flow but not the write flow. Or features but not observability. Or visible UI but not admin ops.
→ Brainstorm 1–3 likely-missing capabilities in the underspecified category.

### 3. Single-noun product mention

User says "build a dashboard" with no detail about what's on it.
→ Brainstorm 2–3 specific capabilities the dashboard might have.

### 4. Common adjacency

A product class usually implies certain capabilities — auth, observability, admin, audit, backup, error reporting. If the user named the product class and did not mention these, they may have assumed them implicitly.
→ Brainstorm 2–3 likely-implicit capabilities.

### 5. User asks "what should I add?"

Explicit invitation. Surface 2–4 candidates based on what's already in the file, what similar products typically have, or gaps you noticed during intake.

## How to brainstorm

Present 2–4 options as a bounded choice. Each option is a concrete proposed capability in user-voice, stated in one line. Always let the user select several, override, or write their own — usually several apply; restrict to a single pick only when the options are mutually exclusive (e.g. one of N versions of the same feature).

Worked example — for "improve dashboard":

```
For "improve dashboard", here are some candidates — which apply? (pick any, or add your own)
  1. Surface unresolved first — dashboard puts unresolved errors at the top, most recent first.
  2. 30-day revenue trend — chart of the last 30 days with day-over-day delta.
  3. Top 5 errors today — the 5 most frequent error types in the past 24h.
  4. User retention panel — active-user count + week-over-week retention curve.
```

## How many to brainstorm at once

- **2–4 options per question** (keep the choice bounded).
- **At most 1–2 brainstorm rounds in a row.** If you want a third round, the user's input is too vague to brainstorm against — ask directly: "What problem are we trying to solve here? Help me anchor."

## What NOT to brainstorm

- **Wholesale new products.** Stay inside the user's stated scope. Building a dashboard does not mean proposing a mobile app.
- **Aesthetic / UI-detail preferences.** "Blue button vs green button" is too low-level for product capability work.

## Anti-patterns

- **Filling silence.** Do not brainstorm because the user paused. Brainstorm because there's a gap.
- **Overwhelming.** Two consecutive option-laden questions is cognitive overload. Surface, wait for the user to integrate, then maybe ask again.
- **Padding the file.** Do not propose capabilities just to make `capabilities:` longer. The smallest worthwhile slice should stay small.
- **Asserting brainstormed items as facts.** "You'll want X" is presumptuous. Phrase as "Have you considered X?" or offer it as a bounded choice.
- **Brainstorming in component-voice.** "You'll need an admin panel" pre-empts architecture. Reshape as: "Admins can do A, B, C — relevant?"
