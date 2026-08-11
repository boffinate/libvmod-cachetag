# Set-interning lookup-first optimization result

Checkout provenance: implementation `b5f423f` (`Optimize small set interning lookups`) and benchmark harness `83b773f` (`Measure acknowledged load-phase cache CPU`). Artifact revision fields are blank, so the recorded build-input hashes, not those commit labels, are the authoritative within-campaign identities.

## Change

For membership sets of two through eight folds, attach now canonicalizes into a bounded stack array and looks in the active and retiring intern tables before allocating a candidate. A miss allocates the exact candidate outside `obj_mtx`, then retries the lookup before publication. Five-tag shared hits therefore avoid the allocation/free cycle entirely. Sets above eight folds retain the prebuilt-candidate path.

The patch also replaces generic `qsort` with insertion sort for the bounded small-set path, removes clock reads and `counter_mtx` acquisition when allocation timing is disabled, and caps request-driven migration to one bounded batch per attach across allocation retries.

## Verification

- Docker targeted interning tests: 5/5 passed (`pm00030` through `pm00034`).
- Docker full set-interning-enabled suite: 59/59 passed.
- Docker set-interning-disabled suite: 38/38 passed.
- `pm00034` proves that a five-tag shared hit does not consume the forced candidate-allocation failure, and covers the exact eight/nine-fold optimization boundary.
- Luna reviewed ownership and retry paths. Its defensive invalid-input leak finding was fixed before the final suites; it found no live race or double free in the changed paths.
- `git diff --check` passed before commit.

No host-local build, type check, or test was used.

## Fixed-work benchmark

The remote host was provisioned again at `ubuntu@51.159.202.218` in `cachetag-bench-set-interning-opt-20260811`. The campaign repeated the previous acknowledged load-phase experiment: Default storage, eight clients, one million all-miss objects, five tags/object, no xkey, no no-index lane, no warm phase, no purge in the measured interval, `BENCH_INSTRUMENT_OBJ_MTX=0`, and required `task-clock,instructions,cycles,ref-cycles` counters attached to the unique `cache-main` process before requests began.

Rows were fresh-built and interleaved B1/P1/B2/P2, with three shared and three unique repetitions per row. All four archives match their SHA-256 sidecars. All 24 phase-stat manifests report `valid=1`, exact target identity before and after the phase, four counter rows, child exit zero, exactly 1,000,000 requests/backend objects/published objects, 5,000,000 edges, zero driver/attach/parse/limit errors, zero LRU nukes, and zero swap. All builds record cachetag input hash `f4d745a10b86a701f69a79e90cf4efcd615e86b8897b3120b4cdfad1fe7b6e3d` and Vinyl input hash `eb02e251a2c79341d13ad7e99190b3f50571c7b514eb7184468d02beaa5c8a06`.

Artifacts: `benchmarks/remote-results/20260811_51.159.202.218/set-interning-fixed-work-cost-optimized/`.

## Retired instructions per object

| Profile | B1 median | P1 median | P1 minus B1 | B2 median | P2 median | P2 minus B2 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Shared five-tag set | 348,416.410 | 348,578.128 | +161.718 (+0.0464%) | 348,345.002 | 348,538.269 | +193.266 (+0.0555%) |
| Unique five-tag sets | 349,536.957 | 351,131.236 | +1,594.279 (+0.4561%) | 349,600.231 | 351,319.163 | +1,718.932 (+0.4917%) |

The ratio of averaged row medians is +0.0509% shared and +0.4739% unique. Unique sets now add about 9.3 times as many instructions per object as the shared set case.

Same-code row-median drift was 0.0205% at most for shared and 0.0535% at most for unique. Twice those floors is 0.0410% and 0.1070%, respectively. Both enabled-minus-disabled comparisons reproduce their positive sign and exceed the corresponding floor. The shared result is only just above that decision threshold and should be read as roughly 0.05%, not a highly precise point estimate. The unique result is clearly around 0.46–0.49%.

Within-row instruction spreads were 0.0518%/0.1009%/0.0545%/0.1671% for shared B1/P1/B2/P2 and 0.0463%/0.1691%/0.1187%/0.0467% for unique. They are descriptive rather than the same-code BR-024 floor, but reinforce the caution against over-precise point estimates, especially for the shared residual.

Compared with the pre-optimization measurements, shared overhead fell from +0.3701/+0.3627% to +0.0464/+0.0555%, an 85–87% reduction. Unique overhead fell from +0.6029/+0.6221% to +0.4561/+0.4917%, a 21–24% reduction.

Task-clock did not reproduce a sign for the shared case. It rose in both unique comparisons, but with substantially more variation than retired instructions, so instructions remain the primary result.

## Interpretation

The allocation-first design was the dominant avoidable CPU cost for shared sets. Lookup-first has reduced that case to a very small residual cost: canonicalizing and hashing five folds, table lookup/reference accounting, and the intern-specific branches.

Unique sets still pay for canonicalization, hashing, intern-table lookup and growth, a candidate allocation, a second locked lookup after allocation, and publication. Their retained-memory cost is a separate question and is not included in the percentages above.

This is a fixed-work CPU result, not a throughput or saturation claim.
