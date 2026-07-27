### The question I asked Fable:

Are these testing and benchmarking problems we're having common (in which case why aren't they known and well documented, so we don't repeat them)? Or are we trying to be stricter in our testing than many developers are?

### Fable's reply

Both, honestly — the problems are common, but the level of rigor you're applying to them is not.

**The failure modes themselves are all well-known — in scattered, niche communities.**

- The procfs blocking trap (`smaps`/`smaps_rollup`/`cmdline`/`maps` taking the target's `mmap_lock`) is documented knowledge among kernel developers and authors of monitoring tools. It's why `smaps_rollup` exists at all (the full `smaps` walk was too expensive for Android's memory tooling), why Prometheus exporters make PSS collection opt-in, and why `ps`/`top` historically avoided anything that needs the target's memory map. But it lives in LWN articles, kernel commit messages, and code comments — places an application developer writing a benchmark wrapper in Python would never look. It's "well documented" only if you already know it exists.
- The methodology problems — A/A noise floors, frozen thresholds, order/position effects, survivorship bias from retries, governor/boost state — are extensively documented in academic performance-engineering literature (Mytkowicz et al.'s *"Producing Wrong Data Without Doing Anything Obviously Wrong"* is the canonical paper; Kalibera & Jones on rigorous benchmarking; SPEC run rules; the JMH documentation is essentially a long warning label). Your BR-024/BR-025/BR-002 rules are independently rediscovering established results.
- The "monitoring says active but captured almost nothing" failure is the classic silent-telemetry problem that SRE literature calls monitoring-the-monitoring. It's known — for production systems. Benchmark harnesses almost never get that treatment.

**Why they get repeated anyway:** three reinforcing reasons. First, the knowledge is fragmented across communities that rarely intersect — kernel internals, statistics-of-benchmarking, and SRE practice — and your incident sits exactly at the three-way intersection. Second, these failures are *silent*: the harness didn't crash, it produced a plausible-looking artifact with `status=active`. Most teams never discover they had the problem, so it never enters their postmortems or blog posts. Negative knowledge of this kind mostly lives in code comments of tools that already fixed it. Third, benchmark harnesses are culturally treated as throwaway scripts, not as measurement instruments that need their own validation — so the engineering effort that would surface these bugs is rarely spent.

**And yes, you are stricter than most developers.** The typical practice is: run the benchmark three times, eyeball the numbers, ship. Frozen pre-registered thresholds, A/A brackets, PID/start-time provenance, fail-loud voids, cohort invalidation on harness change, and retained rejected repetitions is a regime closer to SPEC submission rules or a serious vendor perf lab than to normal development. That strictness is proportionate here, though: you're defending a 15 ms tail gate and memory-containment claims where the effect sizes are small and one contaminated repetition genuinely changes the conclusion — and every BR rule traces to an incident that actually happened in this project.

The practical implication: don't expect an external canonical document to protect you — the BR-* corpus you're building *is* that document for this intersection, and the incidents it encodes (like this sampler one) are exactly the parts the general literature doesn't cover.

### I then asked:

So our use of Python, or our design of the testing harness isn't the problem, and there aren't existing libraries we should use to avoid the troubles we're having?

### And Fable replied:

The background agent finished a follow-up on the same thread (whether Python or the bespoke harness design is to blame, and whether an existing library would have prevented this). Relaying its findings:

**No — Python isn't the cause, the design flaw was real but generic, and no off-the-shelf library would have prevented it.**

- **Python isn't the problem.** A thread stuck in an uninterruptible procfs read that no signal can cancel is an OS-level property, not a language one. A Go, Rust, or C harness with the same single-thread design would stall identically — no language can cancel a thread blocked in a syscall, which is exactly why the fix must be a killable *process* boundary rather than a thread. Python adds two side constraints (fork-with-threads hazards, the GIL), but neither caused the incident and both are manageable in the fix design.
- **The design flaw is the one everyone makes:** mixing bounded reads (`/proc/stat`, `status`) and unbounded reads (`smaps_rollup`, `cmdline`, `maps`) on one cadence-critical thread. That's not a consequence of building bespoke — the blocking behavior is simply undocumented outside kernel circles, and mature monitoring tools converged on the same isolation patterns only after hitting the same wall.
- **psutil has the identical trap:** `memory_full_info()` reads `smaps_rollup` synchronously and `cmdline()` reads `/proc/pid/cmdline` synchronously, with no timeout or isolation machinery — its docs say "slower," not "can block until the target exits." The one thing it would have given you for free is PID-reuse protection: its `Process` identity is (pid, create_time), which is what your plan's item 5 now rebuilds by hand.
- **Prometheus-style exporters** avoid or gate per-process smaps for exactly this reason; their liveness answer is the scrape timeout — the same fail-loud pattern as BR-018 — but scrape-based monitoring can't give you 0.1 s cadence, per-run artifacts, or provenance gating. **Benchmark frameworks** (pyperf, hyperfine, JMH, Google Benchmark) solve statistical rigor at the command/function level; nothing off-the-shelf does cadence-gated system sampling with tracked-process provenance feeding frozen acceptance gates around a multi-process workload. Harnesses at that level (SPEC tooling, fio, vendor perf labs) are always bespoke.

**The one genuine "use existing infrastructure" opportunity it found:** lean harder on **cgroup v2 accounting**, which the harness already partially reads. Running the cache tree in a dedicated cgroup gives kernel-maintained `memory.current`/`memory.peak`/`memory.stat` from bounded reads — no `mmap_lock`, no sampling race, no missed short-lived process. Since Buddy's core promise is staying within a configured memory figure, cgroup accounting measures that claim more directly than sampled PSS does. It complements rather than replaces `smaps_rollup` (PSS shared-page semantics differ from cgroup charging, and BR-007 still needs the worker distinguished from the manager), but it could shrink the unbounded-read surface to a slow-interval, helper-isolated PSS read used only where PSS semantics genuinely matter.

That last point is worth folding into the liveness plan if you adopt it — a per-component cgroup for `cache-main` would reduce how much the detailed-memory helper has to carry.