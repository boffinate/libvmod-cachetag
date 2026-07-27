# Fellow patches

This directory carries the Slash/Fellow changes needed by the Vinyl cachetag
VMOD and its test harness.

Apply these patches to the `slash` checkout based on:

```text
7be4126892dbc58a03f701632e076f312e0332ed Vinylize and adjustments to 9.0
```

Current patch stack:

- `0002-add-fellow-object-attr-provider.patch`
- `0003-fix-fellow-shutdown-drain-races.patch`
- `0004-fellow_logcache-bound-entries-by-scratch-capacity.patch`
- `0005-fellow_log-drain-close-time-free-regions-incremental.patch`
- `0006-fellow_logwatcher-stop-without-pre-close-rewrite.patch`
- `0007-fellow_log-avoid-close-time-rewrite-work.patch`
- `0008-fellow_log-write-close-active-block-synchronously.patch`
- `0009-fix-fellow-high-fanout-shutdown-reset.patch`
- `0010-suppress-fellow-logwatcher-rewrites-during-shutdown.patch`
- `0011-skip-fellow-logwatcher-close-flush-on-shutdown.patch`
- `0012-fellow-storage-skip-drain-idle-grace-on-shutdown.patch`
- `0013-fellow-storage-skip-global-shutdown-drain.patch`
- `0014-fellow-object-attr-add-read-failure-test-hook.patch`

`0002` adds one narrowly generic opaque object-attribute provider backed by
`fellow_disk_obj.va_reserve[0]`. A provider returns the exact byte count before
FDO allocation and fills that reserved range synchronously after allocation.
The no-copy visitor materializes vampire objects through Fellow's existing
coalesced FDO read path, keeps the FCO alive for the callback duration, and
distinguishes non-Fellow objects, absent attributes, and materialization errors.
Registration with a null stevedore installs the single provider for every
current or future Fellow instance; an optional non-null stevedore filters it to
one instance.

The attribute is covered by the normal FDO checksum and survives slimming,
eviction/re-read, and `obj_update`. The patch does not add a DLE type, log
feature bit, cachetag consumer id, resurrection callback, or fallible
`fellow_busy_done()` path. Attr presence is `va_reserve[0].alen != 0`; no FDO
layout or version change is needed.

`0003` fixes Fellow shutdown object handoff races seen when the child drains
objects during reset. `0004` bounds close-time logcache entries by backing
scratch capacity and reuses cached blocks before blocking on scratch memory.
`0005` drains free regions incrementally during the final FP_FINI close pass.
`0006` fixes the Fellow logwatcher shutdown handshake so close does not request
an additional FP_OPEN rewrite before the existing FP_FINI close rewrite. `0007`
prevents stop-time watcher rewrites that can race close, lets an in-progress
rewrite abort promptly once shutdown starts, keeps the close-time persistent log
allocator proof in debug, extra-assertion, and witness builds, and skips that
O(log size) proof during normal release shutdown. `0008` makes the final
close-time active log block write synchronous, because close immediately waits
for that write and remote restart benchmarks showed the async completion could
remain outstanding until the VTC stop timeout killed the child. `0009` fixes
the high-fanout cachetag reset path by stopping Fellow cache LRU threads during
storage warn-close, handling worker-pool task refusal with synchronous IO
fallbacks, draining disk LRU idle polls for longer, and freeing Fellow cache
state before the storage LRU is destroyed. `0010` marks the logwatcher as shutting down during storage warn-close, so it continues flushing logbuffer work but suppresses new background rewrites while the child is trying to stop.
`0011` avoids starting the final watcher-thread `LBUF_FLUSH_CLOSE` after that warn-close shutdown mark; it abandons remaining in-memory logbuffer state while still waiting for already submitted flush/IO ownership cleanup, preventing a new close-time active-block/header drain from extending child stop. `0012` keeps the Fellow disk drain pass during shutdown but skips the normal idle grace once shutdown has already marked the storage as rejecting new allocations. `0013` skips that disk LRU drain for global Fellow storage during process shutdown and abandons the still-populated live Vinyl/Fellow cache structures and backing memory buddy after joining background cache work; VCL-defined Fellow storage still drains and uses the normal asserting cleanup path because the child keeps running.
`0014` adds a narrow one-shot Fellow object-attribute visit failure hook for VMOD/VTC fail-closed tests. It does not change normal object-attribute storage, materialization, or visitor semantics.
Current patch shape:

- open `0002` as a generic Fellow object-attribute provider PR, based on
  `7be4126892dbc58a03f701632e076f312e0332ed`;
- keep cachetag envelope format and validation entirely in the VMOD;
- preserve the attribute through normal FDO persistence and materialization.

See `libvmod-cachetag/devdocs/docs/archived/2026-07-10-purgemap-fdo-direct-fellow-plan.md`.
