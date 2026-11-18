# Using cachetag

Cachetag expects your backend response to include each object's tags in an HTTP header. VCL then registers the tags with cachetag on Varnish Cache or Vinyl Cache.

## Example

A backend response:

```http
HTTP/1.1 200 OK
Cache-Control: max-age=3600
Cache-Tag: article:123, author:42, section:news
```

The VCL:

```vcl
vcl 4.1;

import cachetag;

backend default {
    .host = "127.0.0.1";
    .port = "8080";
}

acl purgers {
    "127.0.0.1";
    "203.0.113.0"/24;
}

sub vcl_init {
    new tags = cachetag.namespace("default");
}

sub vcl_recv {
    if (req.method == "PURGE") {
        if (client.ip !~ purgers) {
            return (synth(403, "Forbidden"));
        }

        if (req.http.Cache-Tag-Purge) {
            set req.http.purged = tags.purge_header(req.http.Cache-Tag-Purge);
            if (req.http.purged == "-1") {
                return (synth(200, "Purge accepted"));
            }
            return (synth(503, "Purge failed (" + req.http.purged + ")"));
        }

        return (purge);
    }
}

sub vcl_backend_response {
    if (beresp.http.Cache-Tag) {
        tags.add_header(beresp.http.Cache-Tag);
        unset beresp.http.Cache-Tag;
    }
}

sub vcl_backend_error {
    if (beresp.http.Cache-Tag) {
        tags.add_header(beresp.http.Cache-Tag);
        unset beresp.http.Cache-Tag;
    }
}

sub vcl_hit {
    if (tags.stale()) {
        return (restart);
    }
}

sub vcl_deliver {
    if (tags.stale()) {
        return (restart);
    }
}
```

A `PURGE` request without a `Cache-Tag-Purge` header falls through to the engine's ordinary URL purge.

## Checking for stale objects

Cachetag doesn't track objects per tag, so a purge can't jump straight to matching objects. Instead, each object is checked against purge history when read. See [How the strict invalidation guarantee works](strictness.md) for the design details.

Objects in memory are also checked by a background sweep every `sweep_interval` (default 60s; `0s` turns it off). The sweep kills hard-purged objects and curtails soft-purged ones to their grace period. In a memory-only namespace it then drops the tag from purge history once no matching objects remain. Fellow objects aren't swept yet, so they are only checked on read.

Call `stale()` in both `vcl_hit` and `vcl_deliver` as shown above: reliable hard purges depend on both checks. Together, they catch tags purged after registration, including races with in-flight fetches and objects already sitting in cache. On a hit, the request restarts and fetches fresh content. If the same request checks the same object twice, the second call reuses the first answer unless a purge was published in that namespace or the object was invalidated in the meantime. The `stale_calls` VSC counter tracks every check, and `stale_memo_hits` tracks how many were answered from cache.
## Registering tags

Use `add_header()`. It splits on commas, trims whitespace around each tag, and rejects tags containing whitespace. If your application uses a different separator, pass it as `sep` (for example, `tags.add_header(beresp.http.Cache-Tag, sep = " ")`). Stick with commas if you can; they avoid ambiguity in tag names.

If an object exceeds either limit below, cachetag rejects the fetch with a 503 and increments `limit_rejections`. It won't cache an object with missing tags, which would risk missing later purges:

- `max_keys_per_object`: unique tags per object. Default is 512 (my largest production objects have 333 tags).
- `max_tag_header_bytes`: maximum size of a single header value. Default is 16 KiB.

There's no separate cap on tag count within a single header. If your objects carry more than 512 tags, bump `max_keys_per_object`.

## Tags on uncacheable responses

Tags are registered before the cacheability decision is finalised. If builtin backend-response VCL sees a non-positive TTL, `Set-Cookie`, `Cache-Control: no-cache`, `no-store` or `private`, or `Vary: *`, it normally makes the object hit-for-miss. Calling `return (pass(duration))` makes it hit-for-pass. When either happens, cachetag drops pending registrations on insert because the object won't serve cached content. Regular pass fetches never create pending registrations or insert cache objects.

Redirects often expose this. Vinyl assigns TTLs by status before your backend-response VCL runs:

