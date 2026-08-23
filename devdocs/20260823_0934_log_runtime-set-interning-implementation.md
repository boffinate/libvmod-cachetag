# Runtime set-interning implementation log

## 2026-08-23 09:34 BST — Start

- Implementation started from `92f8d54 Retry attaches blocked by side-table migration`.
- The reviewed plan is `devdocs/20260823_0910_plan_runtime-set-interning-option.md`. The user explicitly required that plan to remain uncommitted.
- Existing unrelated untracked files at start: `README-OLD.md` and `TODO.md`. They are out of scope and must remain untouched.
- Work is split across supervised subagents with disjoint ownership: core index/purgemap representation; VCL/build/VTC wiring; benchmark generation/provenance/profiling. The root agent owns integration, review, Docker verification, and this log.
- No host-local VMOD, benchmark-helper, Go, or generated-VTC compilation or type-checking will be used. Verification follows the Docker/OrbStack runbook.

## Initial interface decision

- Runtime mode is `enum cachetag_membership_mode { TAG_MEMBERSHIP_DIRECT, TAG_MEMBERSHIP_INTERNED }` in the public internal index header.
- `cachetag_index_new()` receives the immutable mode.
- `cachetag_index_interning()` exposes read-only mode inspection for VCL diagnostic-hook rejection.
- The core implementation will replace compile-time-polymorphic temporary membership ownership with typed direct-vector and intern-candidate preparation.

## Core implementation

- Both membership representations are now compiled into every VMOD. `cachetag_index` stores an immutable mode selected by the namespace constructor; the mode does not enter the namespace digest or persistent identity.
- The `cachetag_objent` remains 24 bytes and does not gain a per-object discriminator. Each index interprets its multi-fold membership pointer according to its immutable mode.
- Multi-fold attachment now uses a typed prepared-membership owner. Direct mode transfers a fold vector to the object entry; interned mode transfers or discards an unpublished candidate and retains the canonical reference. The common caller cleanup owns every value that was not transferred.
- The first prepared-membership draft embedded eight-fold scratch in the shared value. Review identified that this would enlarge the direct attach stack frame. Scratch was moved into the intern-only helper so direct preparation does not declare it.
- Attach, probe, invalidation, sweep, detach, delete, resize maintenance, counters, memory gauges, rollback, and test hooks now select the representation at runtime. Direct namespaces do not allocate an intern table and retain zero intern counters and gauges.
- A direct-vector allocation failure hook was added. Representation-specific hooks reject the wrong namespace mode without arming latent state.

## VCL, build, documentation, and tests

- The namespace constructor appends `BOOL interning = false`. Existing positional arguments remain stable and the default preserves direct vectors.
- The `--enable-set-interning` configure option, `CACHE_TAG_SET_INTERNING` macro, Automake conditional, and build summary entry were removed.
- Core VTCs run in default/direct mode and generated copies add `interning = true`. Separate direct and interned 254/255-fold accounting tests remain explicit.
- Added mixed-mode ownership, mode-specific allocation failure, and direct-to-interned-to-direct reload VTCs.
- The first reload VTC parks old-VCL requests while modes change and objects expire, but it does not hold an object-event callback inside the unsubscribe/drain interval. The later acceptance-gap section records the bounded test-only callback handoff added to cover that interval exactly.
- Documentation now describes the immutable runtime option, its volatile-membership scope, default, and workload-dependent CPU/memory trade-off.

## Benchmark harness

- Candidate generation uses `BENCH_CODE_GENERATION=runtime` with `BENCH_RUNTIME_SET_INTERNING=0|1`; frozen legacy builds use `BENCH_CODE_GENERATION=legacy` with `BENCH_LEGACY_SET_INTERNING=0|1` and the old configure flag.
- Generated workload metadata distinguishes requested, rendered, and effective mode. Build provenance hashes the measured-path harness independently from the VMOD build source.
- A controlled phase protocol and fixed-pass warm driver were introduced for the future runtime decision campaign. No performance campaign or remote benchmark was run, and no performance conclusion is recorded here.
- Independent spec review found incomplete decision-campaign acceptance: the summariser did not consume both controlled phase artifacts, task coverage was not proven fail-closed, four-arm cache isolation/order was incomplete, and rendered mode was absent from some outputs. A focused corrective pass is in progress; any remainder must stay explicitly non-eligible for performance claims.

## Verification and review

- `git diff --check` passed before Docker verification.
- Diagnostic Docker targeted run: `cachetag_pm00039.vtc`, `cachetag_pm00040.vtc`, and `cachetag_pm00041.vtc`: 3/3 passed.
- Diagnostic Docker representation run: direct and interned `cachetag_pm00027` plus generated interned `cachetag_pm00017`: 3/3 passed.
- Full diagnostic Docker `check`: 112/112 passed, matching the updated runbook total.
- Independent standards review found the generated interned test-name inventory was manually duplicated despite a no-drift comment, and that this log was incomplete. The later corrective-verification section records both fixes and the final 119-test result. It found no ownership imbalance in the prepared-membership cleanup funnels and no host-verification violation.
- Independent spec review identified remaining callback-barrier, persistent reload, storage matrix, structural/fault matrix, and benchmark-controller gaps. Passing core tests are not treated as evidence that those acceptance items are complete.

