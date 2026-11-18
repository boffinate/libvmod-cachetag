# Using cachetag

The step-zero expectation we have is your backend response contains each object's tags in a HTTP header. VCL then registers the tags with cachetag on either Varnish Cache or Vinyl Cache.

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

Call `stale()` in both `vcl_hit` and `vcl_deliver` as shown above: reliable hard purges depend on both checks. Combined, they catch objects purged after registration, including ones that raced an in-flight fetch and ones still sitting in memory. The request then restarts and fetches fresh content. The second call for the same object in one request reuses the first answer, unless a purge has been published in the namespace or the object has been killed since. The VSC counter `stale_calls` counts every call, and `stale_memo_hits` counts the calls answered this way.

## Registering tags

Use `add_header()`. It splits on commas, trims whitespace around each tag, and rejects tags containing whitespace. If your application uses a different separator, pass it as `sep` (for example, `tags.add_header(beresp.http.Cache-Tag, sep = " ")`). I recommend using commas if you can, as they avoid any ambiguity in tag names.

If an object exceeds either limit below, cachetag rejects the fetch with a 503 and increments `limit_rejections`. It won't cache an object with missing tags, which would risk missing later purges:

- `max_keys_per_object`: unique tags per object. The default is 512, as the largest objects I've got in production have 333 tags.
- `max_tag_header_bytes`: maximum size of a single header value. Default is 16 KiB.

There's no separate cap on tag count within a single header. If your objects carry more than 512 tags, bump `max_keys_per_object`.

## Tag interning

By default, every in-memory object with multiple tags keeps its own tag list. Tag interning makes the namespace store each distinct tag set once, sharing it across all objects that carry the exact same tags:

```vcl
sub vcl_init {
    new tags = cachetag.namespace("default", interning = true);
}
```

Interning saves memory when many objects share identical tag sets. However, each unique set adds an entry to the shared registry, and every registration must look up its set. Measure it on your own traffic before assuming you'll save memory. Oh and two things are the same with interning on or off:

- Objects with no tags have zero overhead
- Objects with a single tag store it inline

This applies to objects in bundled in-memory storage, Buddy on Vinyl, and in-memory fallbacks in persistent namespaces. Fellow stores tags directly in object attributes on disk, so interning has no effect there.

The `interning` setting is fixed at namespace creation. Changing it requires loading a new VCL, which invalidates all tagged in-memory objects from the previous configuration (see [VCL reloads](#vcl-reloads)).

## VCL reloads

Cached objects survive the VCL that fetched them, so cachetag has to decide what effect a reload has on cache tagging. In this section, "in-memory objects" refers to objects whose tags live in cachetag's own index: everything in a memory-only namespace, plus in-memory fallbacks in a persistent namespace. Fellow objects are discussed below.

A memory-only namespace in a new VCL reuses the existing index if it shares the same name and identical settings for `interning`, `max_keys_per_object`, `max_key_length`, `max_tag_header_bytes`, `sweep_interval`, `purge_history_max_entries`, `sweep_batch_objects`, `sweep_batch_hold`, and `sweep_batch_yield`. The old VCL must still be loaded, and its index must not have been retired. Objects cached before the reload keep their tags, and purges through the new VCL reach them as expected. The index remains active as long as either VCL is warm.

When a VCL importing cachetag warms up, cachetag retires every index belonging to another VCL that the new VCL doesn't share. That includes renamed, deleted, or reconfigured namespaces. Retiring an index invalidates its tagged objects, since the new VCL lacks tag tracking for them and subsequent purges would miss them. Both engines normally warm a VCL as part of `vcl.load`, so this happens before you switch traffic with `vcl.use`. Expect a burst of backend traffic following this kind of reload.

A retired index is sealed. In-flight fetches under the old VCL cannot register tags; their objects are discarded rather than cached. If the request reaches `vcl_deliver` and calls `stale()`, it detects the dead object and restarts to fetch fresh content. The seal is permanent, so switching back to the old VCL with `vcl.use` doesn't bring back tagged caching.

Cachetag also invalidates an index's remaining tagged objects when its VCL cools down and no warm VCL shares the index. Cachetag only tracks VCLs that explicitly import it; if a new VCL omits `import cachetag;`, nothing is retired on warmup. Flush or ban the old tagged objects before you activate a VCL like that. Objects cached without tags are never touched.

Fellow objects keep their tags on disk with the object, and cachetag checks them against the purge history in `persist_path`. The rules above don't apply to them, and retiring an index doesn't touch them. Keep each persistent namespace's name and `persist_path` consistent across reloads (including when multiple namespaces share a name). If you rename or remove one, or change its `persist_path`, flush the affected Fellow objects before activating the new VCL.

## Purging

To purge one or more tags, send a `PURGE` request with the tags in the purge header:

```http
PURGE / HTTP/1.1
Host: www.example.com
Cache-Tag-Purge: article:123, section:news
```

To purge a single tag from VCL, call `tags.purge("article:123")`.

`purge_header()` validates every tag and removes duplicates before publishing, so a header with one bad tag purges nothing. Due to Cachetag's design we can't tell you how many objects were purged. Return codes:

- `-1`: Success. All tags accepted into purge history.
- `-2`: Rejected (resource exhausted or configured limit reached).
- `-3`: Invalid input.
- `-4`: Persistence is configured but unavailable, or the write failed.

To persist purge history across restarts with Fellow, set `persist_path` (see [Fellow storage](install.md#fellow-storage)). Without `persist_path`, namespaces are memory-only.

With `persist_path`, purges are written to a write-ahead log (WAL) and only return success once flushed to disk. All tags in a single `purge_header()` call are written in a single transaction with one fsync, regardless of tag count. On recovery, headers replay atomically: either all tags recover or none do. `wal_fsync = grouped` is currently accepted as an alias for `strict` (group commit across calls is not yet implemented). Persistent namespaces write the purge history to checkpoint files and delete the WAL segments a checkpoint covers.

`purge_history_max_entries` (default 1,000,000) limits in-memory purge history. Setting it to `0` removes the limit (use with caution).

Soft purges are supported and don't remove matching objects. They are treated as expired but stay usable for their grace and keep periods while a fresh copy is fetched:

```vcl
set req.http.purged = tags.purge_header(req.http.Cache-Tag-Purge, mode = soft);
```

## Counters

Cachetag publishes VSC counters at three levels:

- `info`: Operational metrics for production. Call counts, rejections, persistence status, and purge-map state.
- `diag`: Incident troubleshooting. Memory breakdowns, resize and publication state, and the denominators behind the `info` counters.
- `debug`: Internal mechanics and opt-in instrumentation for cachetag development.

`varnishstat` and `vinylstat` curses interfaces default to `info` level. One-shot (`-1`) and JSON (`-j`) modes dump all counters regardless of level.

Each namespace runs a background thread publishing counters every 0.1s by default, so a value you read is at most one interval old. Calls to `objects()`, `pending()`, `edges()`, and `compact()` trigger an immediate synchronous update. Adjust this interval via `vsc_publish_interval`, or set `0s` to publish synchronously on every call.

When two VCLs share an index across a reload, both warm namespaces report gauges against that same shared index. Memory gauges such as `index_memory_bytes` describe a single underlying allocation, so don't add them up across VCLs. A cold VCL may retain and display its last reported values until discarded.
