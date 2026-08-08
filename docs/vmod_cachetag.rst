..
	Copyright (c) 2026 Peter Bowyer
	SPDX-License-Identifier: MPL-2.0

.. _vmod_cachetag:

%%%%%%%%%%%%%
VMOD cachetag
%%%%%%%%%%%%%

``cachetag`` provides explicit cache-tag registration and purge-map based
invalidation for Vinyl Cache. A successful purge publishes durable (when
configured) purge history; it does not walk objects or report an affected
object count.

Namespace Object
================

Create a namespace in ``vcl_init``:

.. code-block:: vcl

	sub vcl_init {
	    new tags = cachetag.namespace("default");
	}

The constructor accepts a namespace name, ``max_keys_per_object``,
``max_key_length``, and ``max_tag_header_bytes``. ``sweep_interval`` controls
the volatile-membership sweeper; ``purge_history_max_entries`` bounds retained
purge history by advancing hard and soft floors. ``persist_path`` enables the
purge WAL and Fellow FDO attributes. ``wal_fsync`` is ``strict`` or ``grouped``
and ``wal_segment_bytes`` controls WAL rotation.

Tag Registration
================

``VOID .add(STRING key)`` registers one key for the object being fetched.
``VOID .add_header(STRING header, STRING sep = ",")`` parses a delimited header;
tokens are trimmed, embedded whitespace is rejected, and configured limits are
enforced. Both methods are restricted to backend response and error contexts.

A registration snapshots the purge sequence. A purge that completed before a
tag is registered does not invalidate that later object; a purge after
registration is detected at insertion and during ``stale()``.

Purge
=====

``INT .purge(STRING key, ENUM { hard, soft } mode = hard)`` and
``INT .purge_header(STRING header, STRING sep = ",", ENUM { hard, soft } mode = hard)``
publish hard or soft purge history.

Return values are:

``-1``
	Accepted purge-history publication.

``-2``
	Resource or configured-limit rejection.

``-3``
	Invalid input. ``purge_header()`` validates the complete header before it
	changes any history.

``-4``
	Persistence is unavailable or a required WAL append failed. The failed
	publication does not advance the sequence visible to new registrations.

Read-Path Validation
====================

``BOOL .stale()`` returns true when the current hit or delivery object has a
fold whose hard purge is newer than its registration sequence. It also retires
that object, allowing the usual ``return (restart)`` pattern to fetch fresh
content. Soft purges reduce expiry once but do not make ``stale()`` true.

Volatile objects use VMOD membership tables. Persistent Fellow objects keep
their membership only in a checksummed FDO attribute and are probed directly on
hit or delivery. Missing, invalid, unsupported, or unreadable Fellow metadata
fails closed. A valid envelope without the queried namespace means that the
object has no membership in that namespace and remains fresh.

Observability
=============

``pending()`` reports unconsumed registrations. ``objects()`` and ``edges()``
describe volatile membership only, so Fellow-direct persistent objects do not
appear in those gauges. ``compact()`` prunes over-cap purge history, rebuilds
tombstone-heavy purge maps, runs one volatile-membership sweep, and requests
deferred low-water container resize maintenance. The return value reports
logical sweep work; object and side-table containers converge asynchronously
after a short observation window so immediate refills can cancel or roll back a
shrink.

Counters report volatile membership, purge-map size/floors/sequence/probes,
sweeps, parse and limit failures, stale detections, WAL health, and Fellow
direct-probe activity. Removed epoch/posting counters are not compatibility
aliases.

Diagnostic Build Flags
======================

A default build exposes only the surface above. Two configure flags add
diagnostic methods; production builds should enable neither.

``--enable-demo-diagnostics`` adds read-only diagnostics: ``generation()``
returns the latest retained purge sequence for a key, ``purge_seq()`` the
namespace's global purge sequence counter, and ``purgemap_entries()``,
``purgemap_slots()``, and ``purgemap_bytes()`` the live purge-map entry count,
hash-table slot count, and hash-table bytes.

``--enable-test-hooks`` adds every ``test_*()`` method: fault injectors and
internal togglers for the regression suite.

The VCC interface is assembled from fragments at build time, so a gated method
is entirely absent from a build without its flag and VCL referencing it fails
to compile.