## Corrective verification

- Replacing the duplicated interned test inventory with GNU `patsubst` failed during Docker bootstrap because Automake warnings are errors and the expression is non-POSIX. The list now uses POSIX suffix substitution and generates `_interned.vtc` siblings in the build tree. The seven newly generated structural/allocation-hook cases then passed 7/7.
- The Docker benchmark Python harness initially failed its embedded-script quoting test on three new single-quoted `sed`/`awk` expressions. Those expressions were converted to safe double-quoted `sed` and `cut` forms. The full Docker benchmark Python harness then passed.
- The final production-surface Docker `check` passed 86/86.
- The final diagnostic Docker `check` passed 119/119. The runbook totals now record 117 VTCs plus the WAL and counter-parity tests.
- Full Docker `distcheck` passed 119/119 and produced `libvmod-cachetag-1.0.2.tar.gz` inside the disposable container.
- The full Fellow attempt passed the Slash focused unit tests but stopped at the existing `cachetag_c00026.vtc`. Its parked request reached `vcl_deliver` after the release barrier listener had closed and received `Barrier connection failed: Connection refused`; Vinyl did not panic. A focused rerun with the correct source path reproduced the same VTC failure. An earlier attempted focused invocation used `vtc/cachetag_c00026.vtc` rather than `src/vtc/cachetag_c00026.vtc` and failed during test generation; it supplied no runtime evidence.
- A focused Fellow run containing the new mixed-mode `cachetag_pm00039.vtc` passed, as did the persistent and SIGKILL tail that the wrapper appends. The full 48-case Fellow matrix remains unconfirmed because of `cachetag_c00026.vtc`.
- No Buddy benchmark smoke or remote performance decision campaign was run. At this verification point the exact object-event callback barrier and persistent opposite-mode reload proof also remained open; the later acceptance-gap section records their focused coverage.
- Runtime R0 and R1 benchmark smoke runs completed through the Docker harness with 50 objects, two tags per object, one measured run, and one second of warm-up. R1 reused the R0 build and passed the source-identity check. These runs verify wiring only; the host did not expose `cpu_model` in the generated system metadata, and the results are not performance evidence.

## Acceptance-gap coverage

- No existing cachetag hook could hold an object-event callback. Request-side barriers are insufficient: an INSERT callback retains the busy VCL request, while an EXPIRE callback can overlap VCL cold independently. A diagnostic-only process-global handoff now lets the old namespace arm and observe a matching callback and a replacement namespace release it after the old VCL begins cooling.
- The object-event handoff is compiled only with `--enable-test-hooks`, accepts only INSERT or EXPIRE, permits one process-wide arm at a time, and self-releases after ten seconds. Cold clears an arm that never fired only after object-event unsubscription, so an aborted test cannot leave a dangling namespace pointer or an indefinite unsubscribe wait.
- The first focused Docker compile failed because `cachetag_now_usec()` is private to the index implementation. The hook now uses Vinyl's monotonic clock. A subsequent runtime attempt showed that compiling the replacement VCL while the callback was held consumed the ten-second safety timeout, and preloading with `-vcl+backend` activated the replacement too early. The final VTC precompiles an inactive `vcl2` with `vcl.inline`, then arms an EXPIRE callback in active `vcl1`, starts a delayed release request in `vcl2`, and proves `vcl.discard vcl1` drains the held callback before destruction.
- Focused diagnostic Docker verification of `cachetag_pm00042.vtc` passed 1/1.
- The Fellow persistence case stores a two-tag FDO record with direct mode, fully discards that VCL, opens the same namespace and WAL path with interning enabled, proves the new namespace sees the old FDO record, publishes and applies a hard purge, stores the replacement object, restarts Vinyl, replays the WAL, and serves that persisted replacement without a backend fetch. Fellow-direct objects remain absent from volatile and interned gauges throughout.
- Focused Fellow verification ran `fellow_cache_test_ndebug` and `cachetag_p00023.vtc`; both passed. This closes the persistent opposite-mode reload proof without claiming that interning changes Fellow-direct storage.

## Final integrated verification

- Repeated independent spec review drove the runtime decision harness to fail closed: `perf stat` attaches an explicit non-empty `-t` task list; task identity and coverage artifacts are internally reconciled; the remote controller requires distinct frozen legacy-direct, legacy-interned, and runtime-candidate sources; each arm builds once and later rows reuse its verified artifact; the exact eight-row order is bound to artifact matrix metadata; and D1/I1 qualification requires three eligible repetitions of both shapes within pre-frozen load/warm dispersion budgets. The full benchmark Python harness and static forwarding/wiring checks passed in Docker. No campaign was run.
- After adding `cachetag_pm00042.vtc`, the full diagnostic Docker `check` passed 120/120. The runbook now records 118 VTCs plus the WAL and counter-parity tests.
- Full Docker `distcheck` passed 120/120 and produced `libvmod-cachetag-1.0.2.tar.gz` inside the disposable container.
- The Fellow runbook total is now 49 after adding `cachetag_p00023.vtc`. The focused new persistence case passed, while the complete Fellow matrix remains unconfirmed because the pre-existing `cachetag_c00026.vtc` barrier failure still reproduces.
- Final independent standards review found one descriptive script-header comment that restated the code; it was removed. No other hard standards findings remained.
