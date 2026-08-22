# BR-016: Never reuse a build cache across an arm or storage-kind change

**Rule:** Do not pass `SKIP_BUILD=1` across a change of code arm (baseline vs
patched) or storage kind; reject any row whose VSC output contains counters that
do not exist in its own arm's source.

**Why:** A 2026-07-12 baseline sampler run reused a build cache still containing
the patched VMOD and emitted patched-only counters
(`purgemap_auto_reclaim_passes`, `sweep_obj_mtx_hold_usec`) in a "baseline" row;
it had to be excluded from every comparison. A Default-storage cache reused for
a Buddy row produced a void `pass=0 fail=0 total=0` result.

**Comply by:** Rebuilding on every arm or storage-kind switch; spot-checking one
arm-distinguishing counter's presence/absence per arm before accepting a
campaign; treating `total=0` rows as failures, never as clean passes.

**Tripwire:** Implemented (2026-07-15) — `benchmarks/build_provenance.sh` records
content hashes of the cachetag/Vinyl/Slash build inputs at build time
(`/work/build-provenance.env`, copied into each result dir); every
`SKIP_BUILD=1` run re-hashes the mounted sources and fails loud on any mismatch
or on a storage-kind/Slash mismatch. `CACHE_TAG_ALLOW_STALE_BUILD=1` downgrades
the failure to a warning for a deliberate, labelled stale reuse. Docs and
harness-only edits are excluded from the hash, so they do not invalidate a
cache. Existing build caches predate the provenance file and will fail their
first `SKIP_BUILD=1` run — rebuild once to mint provenance.
