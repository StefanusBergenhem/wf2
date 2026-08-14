Input to SA ADR logic

Good news: your instinct is already in the literature, and this codex has it mapped in unusual depth. Let me give you
  the sharp version, then challenge the frame you're using.

  You're filtering on the wrong axis

  You're asking "how big is the decision?" — magnitude, impact. That criterion can't be operationalized, for two
  reasons:

  1. Impact is only knowable in hindsight. At decision time everyone believes their decision is high-impact. It's
  unfalsifiable in the moment, which is exactly the moment you need to decide whether to log it.
  2. A huge decision can be fully recoverable, and a tiny one totally invisible. "We use Postgres" is huge but any agent
  reads it off the connection strings and schema in seconds — logging it is waste. retry_count = 3 is tiny but if the 3
  is there because the upstream SLA gives you exactly three attempts inside the timeout window, that's invisible and
  load-bearing, and an agent "cleaning up" will happily change it to 5 and break prod.

  The codex's concept-design-rationale lands on the criterion that actually works, and it's not magnitude. It's this:

  ▎ Rationale is the only artifact that distinguishes essential constraints from accidental ones.

  Code makes every line look equally load-bearing. Tests tell you what must hold but never why (the behavior could be
  the spec, a 2021 customer dependency, or a bug nobody noticed — all four look identical). State docs tell you what is,
  not what must be. The thing none of them carry is: which constraints can I change, and which will bite me. That's the
  entire job of a logged decision.

  The operational test (checkable at decision time)

  Replace "is this a bigger decision?" with three questions, all three must be yes:

  1. Is there a real "why not"? Did you reject a viable alternative? If there was never another live option ("we used
  the standard library"), there's no rationale to record — it's just how it is. No rejected alternative → don't log.
  2. Is the "why" invisible in the code? Could a competent agent reading the repo reconstruct it? If yes → don't log;
  that's your own principle, correctly applied.
  3. Is a wrong guess expensive? If someone treated this constraint as arbitrary and changed it (or treated an arbitrary
  thing as sacred and refused to touch it), what's the blast radius? Cheap to get wrong → let them rediscover it.

  All three yes → log it. This produces "bigger decisions" as an output without you ever defining "big." And critically
  — unlike "is this high-impact?" — all three are answerable by an agent at the moment of the decision, which means you
  can put them in a prompt. That's the unlock for your wf rework.

  A good litmus shortcut is the Y-statement format the codex pulls from the ADR tradition: "In the context of X, facing
  Y, we decided Z, to achieve Q, accepting downside D." If you can't fill in a real accepting D, you probably didn't
  make a decision with a rejected alternative — so don't log it.

  What is structurally irrecoverable (your "what cannot be recovered")

  Short positive list — these are the only things code genuinely cannot carry:

  - The rejected alternatives. Once you pick, the option space collapses. Code shows the one path, never the three you
  killed. This is the thing.
  - External forces that live outside the repo — a vendor SLA, a regulation, a hardware quirk, a customer commitment, a
  deadline trade-off. The code shows 4096; it can't show "= page size, cache-aligned, do not touch without
  re-benchmarking."
  - Deliberately accepted trade-offs — "we chose latency over consistency here." Code shows the choice, not that it was
  a choice with a known cost.
  - The negative space — the bug you're working around, the approach you tried that failed. Invisible by construction;
  an agent will "fix" your workaround straight back into the bug.

  The AI-world inversion you may be under-weighting

  You're reasoning "code is cheap to refactor, so logging matters less." The codex points the opposite way, and it's
  worth sitting with:

  When refactoring is free, the cost of a wrong refactor rises relative to the refactor itself — and rationale is the
  only friction left. An agent reading only code + tests + state cannot tell essential from accidental, so it does one
  of two damaging things: freezes (treats everything as load-bearing, won't touch anything) or vandalizes (treats
  everything as malleable, changes the thing that mattered). Both come from the same missing layer. Cheap, fearless
  refactoring makes that layer more valuable, not less, because the only thing standing between your tuned retry logic
  and an agent's "improvement" is a record saying "load-bearing, here's why."

  Two corollaries for the wf:

  - Capture at decision time, never reconstruct. Ask an agent to recover rationale from finished code and it doesn't go
  silent — it confidently invents plausible justifications (concept-ai-assisted-design). The capture has to be a
  precondition of the design step, not a later "document the codebase" pass. The agent's reasoning lives in its context
  for minutes, then it's gone.
  - The capture-cost constraint has flipped. For 40 years the binding constraint was "logging the why must be cheap or
  humans skip it" — which is why Nygard's 5-field ADR beat Kruchten's rich ontology. Agents dissolve that cost (an LLM
  emits 14 fields as cheaply as 5). So your new binding constraints are faithfulness (invention) and retrieval (a log
  nobody reads, or one that goes stale, is negative value — concept-design-scaling: stale documentation is worse than
  none).

  It's not ADR-or-nothing — it's tiered

  Your instinct to keep ADRs "for bigger decisions" is right but risks a binary trap. Three tiers:

  - Comment at the site — for a local load-bearing constraint (the 4096, the magic retry count). Co-located, dies with
  the code if the code dies, can't go stale silently. This is where most irrecoverable-but-local rationale belongs.
  - Commit message / PR body — for "why this change, why now, what I rejected." This is rationale capture at decision
  time, free, and git blame-able. You may be under-using this as a rationale store and over-reaching for ADRs.
  - ADR — reserve for decisions that (a) span modules / constrain future work, and (b) someone will re-litigate later
  without finding the commit. The real test for ADR-tier: "will this question be asked again, by someone who won't
  git-blame their way to the answer?" That's what justifies a separate durable artifact over a commit message.

  The codex flags the boundary explicitly: ADRs work at architecture scale (O(10–100) decisions/system); unit-level
  rationale at O(1000+) is a different problem and doesn't belong in ADRs — it belongs at the site or nowhere.

  ---
  The one blind spot to name bluntly: "high impact" is doing no work in your filter. It feels like a criterion but it's
  a vibe — unmeasurable in the moment and biased toward whatever you happen to care about that day. Swap it for
  "rejected a real alternative + the why is invisible + wrong guess is costly." That's checkable now, by an agent,
  mechanically — which is the only kind of rule that survives contact with a workflow.