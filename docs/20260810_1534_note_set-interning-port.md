# Set-interning port

**Date:** 2026-08-10
**Task:** Port the private membership-set interning prototype onto rewritten `main` and expose it through `--enable-set-interning`.

## Context

The private `feature/set-interning` branch has no merge base with current `main`, so rebasing its long history would replay obsolete pre-rewrite work. Its original tip remains available as `archive/private-set-interning-prototype`; the new `feature/set-interning` starts from `main` commit `4fe0e48` and ports the final prototype change only.

## Implementation

- A default build retains the existing direct per-object overflow-fold vector representation.
- `--enable-set-interning` sorts and hashes multi-fold memberships, then shares an immutable refcounted set among equal memberships within one namespace index. Single-fold memberships remain inline.
- VSC counters report the enabled registry's set, reference, hit, miss, table, and allocation-byte totals. They remain present and zero in disabled builds so the two configurations are comparable.
- The test-only intern-allocation failure injector is exposed only when both `--enable-set-interning` and `--enable-test-hooks` are configured.
- VTC selection tests each representation's expected accounting, and the default Docker harness enables the experimental option. Passing `CACHE_TAG_CONFIGURE_ARGS=""` continues to exercise the production surface.

## Verification

- `scripts/test-with-vinyl-cache.sh ../vinyl-cache` completed successfully with demo diagnostics, test hooks, and set interning enabled. Both the outer build and the `distcheck` rebuild compiled the generated interfaces and VMOD sources.
- `CACHE_TAG_CHECK_TARGET=check CACHE_TAG_CONFIGURE_ARGS='' scripts/test-with-vinyl-cache.sh ../vinyl-cache` completed successfully with set interning disabled.
- `scripts/test-fellow-with-vinyl-cache.sh ../vinyl-cache` completed successfully with the enabled configuration.
- `git diff --check` completed without whitespace errors.

## Benchmark caveat

No performance conclusion follows from this port. The enabled registry performs sort and hash work for every multi-fold attach. Benchmark resident memory, attach latency, purge/sweep latency, lock contention, and distinct-versus-repeated membership distributions before considering a production default.

## Outside-lock allocation and incremental migration

The follow-up implementation in `20260810_1900_plan_set-interning-outside-obj-mtx.md` changes each multi-fold allocation into the complete unpublished intern candidate. Fold population, sorting, hashing, candidate allocation, and bucket-array allocation happen before `obj_mtx`. A locked publish transaction revalidates object and side-map capacity, searches the active and old tables, and either publishes the candidate or hands a losing candidate to caller-local outside-lock cleanup.

Growth now publishes an empty active table while retaining the previous table as old. Request paths advance at most four migration steps and the existing resize worker advances bounded batches until the old table is empty, including when `sweep_interval = 0s`. Each step examines one empty bucket or relinks one set, so growth no longer allocates, frees, or walks the whole registry under `obj_mtx`. Active plus old bucket bytes remain charged throughout migration; detached set and table bytes remain charged until their caller-owned outside-lock free completes.

Initial table allocation failure still fails an attachment closed. A later growth-allocation failure increments the grow-failure diagnostic and publishes the candidate into the existing table, allowing chains to grow until a later attach can retry growth. Namespace detach and delete detach both table inventories under the mutex and traverse/free them only after unlocking.