| Status | TTL source |
| --- | --- |
| 200, 203, 204, 300, 301, 304, 404, 410, 414 | Cache-Control `s-maxage` or `max-age`, then `Expires`, then `default_ttl` |
| 302, 307 | Explicit Cache-Control freshness or `Expires`; otherwise negative |
| Other statuses, including 303 and 308 | Negative, even with explicit Cache-Control freshness |

A 302 without freshness—or a 308 even with `max-age`—hits the backend on every request under the builtin policy. `add_header()` succeeds and `pending()` drops back to zero, but no tags are stored and repeat requests increment `MAIN.cache_hitmiss`. The namespace's `transient_discards` counter tracks how many inserted objects had their registrations dropped (counting objects, not tags; pass fetches and abandoned requests are excluded). When a transaction log is available, cachetag also logs a backend-transaction `Notice` with the discarded key count and object kind:

```text
vmod_cachetag: Discarded 1 tag registration(s), object is hit-for-miss
```

If both flags are set, hit-for-pass takes precedence. Cachetag uses transaction logs to flag issues an operator needs to fix, not for per-request accounting. By default, this Notice is logged once per affected insert without rate limiting. Its volume tracks your backend fetch rate for tagged uncacheable responses. To silence it, set `log_transient_discards = false` on the namespace; `transient_discards` still tracks the discards:

```vcl
sub vcl_init {
    new tags = cachetag.namespace("default", log_transient_discards = false);
}
```

`purge()` returns `-1` once a purge is published, whether or not any cached objects currently carry the tag. It records purge history rather than scanning for matching objects. Strict freshness still holds: hit-for-miss and hit-for-pass objects never serve cached content anyway.

If you want to cache redirects, you can restore explicit freshness in `vcl_backend_response` when the TTL is non-positive. Because the VCL TTL getter can report a negative core TTL as zero, check `beresp.ttl <= 0s`. Add `import std;` alongside `import cachetag;`. The snippet below prefers `s-maxage` over `max-age`, subtracts `Age`, and clamps expired responses to zero (for example, `max-age=60` with `Age: 50` gets about 10 seconds of freshness, not another minute). There is no `beresp.age` variable, so setting TTL to the raw header value would overextend freshness.

This parser intentionally accepts only unquoted, token-valued `Cache-Control` directives. Quoted strings, escape sequences, empty items, duplicate freshness directives, or malformed values leave the response uncacheable. Missing `Age` defaults to zero; missing freshness directives leave it uncacheable. An invalid `s-maxage` never falls back to `max-age`. Numbers over 15 digits (including padded values) are rejected. The two scratch headers are cleared before use so backend headers can't inject bogus freshness.

