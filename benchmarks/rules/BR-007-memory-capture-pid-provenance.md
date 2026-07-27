# BR-007: Memory captures must prove which process they measured

**Rule:** Do not accept per-cycle or per-phase memory evidence (RSS/PSS/smaps)
unless the artifact records the selected PID, `comm`, and executable, and the
selected process is the `cache-main` worker rather than the small `vinyld`
manager.

**Why:** Two full Phase 6 campaigns (2026-07-14, commits `9e479a0`, `cbc0cc0`)
had their entire memory series rejected because the capture selected the
manager process (a constant ~7 MB) instead of the worker. The fix (`3cd87bc`)
added candidate/selected provenance plus a tripwire that fails the row when the
selected RSS is implausibly small against `index_memory_bytes`.

**Comply by:** Using `capture_phase6_memory.sh` (or an equivalent that records
`selected_pid`/`selected_comm`/`selected_exe` and the tripwire result); rejecting
rows whose provenance or tripwire is absent rather than reinterpreting them.

**Tripwire:** Implemented — the capture script records provenance and a
partial-cycle RSS tripwire (`tripwire=pass|fail|not-required`) in each
`.phase6_memory` artifact.
