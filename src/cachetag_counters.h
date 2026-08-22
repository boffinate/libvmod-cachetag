/*-
 * SPDX-License-Identifier: MPL-2.0
 *
 * The cachetag counter surface, in one place.
 *
 * Every row here has an exactly matching entry in src/cachetag.vsc, in the
 * same order, with the same type, level and oneliner.  src/cachetag.vsc
 * remains the input to vsctool; this table is what the C side is generated
 * from.  cachetag_counter_parity.sh fails the build if the two drift.
 *
 * CT(name, type, level, oneliner)
 *   name      C identifier and VSC counter name
 *   type      bare token: counter | gauge   (stringified for the parity dump)
 *   level     bare token: info | diag | debug
 *   oneliner  string literal, byte-identical to the :oneliner: in cachetag.vsc
 *
 * This file holds the table and nothing else: no includes, no declarations.
 */
#define CACHETAG_COUNTERS(CT)                                                 \
	CT(index_memory_bytes, gauge, info,                                   \
	    "Total VMOD index memory bytes")                                  \
	CT(volatile_side_table_bytes, gauge, info,                            \
	    "Volatile membership side-table bytes")                           \
	CT(volatile_side_table_buckets, gauge, diag,                          \
	    "Volatile membership side-table buckets")                         \
	CT(volatile_side_table_grows, counter, debug,                         \
	    "Volatile membership side-table growth operations")               \
	CT(volatile_side_table_shrinks, counter, debug,                       \
	    "Volatile membership side-table shrink operations")               \
	CT(volatile_object_table_bytes, gauge, info,                          \
	    "Volatile membership object-vector bytes")                        \
	CT(volatile_object_count_sidecar_bytes, gauge, diag,                  \
	    "Volatile membership object-count sidecar bytes")                 \
	CT(volatile_object_count_overflow_bytes, gauge, diag,                 \
	    "Live overflow membership metadata bytes")                        \
	CT(volatile_interned_sets, gauge, diag,                               \
	    "Live unique interned membership sets")                           \
	CT(volatile_interned_set_refs, gauge, diag,                           \
	    "Live object references to interned membership sets")             \
	CT(volatile_interned_set_hits, counter, diag,                         \
	    "Attaches reusing an interned membership set")                    \
	CT(volatile_interned_set_misses, counter, diag,                       \
	    "Attaches creating an interned membership set")                   \
	CT(volatile_interned_set_bytes, gauge, diag,                          \
	    "Interned membership-set node bytes")                             \
	CT(volatile_interned_table_bytes, gauge, diag,                        \
	    "Interning hash-table bucket-array bytes")                        \
	CT(volatile_interned_acquire_calls, counter, debug,                   \
	    "Instrumented interned-set acquisitions while holding obj_mtx")   \
	CT(volatile_interned_acquire_usec, counter, debug,                    \
	    "Instrumented interned-set acquisition microseconds while holding obj_mtx") \
	CT(volatile_interned_acquire_max_usec, gauge, debug,                  \
	    "Maximum instrumented interned-set acquisition time while holding obj_mtx") \
	CT(volatile_interned_acquire_over_50us, counter, debug,               \
	    "Instrumented interned-set acquisitions above 50 microseconds")   \
	CT(volatile_interned_acquire_over_250us, counter, debug,              \
	    "Instrumented interned-set acquisitions above 250 microseconds")  \
	CT(volatile_interned_acquire_over_1ms, counter, debug,                \
	    "Instrumented interned-set acquisitions above 1 millisecond")     \
	CT(volatile_interned_acquire_over_10ms, counter, debug,               \
	    "Instrumented interned-set acquisitions above 10 milliseconds")   \
	CT(volatile_interned_table_grow_calls, counter, debug,                \
	    "Instrumented bounded interned-table publication and migration calls while holding obj_mtx") \
	CT(volatile_interned_table_grow_usec, counter, debug,                 \
	    "Instrumented bounded interned-table publication and migration microseconds while holding obj_mtx") \
	CT(volatile_interned_table_grow_max_usec, gauge, debug,               \
	    "Maximum bounded interned-table publication and migration time while holding obj_mtx") \
	CT(volatile_interned_candidate_alloc_calls, counter, debug,           \
	    "Prepared intern-candidate allocations outside obj_mtx")          \
	CT(volatile_interned_candidate_alloc_usec, counter, debug,            \
	    "Prepared intern-candidate allocation microseconds outside obj_mtx") \
	CT(volatile_interned_candidate_alloc_max_usec, gauge, debug,          \
	    "Maximum prepared intern-candidate allocation time outside obj_mtx") \
	CT(volatile_interned_table_alloc_calls, counter, debug,               \
	    "Intern-table bucket allocations outside obj_mtx")                \
	CT(volatile_interned_table_alloc_usec, counter, debug,                \
	    "Intern-table bucket allocation microseconds outside obj_mtx")    \
	CT(volatile_interned_table_alloc_max_usec, gauge, debug,              \
	    "Maximum intern-table bucket allocation time outside obj_mtx")    \
	CT(volatile_interned_migration_active, gauge, diag,                   \
	    "Intern-table migration active")                                  \
	CT(volatile_interned_old_table_bytes, gauge, debug,                   \
	    "Retiring intern-table bucket-array bytes")                       \
	CT(volatile_interned_detached_set_bytes, gauge, debug,                \
	    "Detached interned-set bytes awaiting outside-lock free")         \
	CT(volatile_interned_detached_table_bytes, gauge, debug,              \
	    "Detached intern-table bytes awaiting outside-lock free")         \
	CT(volatile_interned_table_alloc_failures, counter, debug,            \
	    "Intern-table bucket allocation failures")                        \
	CT(volatile_interned_table_grow_failures, counter, debug,             \
	    "Intern-table growth allocation failures retained by the active table") \
	CT(volatile_interned_candidate_discards, counter, debug,              \
	    "Unpublished intern candidates discarded outside the registry")   \
	CT(volatile_object_table_slots, gauge, diag,                          \
	    "Volatile membership object-vector slots")                        \
	CT(volatile_object_table_shrinks, counter, debug,                     \
	    "Volatile membership object-vector shrink operations")            \
	CT(volatile_objects, gauge, info,                                     \
	    "Objects with volatile purgemap membership")                      \
	CT(volatile_edges, gauge, info,                                       \
	    "Fold edges in volatile purgemap membership")                     \
	CT(volatile_inline_folds, gauge, diag,                                \
	    "One-fold memberships stored inline")                             \
	CT(volatile_attached, counter, info,                                  \
	    "Volatile membership attach records accepted")                    \
	CT(volatile_attach_failures, counter, info,                           \
	    "Volatile membership attach failures")                            \
	CT(request_probe_obj_mtx_calls, counter, debug,                       \
	    "Instrumented request probe obj_mtx acquisitions")                \
	CT(request_probe_obj_mtx_wait_usec, counter, debug,                   \
	    "Instrumented request probe obj_mtx wait microseconds")           \
	CT(request_probe_obj_mtx_wait_max_usec, gauge, debug,                 \
	    "Maximum instrumented request probe obj_mtx wait")                \
	CT(request_attach_obj_mtx_calls, counter, debug,                      \
	    "Instrumented request attach obj_mtx acquisitions")               \
	CT(request_attach_obj_mtx_wait_usec, counter, debug,                  \
	    "Instrumented request attach obj_mtx wait microseconds")          \
	CT(request_attach_obj_mtx_wait_max_usec, gauge, debug,                \
	    "Maximum instrumented request attach obj_mtx wait")               \
	CT(request_invalidate_obj_mtx_calls, counter, debug,                  \
	    "Instrumented invalidation obj_mtx acquisitions")                 \
	CT(request_invalidate_obj_mtx_wait_usec, counter, debug,              \
	    "Instrumented invalidation obj_mtx wait microseconds")            \
	CT(request_invalidate_obj_mtx_wait_max_usec, gauge, debug,            \
	    "Maximum instrumented invalidation obj_mtx wait")                 \
	CT(object_grow_calls, counter, debug,                                 \
	    "Dense object-vector growth attempts")                            \
	CT(object_grow_usec, counter, debug,                                  \
	    "Dense object-vector growth microseconds")                        \
	CT(object_grow_max_usec, gauge, debug,                                \
	    "Maximum dense growth microseconds")                              \
	CT(object_grow_failures, counter, debug,                              \
	    "Dense object-vector growth failures")                            \
	CT(object_grow_compact_active_calls, counter, debug,                  \
	    "Dense growths during certified compact traversal")               \
	CT(object_shrink_calls, counter, debug,                               \
	    "Dense object-vector shrink attempts")                            \
	CT(object_shrink_usec, counter, debug,                                \
	    "Dense object-vector shrink microseconds")                        \
	CT(object_shrink_max_usec, gauge, debug,                              \
	    "Maximum dense shrink microseconds")                              \
	CT(object_shrink_failures, counter, debug,                            \
	    "Dense object-vector shrink failures")                            \
	CT(object_shrink_compact_active_calls, counter, debug,                \
	    "Dense shrinks during certified compact traversal")               \
	CT(side_grow_rehash_calls, counter, debug,                            \
	    "Side-map growth or same-size rehash attempts")                   \
	CT(side_grow_rehash_usec, counter, debug,                             \
	    "Side growth rehash microseconds")                                \
	CT(side_grow_rehash_max_usec, gauge, debug,                           \
	    "Maximum side growth rehash microseconds")                        \
	CT(side_grow_rehash_failures, counter, debug,                         \
	    "Side growth rehash failures")                                    \
	CT(side_grow_rehash_compact_active_calls, counter, debug,             \
	    "Side growth rehashes during certified compact")                  \
	CT(side_shrink_rehash_calls, counter, debug,                          \
	    "Side-map shrink rehash attempts")                                \
	CT(side_shrink_rehash_usec, counter, debug,                           \
	    "Side shrink rehash microseconds")                                \
	CT(side_shrink_rehash_max_usec, gauge, debug,                         \
	    "Maximum side shrink rehash microseconds")                        \
	CT(side_shrink_rehash_failures, counter, debug,                       \
	    "Side shrink rehash failures")                                    \
	CT(side_shrink_rehash_compact_active_calls, counter, debug,           \
	    "Side shrink rehashes during certified compact")                  \
	CT(zero_container_free_calls, counter, debug,                         \
	    "Zero-object container free operations")                          \
	CT(zero_container_free_usec, counter, debug,                          \
	    "Zero-object container free microseconds")                        \
	CT(zero_container_free_max_usec, gauge, debug,                        \
	    "Maximum zero-object free microseconds")                          \
	CT(zero_container_free_failures, counter, debug,                      \
	    "Zero-object container free failures")                            \
	CT(zero_container_free_compact_active_calls, counter, debug,          \
	    "Zero-object frees during certified compact")                     \
	CT(record_shrink_calls, counter, debug,                               \
	    "Volatile record shrink calls")                                   \
	CT(record_shrink_obj_mtx_wait_usec, counter, debug,                   \
	    "Volatile record shrink obj_mtx wait microseconds")               \
	CT(record_shrink_obj_mtx_wait_max_usec, gauge, debug,                 \
	    "Maximum volatile record shrink obj_mtx wait")                    \
	CT(record_shrink_obj_mtx_hold_usec, counter, debug,                   \
	    "Volatile record shrink obj_mtx hold microseconds")               \
	CT(record_shrink_obj_mtx_hold_max_usec, gauge, debug,                 \
	    "Maximum volatile record shrink obj_mtx hold")                    \
	CT(record_shrink_obj_mtx_hold_last_usec, gauge, debug,                \
	    "Last volatile record shrink obj_mtx hold")                       \
	CT(object_segments, gauge, diag,                                      \
	    "Published object segment count")                                 \
	CT(object_published_slots, gauge, diag,                               \
	    "Published object slots")                                         \
	CT(object_published_bytes, gauge, diag,                               \
	    "Published object-entry segment bytes")                           \
	CT(object_count_published_bytes, gauge, diag,                         \
	    "Published object-count sidecar bytes")                           \
	CT(object_segment_grow_publishes, counter, debug,                     \
	    "Object segment growth publications")                             \
	CT(object_emergency_segment_allocations, counter, debug,              \
	    "Request-side emergency object segment allocations")              \
	CT(object_emergency_segment_old_capacity_max, gauge, debug,           \
	    "Largest old object capacity at an emergency segment allocation") \
	CT(object_segment_detach_batches, counter, debug,                     \
	    "Object segment detach batches")                                  \
	CT(object_segment_alloc_usec, counter, debug,                         \
	    "Object segment allocation microseconds outside obj_mtx")         \
	CT(object_segment_alloc_max_usec, gauge, debug,                       \
	    "Maximum object segment allocation microseconds")                 \
	CT(object_segment_alloc_last_usec, gauge, debug,                      \
	    "Last object segment allocation microseconds")                    \
	CT(object_segment_alloc_failures, counter, debug,                     \
	    "Object segment allocation failures")                             \
	CT(object_segment_free_usec, counter, debug,                          \
	    "Object segment free microseconds outside obj_mtx")               \
	CT(object_segment_free_max_usec, gauge, debug,                        \
	    "Maximum object segment free microseconds")                       \
	CT(object_segment_free_last_usec, gauge, debug,                       \
	    "Last object segment free microseconds")                          \
	CT(side_primary_buckets, gauge, diag,                                 \
	    "Primary side table buckets")                                     \
	CT(side_primary_bytes, gauge, diag,                                   \
	    "Primary side table bytes")                                       \
	CT(side_primary_live, gauge, debug,                                   \
	    "Primary side table live entries")                                \
	CT(side_primary_tombstones, gauge, debug,                             \
	    "Primary side table tombstones")                                  \
	CT(side_retiring_buckets, gauge, diag,                                \
	    "Retiring side table buckets")                                    \
	CT(side_retiring_bytes, gauge, diag,                                  \
	    "Retiring side table bytes")                                      \
	CT(side_retiring_live, gauge, debug,                                  \
	    "Retiring side table live entries")                               \
	CT(side_retiring_tombstones, gauge, debug,                            \
	    "Retiring side table tombstones")                                 \
	CT(side_resize_state, gauge, diag,                                    \
	    "Resize state 0 idle 1 migrate 2 observe 3 ready")                \
	CT(side_resize_reason, gauge, diag,                                   \
	    "Resize reason 0 none 1 grow 2 rebuild 3 shrink 4 rollback")      \
	CT(side_migration_buckets_remaining, gauge, debug,                    \
	    "Side migration buckets remaining")                               \
	CT(side_migration_live_remaining, gauge, debug,                       \
	    "Side migration live entries remaining")                          \
	CT(side_migration_batches, counter, debug,                            \
	    "Side migration batches")                                         \
	CT(side_migration_inspected_buckets, counter, debug,                  \
	    "Side migration inspected buckets")                               \
	CT(side_migration_moved_entries, counter, debug,                      \
	    "Side migration moved entries")                                   \
	CT(side_migration_completions, counter, debug,                        \
	    "Side migration completions")                                     \
	CT(side_destination_alloc_usec, counter, debug,                       \
	    "Side destination allocation microseconds outside obj_mtx")       \
	CT(side_destination_alloc_max_usec, gauge, debug,                     \
	    "Maximum side destination allocation microseconds")               \
	CT(side_destination_alloc_last_usec, gauge, debug,                    \
	    "Last side destination allocation microseconds")                  \
	CT(side_destination_alloc_failures, counter, debug,                   \
	    "Side destination allocation failures")                           \
	CT(side_retired_free_usec, counter, debug,                            \
	    "Retired side table free microseconds outside obj_mtx")           \
	CT(side_retired_free_max_usec, gauge, debug,                          \
	    "Maximum retired side table free microseconds")                   \
	CT(side_retired_free_last_usec, gauge, debug,                         \
	    "Last retired side table free microseconds")                      \
	CT(side_resize_grow_publishes, counter, debug,                        \
	    "Side resize grow publications")                                  \
	CT(side_resize_attach_grow_publishes, counter, debug,                 \
	    "Attach-path side resize grow publications, excluding lazy allocation") \
	CT(side_resize_attach_grow_old_buckets_max, gauge, debug,             \
	    "Largest old side bucket count at an attach-path grow publication") \
	CT(side_resize_rebuild_publishes, counter, debug,                     \
	    "Side resize same-size rebuild publications")                     \
	CT(side_resize_shrink_publishes, counter, debug,                      \
	    "Side resize shrink publications")                                \
	CT(side_resize_shrink_cancellations, counter, debug,                  \
	    "Side shrink cancellations")                                      \
	CT(side_resize_shrink_rollbacks, counter, debug,                      \
	    "Side shrink rollbacks")                                          \
	CT(resize_batch_obj_mtx_wait_usec, counter, debug,                    \
	    "Resize batch obj_mtx wait microseconds")                         \
	CT(resize_batch_obj_mtx_wait_max_usec, gauge, debug,                  \
	    "Maximum resize batch obj_mtx wait")                              \
	CT(resize_batch_obj_mtx_wait_last_usec, gauge, debug,                 \
	    "Last resize batch obj_mtx wait")                                 \
	CT(resize_batch_obj_mtx_hold_usec, counter, debug,                    \
	    "Resize batch obj_mtx hold microseconds")                         \
	CT(resize_batch_obj_mtx_hold_max_usec, gauge, debug,                  \
	    "Maximum resize batch obj_mtx hold")                              \
	CT(resize_batch_obj_mtx_hold_last_usec, gauge, debug,                 \
	    "Last resize batch obj_mtx hold")                                 \
	CT(resize_batch_obj_mtx_hold_over_2ms, counter, debug,                \
	    "Resize batch obj_mtx holds above 2ms")                           \
	CT(resize_batch_obj_mtx_hold_over_5ms, counter, debug,                \
	    "Resize batch obj_mtx holds above 5ms")                           \
	CT(resize_batch_obj_mtx_hold_over_10ms, counter, debug,               \
	    "Resize batch obj_mtx holds above 10ms")                          \
	CT(resize_low_water_active, gauge, diag,                              \
	    "Low-water observation active")                                   \
	CT(resize_low_water_starts, counter, debug,                           \
	    "Low-water observation starts")                                   \
	CT(resize_low_water_restarts, counter, debug,                         \
	    "Low-water observation restarts at lower live count")             \
	CT(resize_low_water_rearms, counter, debug,                           \
	    "Low-water observations rearmed after refill cancellation")       \
	CT(resize_low_water_elapsed_usec, gauge, debug,                       \
	    "Current low-water observation elapsed microseconds")             \
	CT(resize_low_water_observed_live, gauge, debug,                      \
	    "Low-water observed live objects")                                \
	CT(resize_low_water_target_objects, gauge, debug,                     \
	    "Low-water target object slots")                                  \
	CT(resize_low_water_target_side_buckets, gauge, debug,                \
	    "Low-water target side buckets")                                  \
	CT(resize_low_water_cancellations, counter, debug,                    \
	    "Low-water observation cancellations")                            \
	CT(resize_active_bytes, gauge, diag,                                  \
	    "Active object and primary side bytes")                           \
	CT(resize_retiring_bytes, gauge, diag,                                \
	    "Retiring side bytes")                                            \
	CT(resize_detached_bytes, gauge, diag,                                \
	    "Detached resize bytes not yet freed")                            \
	CT(resize_reconciled_bytes, gauge, diag,                              \
	    "Active plus retiring plus detached resize bytes")                \
	CT(parse_errors, counter, info,                                       \
	    "Tag and header parse errors")                                    \
	CT(limit_rejections, counter, info,                                   \
	    "Configured limit rejections")                                    \
	CT(stale_calls, counter, info,                                        \
	    "stale() calls")                                                  \
	CT(stale_detected, counter, info,                                     \
	    "stale() positive results")                                       \
	CT(purgemap_entries, gauge, info,                                     \
	    "Live purge-map entries")                                         \
	CT(purgemap_table_slots, gauge, diag,                                 \
	    "Purge-map table slots")                                          \
	CT(purgemap_tombstones, gauge, diag,                                  \
	    "Purge-map tombstone slots")                                      \
	CT(purgemap_empty_slots, gauge, diag,                                 \
	    "Purge-map empty slots")                                          \
	CT(purgemap_bytes, gauge, info,                                       \
	    "Purge-map memory bytes")                                         \
	CT(purgemap_hard_floor, gauge, diag,                                  \
	    "Hard-purge history floor")                                       \
	CT(purgemap_soft_floor, gauge, diag,                                  \
	    "Soft-purge history floor")                                       \
	CT(purgemap_seq, gauge, info,                                         \
	    "Current purge registration sequence")                            \
	CT(purgemap_prunes, counter, info,                                    \
	    "Purge-map prune operations")                                     \
	CT(purgemap_pruned_entries, counter, info,                            \
	    "Purge-map entries pruned")                                       \
	CT(purgemap_rebuilds_grow, counter, diag,                             \
	    "Purge-map growth rebuilds")                                      \
	CT(purgemap_rebuilds_same_size, counter, diag,                        \
	    "Purge-map same-size rebuilds")                                   \
	CT(purgemap_rebuilds_shrink, counter, diag,                           \
	    "Purge-map shrinking rebuilds")                                   \
	CT(purgemap_auto_reclaim_passes, counter, info,                       \
	    "Certified automatic purge-history reclamation passes")           \
	CT(purgemap_auto_reclaimed_entries, counter, info,                    \
	    "Purge-map entries removed by certified reclamation")             \
	CT(purgemap_auto_reclaimed_bytes, counter, diag,                      \
	    "Purge-map bytes removed by certified reclamation")               \
	CT(purgemap_auto_reclaim_deferred_pending, counter, diag,             \
	    "Reclamation attempts deferred for pending publications")         \
	CT(purgemap_auto_reclaim_defer_usec, counter, debug,                  \
	    "Publication-grace reclamation deferral microseconds")            \
	CT(purgemap_auto_reclaim_defer_max_usec, gauge, debug,                \
	    "Maximum publication-grace reclamation deferral microseconds")    \
	CT(purgemap_auto_reclaim_defer_last_usec, gauge, debug,               \
	    "Last publication-grace reclamation deferral microseconds")       \
	CT(purgemap_auto_reclaim_filter_usec, counter, debug,                 \
	    "Purge-map certified reclamation filter microseconds")            \
	CT(purgemap_auto_reclaim_filter_max_usec, gauge, debug,               \
	    "Maximum purge-map certified reclamation filter microseconds")    \
	CT(purgemap_auto_reclaim_filter_last_usec, gauge, debug,              \
	    "Last purge-map certified reclamation filter microseconds")       \
	CT(purgemap_auto_reclaim_transient_bytes, gauge, debug,               \
	    "Last old-plus-new purge-map bytes during reclamation")           \
	CT(purgemap_auto_reclaim_table_slots_before, gauge, debug,            \
	    "Purge-map slots before last certified reclamation")              \
	CT(purgemap_auto_reclaim_table_slots_after, gauge, debug,             \
	    "Purge-map slots after last certified reclamation")               \
	CT(purgemap_probe_hard_hits, counter, info,                           \
	    "Hard-purge probe hits")                                          \
	CT(purgemap_probe_soft_hits, counter, info,                           \
	    "Soft-purge probe hits")                                          \
	CT(purgemap_insert_probe_hits, counter, diag,                         \
	    "Insert-time purge probe hits")                                   \
	CT(purgemap_fellow_attr_objects_written, counter, info,               \
	    "Fellow objects written with a cachetag attribute")               \
	CT(purgemap_fellow_attr_bytes_written, counter, diag,                 \
	    "Attributed Fellow cachetag attribute bytes written")             \
	CT(purgemap_fellow_direct_probes, counter, info,                      \
	    "Direct Fellow attribute probes")                                 \
	CT(purgemap_fellow_attr_absent, counter, info,                        \
	    "Fellow objects without a cachetag attribute")                    \
	CT(purgemap_fellow_attr_invalid, counter, info,                       \
	    "Invalid Fellow cachetag attributes")                             \
	CT(purgemap_fellow_attr_read_failures, counter, info,                 \
	    "Fellow cachetag attribute read failures")                        \
	CT(purgemap_fellow_namespace_records_probed, counter, diag,           \
	    "Fellow namespace records probed")                                \
	CT(purgemap_fellow_store_invariant_failures, counter, info,           \
	    "Fellow store inclusion invariant failures")                      \
	CT(purgemap_volatile_fallback_attaches, counter, info,                \
	    "Fellow allocations attached through volatile fallback")          \
	CT(sweep_passes, counter, info,                                       \
	    "Purge-map sweep passes")                                         \
	CT(sweep_aborts, counter, diag,                                       \
	    "Purge-map sweeps aborted by shutdown")                           \
	CT(sweep_scanned, counter, info,                                      \
	    "Volatile membership records scanned by sweeps")                  \
	CT(sweep_killed, counter, info,                                       \
	    "Objects killed by sweeps")                                       \
	CT(sweep_reduced, counter, diag,                                      \
	    "Objects reduced by soft-purge sweeps")                           \
	CT(sweep_batches, counter, debug,                                     \
	    "Sweep batches")                                                  \
	CT(sweep_last_batches, gauge, debug,                                  \
	    "Batches in the last sweep pass")                                 \
	CT(sweep_batch_scanned_max, gauge, debug,                             \
	    "Maximum objects scanned by one sweep batch")                     \
	CT(sweep_batch_hold_over_2ms, counter, debug,                         \
	    "Sweep batches holding obj_mtx above 2ms")                        \
	CT(sweep_batch_hold_over_5ms, counter, debug,                         \
	    "Sweep batches holding obj_mtx above 5ms")                        \
	CT(sweep_batch_hold_over_10ms, counter, debug,                        \
	    "Sweep batches holding obj_mtx above 10ms")                       \
	CT(sweep_remaining, gauge, diag,                                      \
	    "Objects remaining in the active sweep pass")                     \
	CT(sweep_wakeups, counter, diag,                                      \
	    "Purge-map sweeper condition wakeups")                            \
	CT(sweep_iterations, counter, diag,                                   \
	    "Purge-map sweeper work-loop iterations")                         \
	CT(sweep_obj_mtx_wait_usec, counter, debug,                           \
	    "Sweep obj_mtx wait microseconds")                                \
	CT(sweep_obj_mtx_wait_max_usec, gauge, debug,                         \
	    "Maximum sweep obj_mtx wait microseconds")                        \
	CT(sweep_obj_mtx_wait_last_usec, gauge, debug,                        \
	    "Last sweep obj_mtx wait microseconds")                           \
	CT(sweep_obj_mtx_hold_usec, counter, debug,                           \
	    "Sweep continuous obj_mtx hold microseconds")                     \
	CT(sweep_obj_mtx_hold_max_usec, gauge, debug,                         \
	    "Maximum sweep continuous obj_mtx hold microseconds")             \
	CT(sweep_obj_mtx_hold_last_usec, gauge, debug,                        \
	    "Last sweep continuous obj_mtx hold microseconds")                \
	CT(sweep_unlocked_gap_usec, counter, debug,                           \
	    "Sweep inter-batch unlocked gap microseconds")                    \
	CT(sweep_unlocked_gap_last_usec, gauge, debug,                        \
	    "Last sweep inter-batch unlocked gap microseconds")               \
	CT(sweep_per_object_max_usec, gauge, debug,                           \
	    "Maximum single object operation microseconds during sweeps")     \
	CT(sweep_deferred_shrinks, counter, debug,                            \
	    "Container shrinks deferred while a sweep pass was active")       \
	CT(sweep_total_usec, counter, debug,                                  \
	    "Total certified sweep microseconds")                             \
	CT(sweep_total_max_usec, gauge, debug,                                \
	    "Maximum certified sweep microseconds")                           \
	CT(sweep_total_last_usec, gauge, debug,                               \
	    "Last certified sweep microseconds")                              \
	CT(sweep_last_scanned, gauge, diag,                                   \
	    "Objects scanned by last sweep")                                  \
	CT(sweep_last_killed, gauge, diag,                                    \
	    "Objects killed by last sweep")                                   \
	CT(sweep_last_reduced, gauge, diag,                                   \
	    "Objects reduced by last sweep")                                  \
	CT(sweep_last_objects_before, gauge, diag,                            \
	    "Volatile objects before last sweep")                             \
	CT(sweep_last_objects_after, gauge, diag,                             \
	    "Volatile objects after last sweep")                              \
	CT(publication_phase, gauge, diag,                                    \
	    "Current publication phase")                                      \
	CT(publication_readers_phase0, gauge, diag,                           \
	    "Current publication readers in phase zero")                      \
	CT(publication_readers_phase1, gauge, diag,                           \
	    "Current publication readers in phase one")                       \
	CT(publication_acquires, counter, diag,                               \
	    "Publication reader tokens acquired")                             \
	CT(publication_releases, counter, diag,                               \
	    "Publication reader tokens released")                             \
	CT(reclaim_pending, gauge, diag,                                      \
	    "Certified reclamation waiting for publication readers")          \
	CT(reclaim_phase, gauge, diag,                                        \
	    "Publication phase blocking certified reclamation")               \
	CT(persist_wal_records, counter, info,                                \
	    "Persistent purge WAL records appended")                          \
	CT(persist_wal_bytes, counter, info,                                  \
	    "Persistent purge WAL bytes appended")                            \
	CT(persist_checkpoint_entries, gauge, info,                           \
	    "Entries in the published purge-history checkpoint")              \
	CT(persist_checkpoint_wal_sequence, gauge, diag,                      \
	    "WAL record sequence covered by the published checkpoint")        \
	CT(persist_checkpoint_bytes, gauge, diag,                             \
	    "Bytes in the published purge-history checkpoint")                \
	CT(persist_checkpoint_publications, counter, diag,                    \
	    "Purge-history checkpoints published")                            \
	CT(persist_checkpoint_segments_collected, counter, diag,              \
	    "Checkpoint-covered WAL segments collected")                      \
	CT(persist_orphan_files_collected, counter, diag,                     \
	    "Manifest-relative orphan persistence files collected")           \
	CT(persist_replay_records, counter, info,                             \
	    "WAL records read after the checkpoint during startup")           \
	CT(persist_failures, counter, info,                                   \
	    "Persistent purge WAL failures")                                  \
	CT(persist_degraded, gauge, info,                                     \
	    "Persistence backend degraded state")                             \
	CT(fellow_replayed_records, counter, diag,                            \
	    "Purge WAL records replayed for Fellow namespaces")

