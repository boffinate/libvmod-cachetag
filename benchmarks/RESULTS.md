# Cachetag benchmark results

Last updated: 2026-08-20

Rules reviewed: BR-001..BR-026; applicable: BR-001, BR-002, BR-004, BR-007, BR-008, BR-011, BR-013, BR-014, BR-016, BR-017, BR-018, BR-019, BR-020, BR-021, BR-023, BR-024, BR-025 and BR-026. The wall-CPU claims comply; the optional `perf stat` evidence is retrospectively partial and report-only under the strengthened BR-024 coverage rule.

Cachetag used less process memory than xkey in all three static in-memory workloads tested. The difference was largest when tags were mostly unique and smallest when 4 KiB object bodies dominated the cache footprint. Cachetag also used slightly less `cache-main` CPU to load and index the fixed population. On warm hits the two are now indistinguishable at the resolution of this measurement: cachetag's remaining median gap to xkey is 0.45–1.09%, below the run-to-run noise floor on every workload.

These results describe synthetic workloads on one server cohort. They do not show maximum throughput, production traffic, invalidation cost, long-running lifecycle behaviour, or performance with Fellow or Buddy storage.

## Results

### Process memory after load

The memory measure is confirmation PSS for the provenanced Vinyl `cache-main` process after loading 100,000 objects, draining pending attachment work and waiting for a quiescent endpoint. It is a common whole-process measure; implementation-specific counters are not used for the comparison.

| Workload | Cachetag median PSS (min–max) | xkey median PSS (min–max) | Cachetag difference |
| --- | ---: | ---: | ---: |
| Mostly unique tags, 2-byte body | 108.954 MiB (108.895–109.116) | 188.421 MiB (188.387–188.499) | 42.18% lower |
| Mostly shared tags, 2-byte body | 109.108 MiB (108.963–109.199) | 144.004 MiB (143.853–144.066) | 24.23% lower |
| Moderate sharing, 4 KiB body | 511.785 MiB (511.721–511.831) | 550.168 MiB (549.982–550.325) | 6.98% lower |

The 2-byte lanes isolate index and object-metadata costs. They are not a model of a typical cached response. The 4 KiB lane puts the index difference in the context of ordinary object data: cachetag saved about 38.4 MiB across 100,000 objects, but the proportional difference fell to 6.98% of total process PSS.

These absolute PSS values come from `vinyltest`, whose child process uses its non-production `abort:true,junk:true` allocator configuration. The within-cohort comparison is valid; the absolute totals should not be treated as a production sizing guide.

### CPU for the fixed load and attachment work

The load measure covers the exact 100,000-object workload and cachetag's pending-attachment drain. Values are phase-aligned CPU consumed by `cache-main`, not whole-container CPU or requests per second.

| Workload | Cachetag median (min–max) | xkey median (min–max) | Cachetag difference |
| --- | ---: | ---: | ---: |
| Mostly unique tags | 330.87 µs/object (330.57–332.06) | 341.40 µs/object (337.37–345.57) | 3.09% lower |
| Mostly shared tags | 328.31 µs/object (327.05–329.12) | 331.05 µs/object (330.68–333.93) | 0.83% lower |
| Moderate sharing, 4 KiB body | 349.39 µs/object (347.99–353.86) | 355.99 µs/object (354.48–357.59) | 1.86% lower |

Cachetag's median pending drain was 0.151–0.197 ms, compared with 6.4–7.7 seconds for the fixed load. The drain was included in the measurement, but was not a material part of the result.

### CPU for warm hits

Superseded on 2026-08-20 by the `step-4-vsc-publish-policy-decision-v1` campaign, which re-measured warm-hit CPU on a new cohort with the **two-call VCL shape** that [`docs/usage.md`](../docs/usage.md) documents: `tags.stale()` in `vcl_hit` *and* in `vcl_deliver`. The earlier table below the fold measured a one-call shape and is retained as supersession history, not mixed in here. The two shapes are not comparable and the harness refuses to merge them: the VCL shape is folded into every row's cohort fingerprint.

Warm traffic was offered at 5,000 requests per second for 60 seconds, below the demonstrated driver and server knee. The useful comparison is CPU per successful hit, not achieved rate or maximum throughput. Figures are medians of two physical rows per cachetag arm and four per xkey arm, each of three repetitions.

