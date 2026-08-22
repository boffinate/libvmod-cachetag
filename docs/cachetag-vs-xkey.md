## How cachetag differs from xkey

![cachetag-vs-xkey-approach](./_images/cachetag-vs-xkey-approach.svg)

## Why cachetag differs from xkey

This generational approach saves memory, particularly on large caches. It also allows us to work efficiently with Fellow on-disk storage.

## Benchmarks: memory and CPU vs `xkey`

The comparison used in-memory storage, 100,000 objects, four tags per object, and 400,000 object-tag relationships. The figures are medians from controlled synthetic workloads; they are not production sizing guidance or maximum-throughput claims.

| Workload                        |                   xkey extra process PSS | Cachetag difference |
| ------------------------------- | ---------------------------------------: | ------------------: |
| Mostly unique tags, 2-byte body | 79.467 MiB total; about 833 bytes/object |        42.18% lower |
| Mostly shared tags, 2-byte body | 34.896 MiB total; about 366 bytes/object |        24.23% lower |

Process PSS is measured after loading, draining cachetag's pending attachment work, and waiting for a quiescent endpoint. It compares whole-process memory rather than implementation-specific counters. The 2-byte bodies largely remove response-body size from the comparison, leaving tag-index and object-metadata costs. Tag sharing matters: the mostly-unique workload saved about 833 bytes per object, while the mostly-shared workload saved about 366 bytes per object. These are whole-process PSS deltas, not a universal per-object index allocation.

With 4 KiB bodies and moderate tag sharing, xkey used 38.4 MiB more PSS across 100,000 objects — about 403 bytes per object — while cachetag's total PSS was 6.98% lower. Larger response bodies will dilute the percentage of total process memory because both implementations store the response body; the tag-shape-dependent byte deltas are the more portable comparison.

| Workload                     | Cachetag load CPU |    xkey load CPU | Cachetag difference |
| ---------------------------- | ----------------: | ---------------: | ------------------: |
| Mostly unique tags           |  330.87 µs/object | 341.40 µs/object |         3.09% lower |
| Mostly shared tags           |  328.31 µs/object | 331.05 µs/object |         0.83% lower |
| Moderate sharing, 4 KiB body |  349.39 µs/object | 355.99 µs/object |         1.86% lower |

Load CPU covers the fixed workload and cachetag's pending-attachment drain. It is phase-aligned CPU consumed by `cache-main`, not container CPU or requests per second.

For warm hits, cachetag measured 0.45–1.09% more CPU than `xkey` across the same three workloads, but every gap was below the campaign's run-to-run noise floor. No directional warm-hit performance claim is warranted. The test used the documented VCL shape, calling `tags.stale()` in both `vcl_hit` and `vcl_deliver`, at a fixed under-saturated offered load.

The next optimizations depend on real tag shapes rather than synthetic ones — [your workload can answer questions mine can't](#what-does-your-workload-look-like). The [full results](benchmarks/RESULTS.md) include ranges, workload definitions, method, provenance, and limitations. The comparison uses in-memory `xkey`, which expires cache objects eagerly at purge time. Cachetag checks purge history at read time instead, so the implementations are not doing identical work.

## Differences from `xkey` and `ykey`

I've tried to do a fair comparison because they all make different choices and tradeoffs and *this is interesting* to a nerd like me. If you spot an error or omission, please open a PR.

| Area                     | `xkey`                                                       | `ykey`                                                       | `cachetag`                                                   |
| ------------------------ | ------------------------------------------------------------ | ------------------------------------------------------------ | ------------------------------------------------------------ |
| Status                   | Actively maintained `varnish-modules` VMOD                   | Varnish Enterprise 6.x, commercial                           | Vinyl Cache 9.x; Varnish Cache support planned               |
| Index model              | Resident mappings from tags to cache objects                 | Secondary-key index integrated with core and storage         | Purge history plus tag digests stored with each cache object; Fellow keeps them in persistent attributes |
| Registration and parsing | Automatically scans repeated `xkey` and legacy `X-HashTwo` headers; splits on commas or whitespace | Explicit key and header calls with configurable separators; supports hashed keys and blobs | Explicit `tags.add()` and `tags.add_header()` calls; configurable separators, validation, and registration limits |
| Invalidation guarantee   | Best effort: busy or in-flight copies may survive until a later purge, refresh, or expiry | Core-integrated and stronger than `xkey`; narrow fetch/commit races are not publicly documented | Strict read barrier: copies invalidated by an accepted purge are rejected before delivery, including after a persistent restart |
| High-fanout purges       | Purge work runs inline in the caller                         | Built for cache-wide keys; returns affected counts           | Purge publication cost does not grow with fanout; no affected count |
| Namespaces               | One global index                                             | Transaction namespaces through `namespace()` and `namespace_reset()` | Multiple namespace instances, each with its own limits, counters, and optional persistence |
| Persistence              | In-memory index only                                         | Persists its index in MSE/MSE4 metadata                      | Memory-only by default; Fellow persists purge history and tag metadata |
| Observability            | Aggregate key and memory counters                            | Per-key statistics plus aggregate registration, purge, memory, and timing counters | Membership, purge-map, stale-check, WAL, and Fellow health counters |

All three offer soft purges that preserve grace and keep, spelled `mode = soft` here.