/*
 * Family accumulators.
 *
 * The three families below live on struct cachetag_index as accumulators and
 * are fanned out into the flat published struct by cachetag_snapshot_counters().
 * The group tables name the VSC prefix and the matching index member; the
 * member lists name the members, once, for every instance of the family.
 *
 * The member lists take the per-group context as macro arguments -- M(g, i, m)
 * rather than M(m) -- because the preprocessor cannot bind a "current group"
 * into a member-only list.  Expansion is CACHETAG_<FAMILY>_GROUPS(G) with a
 * group macro that calls CACHETAG_<FAMILY>_MEMBERS(M, g, i).
 */

/* CT_LOCKWAIT(vsc_prefix, index_member); VSC name is <prefix>_obj_mtx_<member> */
#define CACHETAG_LOCKWAIT_GROUPS(CT_LOCKWAIT)                                 \
	CT_LOCKWAIT(request_probe, lockwait_request_probe)                    \
	CT_LOCKWAIT(request_attach, lockwait_request_attach)                  \
	CT_LOCKWAIT(request_invalidate, lockwait_request_invalidate)

#define CACHETAG_LOCKWAIT_MEMBERS(M, g, i)                                    \
	M(g, i, calls)                                                        \
	M(g, i, wait_usec)                                                    \
	M(g, i, wait_max_usec)

