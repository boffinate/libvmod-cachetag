# BR-002: Interleave baseline/patched rows; never run arms back-to-back

**Rule:** Run before/after campaigns in interleaved order (`B,P,B,P`) or reset
host state between rows; never all-baseline-then-all-patched unless the goal is
explicitly to study host-state accumulation.

**Why:** Sequential arm ordering produced position-correlated tail spikes that
manufactured an apparent regression in whichever arm ran last: the 190 ms P3
refill convoy landed in the last patched row, and a reversed-order campaign
moved the worst tail with the position, not the arm. The interleaving protocol is
now enforced by campaign design.

**Comply by:** Ordering campaign rows `B,P,B,P` in the campaign note's command
list; recording the actual execution order in the note so reviewers can check it.

**Tripwire:** None — ordering is a design-time decision; the campaign note's
required rules-checked line is the control.
