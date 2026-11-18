<!-- Copyright (c) 2026 Peter Bowyer -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

# VMOD cachetag

`cachetag` provides explicit cache-tag registration and generational invalidation for Varnish Cache 9.1 and Vinyl Cache 9.1. A successful purge appends to durable purge history (when configured); it does not walk objects or report an affected object count.

## Namespace Object

Create a namespace in `vcl_init`:

```vcl
sub vcl_init {
    new tags = cachetag.namespace("default");
}
```

The constructor accepts a namespace name, `max_keys_per_object`, `max_key_length`, and `max_tag_header_bytes`. `sweep_interval` controls the volatile sweeper; `purge_history_max_entries` caps retained purge history by advancing hard and soft purge floors. `persist_path` enables the purge WAL and Fellow FDO attributes. `wal_fsync` selects `strict` or `grouped` (currently an alias for `strict`), and `wal_segment_bytes` controls WAL file rotation.

## Tag Registration

`VOID .add(STRING key)` registers a single tag for the object being fetched. `VOID .add_header(STRING header, STRING sep = ",")` parses a delimited header: it trims whitespace, rejects tags containing whitespace, and enforces configured limits. Both methods are available only in `vcl_backend_response` and `vcl_backend_error`.

Registration records a snapshot of the current purge sequence. Purges completed before a tag is registered do not affect that object; purges published after registration are caught at insertion and during `stale()`.

## Purge

`INT .purge(STRING key, ENUM { hard, soft } mode = hard)` and `INT .purge_header(STRING header, STRING sep = ",", ENUM { hard, soft } mode = hard)` publish hard or soft purges.

Return values:

- `-1`: Success. Purge recorded in history.
- `-2`: Rejected (resource exhausted or configured limit reached).
- `-3`: Invalid input. `purge_header()` validates the entire header before recording anything.
- `-4`: Persistence unavailable or WAL append failed. Failed purges do not advance the purge sequence seen by new registrations.

## Read-Path Validation

`BOOL .stale()` returns true when the current hit or delivery object has a tag whose hard purge sequence is newer than the object's registration sequence. It also retires the object so `return (restart)` can fetch fresh content. Soft purges shorten expiry once to grace, but leave `stale()` returning false.

Volatile objects use in-memory membership tables. Persistent Fellow objects store their tags directly in checksummed FDO attributes and are checked on hit or delivery. Missing, invalid, unsupported, or unreadable Fellow metadata fails closed (treated as stale). A valid envelope that does not mention the queried namespace means the object has no tags in that namespace and remains fresh.

## Observability

`pending()` reports unconsumed registrations for the current fetch. `objects()` and `edges()` track volatile membership only; Fellow objects do not appear in these gauges. `compact()` trims purge history past configured caps, rebuilds tombstone-heavy purge maps, runs an immediate volatile sweep, and triggers deferred table shrinking. Its return value reports sweep work completed; hash tables and container allocations shrink asynchronously after a short observation window so immediate refills can cancel or roll back a shrink.

VSC counters report volatile membership, purge-map size/floors/sequence/probes, sweeps, parse and limit failures, stale checks, WAL health, and Fellow direct probes. Obsolete epoch and posting counters have been removed, not aliased. The `info` counter `transient_discards` counts inserted hit-for-miss or hit-for-pass objects whose pending tags were dropped. Notices are disabled by default. Set `log_transient_discards = true` on the namespace to log the discarded tag count and object kind on affected backend transactions for diagnosis; the counter stays active regardless of this setting.

## Diagnostic Build Flags

A standard build exposes only the API documented above. Two configure flags add diagnostic methods (neither should be enabled in production):

`--enable-demo-diagnostics` adds read-only inspection methods: `generation()` returns the latest retained purge sequence for a tag, `purge_seq()` returns the namespace's global purge sequence counter, and `purgemap_entries()`, `purgemap_slots()`, and `purgemap_bytes()` report purge-map entry count, hash-table slot count, and allocated hash-table bytes.

`--enable-test-hooks` exposes `test_*()` methods: fault injection hooks and internal state toggles for the regression test suite.

Because the VCC interface is generated from source fragments at build time, methods gated behind disabled flags do not exist in the VMOD binary; any VCL referencing them will fail to compile.
```
