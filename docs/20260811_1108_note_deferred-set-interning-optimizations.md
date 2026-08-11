# Deferred set-interning optimizations

## Decision

Leave the implementation at `b5f423f` unchanged for now.

The acknowledged fixed-work benchmark measured an interning cost of about 0.05% retired instructions per object for one shared five-tag set and 0.46–0.49% for one million unique five-tag sets. Lookup-first removed 85–87% of the former shared-set overhead and 21–24% of the unique-set overhead. The shared residual is too small to justify more hot-path complexity.

Unique sets remain the expensive case. They receive no deduplication benefit, but still require canonicalization, hashing, intern-table lookup, candidate allocation, publication and eventual table migration. Further work should target that case without giving back the shared-set improvement.

Benchmark evidence and validation details are in `docs/20260811_1059_report_set-interning-lookup-first-optimization.md`.

## Preferred future experiment: adaptive eager allocation

Lookup-first currently handles every small set in the same way:

1. Canonicalize and hash the folds.
2. Acquire `obj_mtx` and look for an existing set.
3. On a miss, release the mutex and allocate a candidate.
4. Reacquire the mutex, repeat the authoritative lookup and publish if the set is still absent.

This is the right sequence for a hit-heavy namespace because hits avoid allocation. An all-unique namespace pays for two mutex acquisitions and two lookups for nearly every object.

An adaptive policy could retain lookup-first while hits are common, then switch to eager candidate allocation after a sustained high miss rate. In eager mode, the candidate would be prepared before the first mutex acquisition, allowing a unique miss to complete with one locked lookup. The authoritative lookup must remain; the prediction may choose when to allocate, but must never decide set identity or publication.

The policy needs hysteresis so a namespace does not oscillate between modes. A change from unique traffic to shared traffic would temporarily allocate candidates that are discarded on hits, which is safe but could regress CPU until the predictor changes mode. Counters should expose the selected mode, transitions, eager allocations and eager candidates discarded on hits.

This is the best next experiment because it attacks work that unique sets pay on every attach and does not change the interning representation.

### Acceptance gate

Use the same acknowledged fixed-work shared and unique profiles, source freezing and B/P interleaving as the existing report. Compare the adaptive implementation with the current lookup-first implementation, not only with interning disabled.

Keep the change only if both paired unique comparisons improve by at least 0.1 percentage point of instructions per object, the sign clears the same-code floor, and shared overhead does not rise materially. Correctness runs must include traffic that changes from unique to shared and back, concurrent first publication of equal sets, allocation failure in each mode, migration in progress and mode changes around candidate allocation.

## Intern-table migration

Migration is another plausible source of unique-set cost. The table begins with 64 buckets and doubles as the set population grows. A million unique sets therefore require repeated table publication and bounded migration of existing entries.

Faster growth or pre-sizing would reduce this work, but spends memory before demand proves it is useful:

- Pre-sizing for one million unique sets would reserve roughly 8 MiB of bucket pointers at namespace startup. A shared-set namespace currently needs only 512 bytes of active table storage.
- A larger growth factor reduces migration frequency but can leave the active table much further above the current population. The worst-case unused bucket memory rises with that factor.
- An expected-set-count hint moves the decision to configuration, but a bad estimate either wastes memory or returns to the current growth path.

The exact one-million-set benchmark happens to end near a power-of-two boundary, so some larger growth factors could reach the same final table size in that specific run. That does not remove the general production trade-off: populations between growth boundaries can reserve substantially more memory.

Do not change table growth without a workload that measures both instructions saved and peak/steady bucket bytes across shared, mixed and unique populations. CPU improvement alone is insufficient.

## Lower-priority ideas

### Per-bucket change detection

After the first miss, record enough bucket state to detect whether that bucket changed while the candidate was allocated. If it did not change, the retry could avoid repeating the full comparison chain. This still requires the second mutex acquisition and adds versioning to publication, migration and deletion. It is less promising than avoiding the second acquisition through adaptive eager allocation.

### Set allocator

A size-class or slab allocator could reduce allocation overhead for common fold counts. The direct-vector build also allocates once per multi-tag object, so allocator cost is only part of the measured difference. Reclamation, mixed fold counts and concurrency would add substantial code. Profile the adaptive experiment before considering this.

### Runtime interning policy

If a namespace is known to contain overwhelmingly unique sets, disabling interning is the best CPU and memory result. Supporting that choice per namespace would require both membership representations in one build and a per-object representation marker. That is a larger design change than the compile-time switch and should be driven by a real mixed deployment requirement.

### Lock or hash-table redesign

Sharding the intern registry, splitting `obj_mtx`, or replacing the table structure could reduce contention at higher concurrency. The current result is a fixed-work cost below 0.5%, with no demonstrated contention problem in scope. These changes are not justified by the present evidence.

## When to reopen this work

Revisit optimization if production evidence shows that unique or mostly unique membership sets are common and their CPU cost matters, or if a higher-concurrency benchmark attributes a measurable bottleneck to intern lookup, migration or candidate publication. Start with an enabled-only profile or controlled compile-time variant that separates eager allocation from migration before changing the production path.
