Rules reviewed: BR-001..BR-027; applicable: BR-002 (B1/P1/B2/P2 order, complied), BR-008 (user-authorized dedicated agent and one host workload at a time, complied), BR-011 (serial latency only), BR-014/025 (one matched host, boot and power cohort), BR-016/017 (fresh optimized harness cohort), BR-018 (no replacement), BR-019/020 (fixed work and residency), BR-023 (descriptive tail samples), BR-024 (two baseline rows and three repetitions each), BR-026 (owner-approved twelve-repetition budget), and BR-027 (actual optimized production-object compile commands).

# Optimized Fellow io_uring strict-WAL latency rerun

## Scope and cohort

This owner-approved development screen repeats the August serial persistent purge-header shape with the corrected io_uring Fellow backend and optimized Slash/Fellow production objects. It consists only of B1, P1, B2, P2 in that order, with three repetitions each. It does not support throughput, CPU, disk-utilisation, cross-date, release-acceptance, or general-production claims.

The raw data is under `benchmarks/remote-results/20261013_51.159.202.68/fellow-optimized-io-uring-latency/`. The exact run IDs are B1 `remote-20261013T061938Z-fellow-local-cost-100k`, P1 `remote-20261013T062324Z-fellow-local-cost-100k`, B2 `remote-20261013T062612Z-fellow-local-cost-100k`, and P2 `remote-20261013T062910Z-fellow-local-cost-100k`.

The raw root retains the resolved per-row launcher environments, source/build manifests, Docker summary, raw samples, both harness patches, and remote image/liburing metadata. After those files were archived, the host had no running benchmark container and the dedicated ephemeral image was removed; `provenance/remote-ephemeral-image-removal.txt` and `provenance/remote-post-cleanup.txt` retain the removal receipt.

The host was `ubuntu@51.159.202.68`, boot ID `0fc14bda-0dd9-4bf9-be3d-d673fa57cad4`, with all governors `performance`, boost `1`, and no benchmark container before B1 or after any row. The dedicated image was `vinyl-cache-ubuntu-build-uring-20261012`, `sha256:179d6975051ceda89021723d3d21f7f19c8a8c98e74ef3fa10b6c9f64ad92c36`, labelled `ai.ephemeral=true`. The comparison-specific fingerprint is `9e07840fb9421a4e60d07042ac05095a23208ec28829ad4434bd7a365aea4c9c` for all twelve repetitions.

The optimized harness is `d23537a309201303674c23c71f9e6486ef79c99b`, tree `ecff77441a39ad80de06ad003453505c24b8844c`; baseline is `6eb351c3cae67aa021715c6bd5083466bfe7f7b7`; candidate is `44ab2b92a1577e6a0147ef003edebc6f310d0535`. Vinyl is `0783f5243cea4ae2b0236ba635ef7dc39efdee66`, Slash `7be4126892dbc58a03f701632e076f312e0332ed`, and VTest2 `2e646cc556c8b0e4730b19b90a3df6fcc91a3091`.

## Admission and validity

Every row records `fellow_io_uring_compiled=1`, `fellow_io_uring_runtime_probe=passed`, and `fellow_io_uring_security_opt=seccomp=unconfined`. Every measured VTC log records `io_uring register_buffers_choice CLONE (5)` and `registering 1 buffer`, with no registration error. The measured `libfellow_la-fellow_io_uring` and `libvmod_slash_la-fellow_storage` compiler commands include `-O2 -g`; none include `-O0` or `-fno-inline`.

Each repetition passed VTC, retained 100 raw serial latency samples, published 100 of 100 headers with 1,000 keys, and made 960 of 960 post-purge freshness probes miss, with zero driver errors. Baseline rows require ten WAL records per accepted header; candidate rows require one. Docker summarization reports 3/3 valid and process-passing repetitions per row, with no swap. It reports the load/warm phases as inconclusive for throughput, which is outside this serial-latency scope.

Independent raw-sample recomputation agrees with all driver p50/p95/p99/max values to under 1e-8 seconds across all 1,200 samples. The retained `provenance/independent-raw-sample-check.json` records the calculation. `provenance/docker-summary.txt` contains the Dockerized summary.

## Result

The repetition p50 values in milliseconds are B1 42.0991355, 41.7810725, 43.0151630; P1 4.2562330, 4.3140010, 4.2654495; B2 41.8627760, 43.1138685, 41.8478675; and P2 4.4864905, 4.3419895, 4.4837205.

The B1/P1 median-repetition p50 difference is 37.8336860ms, an 89.8680829% reduction. The B2/P2 difference is 37.3790555ms, an 89.2894812% reduction. The six baseline p50 observations span 1.3327960ms; twice that spread is 2.6655920ms. Both paired reductions have the same direction and exceed that frozen threshold.

The raw p95/p99/max samples remain descriptive rather than an acceptance criterion. A few isolated tail observations are retained in the raw sets; this screen makes no tail-budget claim. The narrow development verdict is positive for this exact serial, strict-WAL, 100-header-by-10-key persistent Fellow operation only.

## Relation to earlier evidence

The 2026-10-12 August B1 remains a diagnostic-only io_uring correctness artifact because its Slash/Fellow objects were unoptimized. The historical August result also used the threads backend and unoptimized Fellow objects. Neither enters this cohort or this comparison. The September restart/sparse rerun remains separate lifecycle correctness evidence.

The [committed latency observations](fellow-optimized-io-uring-latency.json) retain all twelve repetition percentiles, the comparison fingerprint, baseline spread and paired reductions. Full build logs and raw request samples remain in the archived result directories listed above.

## Cold restart and sparse persistence

The separate 2026-10-12 io_uring rerun passed one baseline and one candidate repetition for the September 100,000-object restart/sparse lifecycle. Baseline run `remote-20261012T211734Z-local-cost-warm-100k` used cachetag `2ab3585b9643b88542c03c79447056ae1b153372`; candidate run `remote-20261012T212028Z-local-cost-warm-100k` used `d9d44544b87ad2b0fc635ab0996ccc0178c2cb44`. Both used harness `da663510d82acc5b7f8aa97425a3974d7254c8f7`, Vinyl `b677c39c07541a114ed6e0cf8dde2a94b986cb5f`, and the same Slash and VTest2 sources as the latency cohort. Their archives are retained under `benchmarks/remote-results/20261012_51.159.202.68/fellow-io-uring-rerun/`.

Both arms restarted with 100,000 vampires, zero Fellow gets, zero backend requests and zero direct probes. The 1,000 sparse hits produced 1,000 gets, no backend requests and 1,000 direct probes. Purging untouched IDs left those counters unchanged. Fetching the stale range ended at 2,000 gets, 1,000 backend requests and 2,000 direct probes. Volatile interned sets and references stayed zero at every boundary. Full initial residency passed, with zero driver errors and no swap. Each row records a successful io_uring queue probe and CLONE initialization with one registered buffer. These two repetitions support lifecycle correctness only; they support no timing comparison.
