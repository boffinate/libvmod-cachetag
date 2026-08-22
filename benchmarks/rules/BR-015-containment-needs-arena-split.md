# BR-015: Storage-containment claims need an arena/heap decomposition

**Rule:** Do not judge a storage engine's memory-containment promise from
whole-process RSS or anon PSS; decompose full `/proc/<pid>/smaps` into the
engine's own mapping(s) versus non-storage process memory first.

**Why:** Buddy's 1M row ended at ~7.5 GB anon RSS against a 4 GiB configured
arena, which read as a containment failure until full smaps proved the arena
mapping fixed at exactly 4,194,304 kB across every cycle; the residue was
non-arena process memory — a separate whole-process sizing caveat, not a Buddy
defect.

**Comply by:** Capturing full `smaps` (not just `smaps_rollup`) at the endpoints
that matter; reporting the engine mapping bound and the non-engine residue as
separate findings.

**Tripwire:** None — full-smaps capture is opt-in per campaign.