/* CT_RESIZE(vsc_prefix, index_member); VSC name is <prefix>_<member> */
#define CACHETAG_RESIZE_GROUPS(CT_RESIZE)                                     \
	CT_RESIZE(object_grow, resize_object_grow)                            \
	CT_RESIZE(object_shrink, resize_object_shrink)                        \
	CT_RESIZE(side_grow_rehash, resize_side_grow_rehash)                  \
	CT_RESIZE(side_shrink_rehash, resize_side_shrink_rehash)              \
	CT_RESIZE(zero_container_free, resize_zero_container_free)

#define CACHETAG_RESIZE_MEMBERS(M, g, i)                                      \
	M(g, i, calls)                                                        \
	M(g, i, usec)                                                         \
	M(g, i, max_usec)                                                     \
	M(g, i, failures)                                                     \
	M(g, i, compact_active_calls)

/*
 * CT_TIMING(vsc_prefix, index_member, member_list); VSC name is
 * <prefix>_<member>.  Only volatile_interned_acquire publishes the
 * over-threshold buckets, so the other three groups carry the brief list.  The
 * accumulator type keeps all seven members for every instance, so
 * cachetag_note_intern_timing() serves all four unchanged.
 */
#define CACHETAG_TIMING_GROUPS(CT_TIMING)                                     \
	CT_TIMING(volatile_interned_acquire, intern_acquire_timing,           \
	    CACHETAG_TIMING_MEMBERS_FULL)                                     \
	CT_TIMING(volatile_interned_table_grow, intern_table_grow_timing,     \
	    CACHETAG_TIMING_MEMBERS_BRIEF)                                    \
	CT_TIMING(volatile_interned_candidate_alloc,                          \
	    intern_candidate_alloc_timing, CACHETAG_TIMING_MEMBERS_BRIEF)     \
	CT_TIMING(volatile_interned_table_alloc, intern_table_alloc_timing,   \
	    CACHETAG_TIMING_MEMBERS_BRIEF)

#define CACHETAG_TIMING_MEMBERS_FULL(M, g, i)                                 \
	M(g, i, calls)                                                        \
	M(g, i, usec)                                                         \
	M(g, i, max_usec)                                                     \
	M(g, i, over_50us)                                                    \
	M(g, i, over_250us)                                                   \
	M(g, i, over_1ms)                                                     \
	M(g, i, over_10ms)

#define CACHETAG_TIMING_MEMBERS_BRIEF(M, g, i)                                \
	M(g, i, calls)                                                        \
	M(g, i, usec)                                                         \
	M(g, i, max_usec)