| Workload | Cachetag median | xkey median | Residual gap | Noise floor (2 × A/A) |
| --- | ---: | ---: | ---: | ---: |
| Mostly unique tags | 74.005 µs/hit (73.43–74.58) | 73.670 µs/hit (72.95–73.99) | 0.45% | 1.383% |
| Mostly shared tags | 74.525 µs/hit (73.84–75.21) | 73.720 µs/hit (72.90–74.06) | 1.09% | 1.748% |
| Moderate sharing, 4 KiB body | 73.900 µs/hit (73.33–74.47) | 73.510 µs/hit (73.09–74.99) | 0.53% | 3.959% |

**No directional claim is made from this table.** On every workload the residual gap is smaller than twice the same-code xkey A/A spread, which is the campaign's own bar for calling a difference real. Post-change cachetag warm-hit CPU is indistinguishable from xkey's at this resolution; the medians are reported so the figures are on record, not because the ordering means anything.

Those are the current cachetag figures, which include the VSC publish policy. The same campaign measured the immediately preceding cachetag tree on identical hardware, harness and VCL, which is what the publish policy changed:

| Workload | Cachetag before the publish policy | Cachetag after | xkey | Gap to xkey, before → after |
| --- | ---: | ---: | ---: | ---: |
| Mostly unique tags | 75.235 µs/hit | 74.005 µs/hit | 73.670 µs/hit | 2.13% → 0.45% |
| Mostly shared tags | 75.135 µs/hit | 74.525 µs/hit | 73.720 µs/hit | 1.92% → 1.09% |
| Moderate sharing, 4 KiB body | 74.660 µs/hit | 73.900 µs/hit | 73.510 µs/hit | 1.56% → 0.53% |

What is and is not claimed here. The **improvement** from the publish policy is directional on the mostly-unique workload, where it measured 1.670% against a 1.383% bar with both bracketed comparisons agreeing; on the other two workloads it is below the wall-CPU noise floor and no directional claim is made. The **before** gap to xkey exceeds the floor on the two 2-byte workloads and not on the 4 KiB one. The **after** gap is below the floor everywhere, as the table above says.

Warm-phase instructions per hit were lower by 1.37–1.55% in both physical before/after brackets on all three workloads. This is supporting mechanism evidence only: `perf stat` ran on repetition 1 of each three-repetition row (`n=1/3`), and repetition 1 was systematically hotter than later repetitions. The resulting 0.010–0.171% two-point A/A spreads are not reusable noise floors and do not support a judged instruction-count claim under BR-024. The harness now requires complete repetition coverage before reporting such a comparison.

All rows met the frozen offered-load and scheduling gates. Median achieved rates were about 4,950–4,960 requests per second with no request errors. Because the campaign deliberately ran below saturation, these figures do not establish either implementation's throughput capacity.

Latency differences were small and inconsistent. Neither campaign authorised a directional latency claim, so none is made here.

## Workloads

Every lane loaded 100,000 objects with four tags per object and 400,000 object-tag relationships. Warm access was deterministic and uniform-cyclic.

- **Mostly unique tags:** 400,000 distinct tags, each attached to one object; 100,000 distinct tag sets; 2-byte bodies. This is deliberately adverse to xkey's per-key index.
- **Mostly shared tags:** four distinct tags, each attached to all 100,000 objects; one shared tag set; 2-byte bodies. This is deliberately favourable to xkey's key-head amortisation.
- **Moderate sharing, 4 KiB body:** 100,000 distinct tags with fanout four; 100,000 distinct tag sets; 4,096-byte bodies. This tests whether the memory difference remains meaningful when object data forms most of the cache footprint.

These bounds are sensitivity tests, not samples of production traffic. The planned CMS-derived workload was not run because the canonical ordered payload, redistribution basis and expected fingerprints were unavailable. No access pattern or purge history was inferred from the incomplete fixture.

## Comparison method

The admitted `reset-in-memory-cachetag-xkey-synthetic-decision-v1.1` campaign ran on 18 August 2026 on one Scaleway `EM-B220E-NVMe` server with an AMD EPYC 7232P, 64 GiB RAM and no swap activity.

