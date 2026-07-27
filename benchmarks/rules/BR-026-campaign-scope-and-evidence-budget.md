# BR-026: Freeze campaign scope and define how much evidence is enough

**Rule:** Before remote execution, classify the campaign, map every planned row to an explicit claim, freeze a maximum row/repetition and expected wall-clock budget, and state the stop condition. Use the minimum evidence sufficient for the requested decision. Benchmark rules are conditional constraints on relevant claims, not a cumulative checklist that automatically expands every campaign. Crossing the frozen budget or promoting a screen into a broader decision or acceptance campaign requires owner approval. When exactly one planned repetition is ineligible for its required judged scope for a BR-018-eligible reason, one exact replacement occupies that failed slot and does not count as crossing the frozen budget; the rejected repetition remains documented and cannot contribute evidence.

Campaign classes:

- A **development screen** exists to reject an unsafe or unpromising candidate cheaply. Its results may guide implementation but do not establish a performance delta or release acceptance.
- A **decision round** gathers the minimum repeated evidence needed for a named decision, such as accepting exact geometry and safety counters before sizing a later arm. It does not inherit unrelated release gates.
- A **release acceptance** runs the complete owner-ratified correctness, performance, lifecycle, and comparison matrix needed for a release claim.

**Why:** The Stream 3 growth-runway quick round expanded into A/A screens, interleaved baseline/candidate rows at three shapes, repeated rebuilds, and the start of a twenty-repetition Phase 4 comparison. The rules correctly protected validity within each row, but the campaign plan treated BR-002 and BR-024 as universally additive even though the decisive claims were exact geometry and zero rescue counters. The post-fix three-repetition candidate cohort already answered those claims. The unnecessary expansion consumed roughly ninety minutes before the owner stopped it and clarified that Streams 4 and 5 were to be benchmarked as separate arms.

**Comply by:** Put the following fields in the campaign plan before requesting or using a remote host:

1. **Campaign class:** development screen, decision round, or release acceptance.
2. **Claims:** list the exact decisions the evidence must support and explicitly exclude claims that are out of scope.
3. **Minimum evidence map:** name the smallest row/repetition set that supports each claim. If removing a row would not weaken an in-scope claim, do not run it.
4. **Budget:** state the maximum remote rows and repetitions, expected wall-clock duration, and any expensive rebuild or profiling steps.
5. **Conditional rules:** apply BR-002 interleaving and BR-024 A/A/repetition requirements only when judging a comparative performance delta. Exact deterministic geometry, counter, and correctness gates do not acquire performance arms merely because baseline data is available.
6. **Staged escalation:** run the cheapest rejection screen first, then only the minimum repeated decision evidence. If the campaign has exactly one planned repetition that is ineligible for its required judged scope for a BR-018-eligible reason, one exact slot-for-slot replacement is authorized automatically. This does not authorize a fresh cohort, extra scale, new control except where BR-018 requires an adjacent control, or full acceptance matrix.
7. **Stop and approval boundary:** stop as soon as every in-scope claim passes or a blocking failure is established. Pause for owner approval before replacing a second invalid repetition or adding a lane, arm, scale, diagnostic rerun, profiling pass, or acceptance phase beyond the frozen budget.

Reports record actual rows and elapsed time against the frozen budget. If the budget was exceeded, the report identifies who approved the expansion and why. A user request such as “quick,” “screen,” or “before separate arms” is a hard campaign-class constraint unless the owner explicitly changes it.

**Tripwire:** Partial — the index requires campaign class, claims, budget, and stop condition in plans, and review can reject a plan that omits them. The remote wrapper does not yet enforce a row or elapsed-time budget; until it does, the primary agent must track both and pause at the boundary.
