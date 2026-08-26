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

**Comply by:** Reading `/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor` and `cpufreq/boost`/`intel_pstate` state in host metadata capture; noting the values in the campaign note; and failing a remote performance row before workload execution unless every online CPU exposes the `performance` governor. Record the verified governor and boost state in every artifact.

**Tripwire:** `capture_system_metadata.sh` records governor/boost fields for the summarizer's hardware fingerprint, and `verify_performance_governor.sh` fails closed on a missing or non-performance governor.