The comparison used cachetag commit `2a4bc91f84e4c099812a339eeda11b375365a1f9`, Vinyl commit `61f45d6740f8818e07dffee6edf8b433e93b81fb`, and unmodified xkey 0.28.0 source from varnish-modules commit `7abe0e2a59a685b4ea8626ff1a3fe9c60a037368`. The xkey arm is an unsupported third-party build for Vinyl Cache; these results do not characterise xkey on a supported Varnish release.

Both VMODs were rebuilt for every physical row under the same explicit optimisation policy. The campaign retained source, image, compiler, linker, command and binary provenance. The unmodified upstream xkey suite passed 14/14 on supported Varnish, the Vinyl xkey contract suite passed, and the cachetag suite passed 54/54.

There were 24 valid judged rows: three cachetag repetitions and five xkey repetitions for each workload. The first two xkey rows established the same-code noise floor. Rows then alternated between implementations, used a fresh Vinyl process and fresh build, and were accepted only when provenance, CPU placement, work volume, residency, memory stability, process identity, power state, pacing, CPU telemetry, errors, eviction, expiry and swap checks passed. All 24 rows passed. A direction was reported only when all three bracketed comparisons agreed and the median difference exceeded twice the xkey A/A spread.

The earlier v1.0 execution is excluded. Its scheduling-lag threshold contradicted the calibration evidence and therefore could not support a judged comparison.

## Scope still to test

This campaign measured static-population foreground cost only. It did not exercise hard or soft purge, concurrent invalidation, refill, deferred reclamation, bounded churn, or memory behaviour over a long purge history. Those require a separately frozen invalidation and lifecycle campaign. Persistent Fellow and Buddy storage also require their own comparisons and should not be mixed with these in-memory results.

The comparison was frozen as a synthetic decision round before remote execution. Its 24 judged rows used fixed workload fingerprints, fresh arm builds, interleaved execution, three repetitions per row, explicit process identity and placement, quiescent memory endpoints, and fail-closed workload and residency gates. Raw campaign artifacts are retained outside this repository.

## Supersession history

### Warm-hit CPU, one-call VCL shape, 2026-08-18 (superseded)

The `reset-in-memory-cachetag-xkey-synthetic-decision-v1.1` campaign measured warm-hit CPU with `tags.stale()` called once, in `vcl_hit` only. [`docs/usage.md`](../docs/usage.md) documents a two-call contract, so that shape understated cachetag's per-hit cost by roughly half of the VMOD's contribution. The 2026-08-20 campaign re-measured on the documented shape and on a different server; under BR-014 the two sets are from different hardware cohorts and must not be compared with each other. The superseded table, verbatim:

| Workload | Cachetag median (min–max) | xkey median (min–max) | Cachetag difference |
| --- | ---: | ---: | ---: |
| Mostly unique tags | 79.599 µs/hit (79.52–80.32) | 77.792 µs/hit (77.24–79.61) | 2.32% higher |
| Mostly shared tags | 79.336 µs/hit (78.82–79.60) | 78.103 µs/hit (77.32–79.57) | 1.58% higher |
| Moderate sharing, 4 KiB body | 80.891 µs/hit (80.38–81.98) | 79.733 µs/hit (79.61–81.90) | 1.45% higher |

The memory and load-CPU results above still come from that 2026-08-18 campaign and its cohort; the 2026-08-20 campaign made no memory claim and is not a substitute for them.

### The 2026-08-20 campaign

`step-4-vsc-publish-policy-decision-v1`, run on a Scaleway `AS -2014TP-HTR` (`em-elastic-bose`) with an AMD EPYC 7232P, 64 GiB RAM, no swap, all governors `performance` and boost enabled. Three arms were rebuilt fresh for every physical row: pinned xkey 0.28.0, the immediately preceding Cachetag counter-surface tree, and the periodic VSC publisher candidate. Both Cachetag arms were production-surface builds with set interning disabled and `-O2 -g`, and ran a byte-identical frozen harness against the same Vinyl build.

The campaign produced 24 valid judged rows plus one profiling capture, with no replacements. The decision rests on the request-path mechanism disappearing from the profile, no warm-p99 regression, load CPU improving in all three lanes, and wall CPU improving beyond the frozen noise bar on the mostly-unique lane. The other two wall-CPU lanes and the partial instruction counters are not directional claims. Raw campaign artifacts are retained outside this repository.