```vcl
sub vcl_backend_response {
    unset beresp.http.X-Cachetag-Freshness;
    unset beresp.http.X-Cachetag-Age;

    if (beresp.status >= 300 && beresp.status < 400) {
        set beresp.grace = 0s;

        if (beresp.ttl <= 0s) {
            std.collect(beresp.http.Cache-Control);
            std.collect(beresp.http.Age);

            # Quoted arguments can hide commas and directive names, so reject them.
            if (beresp.http.Cache-Control ~ "^[ \t]*[-!#$%&'*+.^_`|~0-9A-Za-z]+(?:[ \t]*=[ \t]*[-!#$%&'*+.^_`|~0-9A-Za-z]+)?(?:[ \t]*,[ \t]*[-!#$%&'*+.^_`|~0-9A-Za-z]+(?:[ \t]*=[ \t]*[-!#$%&'*+.^_`|~0-9A-Za-z]+)?)*[ \t]*$") {
                if (beresp.http.Cache-Control ~ "(?i)(^|,)[ \t]*s-maxage[ \t]*(=|,|$)") {
                    if (beresp.http.Cache-Control !~ "(?i)(^|,)[ \t]*s-maxage(?=[ \t=,]|$).*?,[ \t]*s-maxage(?=[ \t=,]|$)") {
                        set beresp.http.X-Cachetag-Freshness = regsub(
                            beresp.http.Cache-Control,
                            "(?i)^(?:[^,]*,)*?[ \t]*s-maxage[ \t]*=[ \t]*([0-9]+)[ \t]*(?:,.*)?$",
                            "\1");
                    }
                } else if (beresp.http.Cache-Control ~ "(?i)(^|,)[ \t]*max-age[ \t]*(=|,|$)") {
                    if (beresp.http.Cache-Control !~ "(?i)(^|,)[ \t]*max-age(?=[ \t=,]|$).*?,[ \t]*max-age(?=[ \t=,]|$)") {
                        set beresp.http.X-Cachetag-Freshness = regsub(
                            beresp.http.Cache-Control,
                            "(?i)^(?:[^,]*,)*?[ \t]*max-age[ \t]*=[ \t]*([0-9]+)[ \t]*(?:,.*)?$",
                            "\1");
                    }
                }
            }

            set beresp.http.X-Cachetag-Age = "0";
            if (beresp.http.Age) {
                set beresp.http.X-Cachetag-Age = "-1";
                if (beresp.http.Age ~ "^[ \t]*[0-9]+[ \t]*$") {
                    set beresp.http.X-Cachetag-Age = regsub(
                        beresp.http.Age, "^[ \t]*([0-9]+)[ \t]*$", "\1");
                }
            }

            if (std.integer(beresp.http.X-Cachetag-Freshness, -1) >= 0 &&
                std.integer(beresp.http.X-Cachetag-Age, -1) >= 0) {
                set beresp.ttl = std.duration(integer =
                    std.integer(beresp.http.X-Cachetag-Freshness, -1)) -
                    std.duration(integer =
                    std.integer(beresp.http.X-Cachetag-Age, -1));
                if (beresp.ttl < 0s) {
                    set beresp.ttl = 0s;
                }
            }
        }
    }

    unset beresp.http.X-Cachetag-Freshness;
    unset beresp.http.X-Cachetag-Age;
    tags.add_header(beresp.http.Cache-Tag);
}
```

The example sets redirect grace to zero and falls through to builtin VCL. Cookie, Cache-Control, Vary, and non-positive TTL checks still apply, and an existing `beresp.uncacheable` flag remains set. Preserve this fall-through when adapting the logic.

## Tag interning

By default, every in-memory object with multiple tags keeps its own tag list. Tag interning makes the namespace store each distinct tag set once, sharing it across all objects that carry the exact same tags:

```vcl
sub vcl_init {
    new tags = cachetag.namespace("default", interning = true);
}
```

Interning saves memory when many objects share identical tag sets. However, each unique set adds an entry to the shared registry, and every registration must look up its set. Test on your own traffic before enabling it. Two cases behave the same whether interning is on or off:

- Objects with no tags have zero overhead
- Objects with a single tag store it inline

This applies to objects in bundled in-memory storage, Buddy on Vinyl, and in-memory fallbacks in persistent namespaces. Fellow stores tags directly in object attributes on disk, so interning has no effect there.

The `interning` setting is fixed at namespace creation. Changing it requires loading a new VCL, which invalidates all tagged in-memory objects from the previous configuration (see [VCL reloads](#vcl-reloads)).

## VCL reloads

Cached objects survive the VCL that fetched them, so cachetag must handle VCL reloads cleanly. In this section, "in-memory objects" refers to objects whose tags live in cachetag's own index: everything in a memory-only namespace, plus in-memory fallbacks in a persistent namespace. Fellow objects are discussed below.

A memory-only namespace in a new VCL reuses the existing index if it shares the same name and identical settings for `interning`, `max_keys_per_object`, `max_key_length`, `max_tag_header_bytes`, `sweep_interval`, `purge_history_max_entries`, `sweep_batch_objects`, `sweep_batch_hold`, and `sweep_batch_yield`. The old VCL must still be loaded, and its index must not have been retired. Objects cached before the reload keep their tags, and purges through the new VCL reach them as expected. The index remains active as long as either VCL is warm.

When a VCL importing cachetag warms up, cachetag retires any index from older VCLs that the new VCL doesn't inherit. That includes renamed, deleted, or reconfigured namespaces. Retiring an index invalidates its tagged objects, since the new VCL lacks tag tracking for them and subsequent purges would miss them. Both engines warm VCL during `vcl.load`, so this happens before you switch traffic with `vcl.use`. Expect a burst of backend traffic following this kind of reload.

A retired index is sealed immediately. In-flight fetches under the old VCL cannot register tags; their objects are discarded rather than cached. If the request reaches `vcl_deliver` and calls `stale()`, it detects the dead object and restarts to fetch fresh content. Sealing is permanent—reverting to the old VCL via `vcl.use` will not restore tag tracking.

Cachetag also invalidates an index's remaining tagged objects when its VCL cools down and no warm VCL shares the index. Cachetag only tracks VCLs that explicitly import it; if a new VCL omits `import cachetag;`, nothing is retired on warmup. In that scenario, ban or purge old tagged objects before activating the new VCL. Objects cached without tags are never touched.

Fellow objects keep their tags on disk alongside object data and validate against the purge history in `persist_path`. The in-memory retirement rules above don't apply to them. Keep each persistent namespace's name and `persist_path` consistent across reloads (including when multiple namespaces share a name). If you rename a namespace or change `persist_path`, purge the affected Fellow objects before switching to the new VCL.

## Purging

To purge one or more tags, send a `PURGE` request with the tags in the purge header:

```http
PURGE / HTTP/1.1
Host: www.example.com
Cache-Tag-Purge: article:123, section:news
```

To purge a single tag from VCL, call `tags.purge("article:123")`.

`purge_header()` validates every tag and removes duplicates before publishing, so an invalid tag halts the entire header. Because of cachetag's generational design, purges record invalidation timestamps rather than scanning objects, so it cannot report how many objects were invalidated. Return codes:

- `-1`: Success. All tags accepted into purge history.
- `-2`: Rejected (resource exhausted or configured limit reached).
- `-3`: Invalid input.
- `-4`: Persistence unavailable or disk write failed.

To persist purge history across restarts with Fellow, set `persist_path` (see [Fellow storage](install.md#fellow-storage)). Without `persist_path`, namespaces are strictly memory-only.

With `persist_path`, purges are written to a write-ahead log (WAL) and only return success once flushed to disk. All tags in a single `purge_header()` call are written in a single transaction with one fsync, regardless of tag count. On recovery, headers replay atomically: either all tags recover or none do. `wal_fsync = grouped` is currently accepted as an alias for `strict` (group commit across concurrent calls is not yet implemented). Persistent namespaces periodically compact purge history into checkpoints and prune obsolete WAL segments.

`purge_history_max_entries` (default 1,000,000) limits in-memory purge history. Setting it to `0` removes the limit (use with caution).

Soft purges mark matching objects as expired without immediately discarding them, allowing grace and keep handling while background fetches refresh content:

```vcl
set req.http.purged = tags.purge_header(req.http.Cache-Tag-Purge, mode = soft);
```

## Counters

Cachetag publishes VSC counters at three levels:

- `info`: Operational metrics for production. Call counts, rejections, persistence status, and purge map size.
- `diag`: Incident troubleshooting. Memory breakdowns, table resize/compaction state, and underlying denominators for `info` rates.
- `debug`: Internal mechanics and opt-in instrumentation for cachetag development.

Monitor `CACHETAG.*.transient_discards` in production. It counts objects whose pending tags were dropped upon inserting a hit-for-miss or hit-for-pass response. It tracks object count (not tag count) and ignores pass fetches and aborted requests. A rising count usually indicates backend headers or VCL rules preventing cacheability. Unless silenced via `log_transient_discards = false`, backend `vmod_cachetag:` notices will detail the discarded tag count and object type.

`varnishstat` and `vinylstat` curses interfaces default to `info` level. One-shot (`-1`) and JSON (`-j`) modes dump all counters regardless of level.

Each namespace runs a background thread publishing counters every 0.1s by default, meaning counters lag by at most 100ms. Calls to `objects()`, `pending()`, `edges()`, and `compact()` trigger an immediate synchronous update. Adjust this interval via `vsc_publish_interval`, or set `0s` to publish synchronously on every call.

When two VCLs share an index across a reload, both warm namespaces report gauges against that same shared index. Memory gauges such as `index_memory_bytes` describe a single underlying allocation—do not sum them across VCLs. A cold VCL may retain and display its last reported values until discarded.
```
