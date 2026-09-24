# Fellow patches

These patches change Slash/Fellow for the cachetag VMOD and its test harness. Apply them in order to a `slash` checkout at:

```text
5c934c90d670f6cc98089ed6bcd2a2a2c26d1421 Vcacheize, clarify Varnish Support (not)
```

That Slash needs Vinyl 9.1 or trunk, as it uses the `VCACHE_*` autoconf macros.

Current patch stack:

- `0001-add-fellow-object-attr-provider.patch`
- `0002-fix-fellow-shutdown-drain-races.patch`
- `0003-fellow_logcache-bound-entries-by-scratch-capacity.patch`
- `0004-fellow_log-drain-close-time-free-regions-incremental.patch`
- `0005-fellow_logwatcher-stop-without-pre-close-rewrite.patch`
- `0006-fellow_log-avoid-close-time-rewrite-work.patch`
- `0007-fellow_log-write-close-active-block-synchronously.patch`
- `0008-fix-fellow-high-fanout-shutdown-reset.patch`
- `0009-suppress-fellow-logwatcher-rewrites-during-shutdown.patch`
- `0010-skip-fellow-logwatcher-close-flush-on-shutdown.patch`
- `0011-fellow-storage-skip-drain-idle-grace-on-shutdown.patch`
- `0012-fellow-storage-skip-global-shutdown-drain.patch`
- `0013-fellow-object-attr-add-read-failure-test-hook.patch`

## 0001: object attribute provider

Fellow's disk object header has four unused attribute slots, `va_reserve[0..3]`. This patch lets one outside user, such as cachetag, keep a blob of bytes in `va_reserve[0]`. Fellow stores the bytes but doesn't interpret them. cachetag's envelope format and its validation stay in the VMOD.

- When an object is created, the provider's `size` callback says how many bytes it needs, before Fellow allocates the disk object. After allocation, the `fill` callback writes them.
- `fellow_object_attr_visit()` hands a callback a pointer to the bytes without copying them. If the object is only on disk, Fellow first loads it through its normal read path, where concurrent requests for the same object share one read. The object stays in memory until the callback returns. The caller must hold a reference on the object. The return value says whether the object isn't a Fellow object, has no blob, or couldn't be loaded.
- Registering with a NULL stevedore applies the provider to every Fellow storage, including ones created later. Passing a stevedore limits it to that storage. Only one provider can be registered at a time.

The blob is covered by the disk object's checksum, and it survives slimming, eviction and reloading, and `obj_update`. An object has a blob when `va_reserve[0].alen` isn't zero, so the on-disk layout and version don't change. The patch doesn't add a DLE type, a log feature bit, a cachetag-specific ID, a resurrection callback, or a way for `fellow_busy_done()` to fail.

An overflow fix for the existing attribute bounds check in `fellow_disk_obj_check()` is proposed upstream separately, on the `fellow-disk-obj-check-overflow` branch. cachetag doesn't need it, so it isn't in this stack.

## 0002 to 0012: shutdown and log fixes

`0002` fixes races in how Fellow hands objects over during shutdown, seen when the child drains objects during a reset.

`0003` limits logcache entries at close to what the backing scratch space can hold, and reuses cached blocks before waiting for scratch memory.

`0004` drains free regions incrementally during the final FP_FINI close pass.

`0005` fixes the logwatcher shutdown handshake, so close no longer asks for an extra FP_OPEN rewrite before the FP_FINI close rewrite it already does.

`0006` stops the watcher from starting rewrites at stop time that could race with close, and lets a rewrite that is already running stop soon after shutdown starts. The close-time check of the persistent log allocator takes time proportional to the log size, so it now runs only in debug, extra-assertion and witness builds. Normal release builds skip it at shutdown.

`0007` writes the last active log block synchronously at close. Close waits for that write straight away, and in remote restart benchmarks the asynchronous write sometimes hadn't finished when the VTC stop timeout killed the child.

`0008` fixes the cachetag reset path under high fanout. It stops the Fellow cache LRU threads during storage warn-close, falls back to synchronous IO when the worker pool refuses a task (upstream `sfe_taskrun()` already passes the refusal back), waits longer for disk LRU idle polls to drain, and frees Fellow cache state before destroying the storage LRU.

`0009` marks the logwatcher as shutting down during storage warn-close. It keeps flushing logbuffer work but starts no new background rewrites while the child is stopping.

`0010` stops the watcher thread from starting the final `LBUF_FLUSH_CLOSE` after that shutdown mark. It drops the logbuffer state still in memory, but still waits for flushes and IO that were already submitted to finish cleaning up. Without this, a new close-time drain of the active block and header could make the child take longer to stop.

`0011` keeps the Fellow disk drain pass during shutdown, but skips the usual idle grace period once shutdown has marked the storage as refusing new allocations.

`0012` skips that disk LRU drain for global Fellow storage at process shutdown. After joining background cache work, it leaves the Vinyl/Fellow cache structures and the buddy memory behind them allocated instead of freeing them. Fellow storage defined in VCL still drains and goes through the normal cleanup with its assertions, because the child keeps running.

## 0013: test hook for read failures

`0013` adds `fellow_object_attr_test_fail_next_visit()`, which makes the next `fellow_object_attr_visit()` call fail. It only exists in Slash builds with `DEBUG`, such as `libvmod_slashwitness.so`, so normal builds are unchanged. A VTC has no reliable way to make Fellow fail a real read, which takes a disk error or a corrupt object. `cachetag_p00018.vtc` uses the hook to check that when cachetag can't read an object's tags after a restart, it treats the object as stale, so a purged object isn't served. The Fellow harness passes the witness build to that test as `${libvmod_slashwitness}`.

## Upstream

`0001` and `0013` are proposed upstream as Slash merge request 30, from the `cachetag-integration` branch of the Slash fork.
