# BR-008: The benchmark host runs one row at a time

**Rule:** Never run two remote benchmark rows concurrently on the benchmark
host, and never let a subagent touch the benchmark host at all.

**Why:** Rows contend for CPU, memory bandwidth, and disk; concurrent runs
invalidate both rows silently. Sequential execution is therefore part of every
campaign contract.

**Comply by:** Running `scripts/remote-benchmark.sh` invocations strictly in
sequence from one session; using `CACHE_TAG_REMOTE_CLEAN_STALE=1` and fresh
remote dirs per row so a crashed row cannot overlap the next.

**Tripwire:** Partial — the remote wrapper refuses to start when an old labelled
benchmark container exists, which catches most accidental overlap.
