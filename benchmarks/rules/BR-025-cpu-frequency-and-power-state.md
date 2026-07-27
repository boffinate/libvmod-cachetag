# BR-025: Record CPU frequency/power state; treat drift as a cohort change

**Rule:** Capture the CPU frequency governor, boost/turbo state, and (where
readable) current/max frequencies with each remote campaign's host metadata,
and treat any change in them between rows as a hardware-cohort change under
BR-014 — comparisons across a governor or boost-state change are invalid.
Sustained load can also thermally throttle: on a new host class, check that a
long row's later cycles are not systematically slower than its early cycles
before attributing a within-row trend to the code.

**Why:** General C-benchmarking practice: DVFS governors (`schedutil` vs
`performance`), turbo headroom, and thermal limits move wall time and tail
latency by double-digit percentages with zero code change. This project has
already seen an unexplained 14% same-code swing across a host rebuild
(BR-014's incident) — frequency policy is a plausible, currently-uncaptured
contributor. Rented hosts may change firmware/governor defaults on rebuild.

**Comply by:** Reading `/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor`
and `cpufreq/boost`/`intel_pstate` state in host metadata capture; noting the
values in the campaign note; pinning the governor to `performance` when the
host permits it and recording that choice.

**Tripwire:** Candidate — extend `capture_system_metadata.sh` to record
governor/boost fields so the summarizer's hardware fingerprint can include
them; not yet implemented.
