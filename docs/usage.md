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

Cachetag doesn't keep a list of objects per tag, so a purge can't go straight to the objects it matches. Instead, each object is checked against the purge history when it's read. [How the strict invalidation guarantee works](strictness.md) explains the design.

Objects in memory are also checked by a background sweep every `sweep_interval` (default 60 s, `0s` turns it off). The sweep kills hard-purged objects and reduces soft-purged ones to their grace period. In a memory-only namespace it then drops the tag from the purge history, since there are no remaining objects. Currently Fellow objects aren't swept (pending a design decision), so they are only checked when read.

It's important that you call `stale()` in both `vcl_hit` and `vcl_deliver` as the examples do, as correct hard purges rely on both checks being present. Combined, they catch objects purged after registration, including ones that raced an in-flight fetch and ones still sitting in memory. The request then restarts and fetches fresh content. The second call for the same object in one request reuses the first answer, unless a purge has been published in the namespace or the object has been killed since. The VSC counter `stale_calls` counts every call, and `stale_memo_hits` counts the calls answered this way.

## Registering tags

`add_header()` is the right function to use. It splits the header on commas, trims whitespace around each tag, and rejects any tag that contains whitespace. If your application uses a different separator, pass it as `sep`, for example `tags.add_header(beresp.http.Cache-Tag, sep = " ")`. I recommend using commas if you can, as they avoid any ambiguity in tag names.

If an object has too many tags (see below), cachetag rejects the fetch. It won't cache an object with some of its tags missing, as a later purge could then miss it. The client gets a 503 and the namespace's `limit_rejections` counter goes up. Two limits apply:

- `max_keys_per_object`: unique tags per object. The default is 512, as the largest objects I've got in production have 333 tags.
- `max_tag_header_bytes`: the size of one header value. The default is 16 KiB.

There's no separate cap on the number of tags in one header. If your objects carry more than 512 tags, raise `max_keys_per_object`.

## Tags on uncacheable responses

Tag registration happens before the final cacheability decision. If the builtin backend-response VCL sees a non-positive TTL, `Set-Cookie`, `Cache-Control: no-cache`, `no-store` or `private`, or `Vary: *`, it normally makes the object hit-for-miss. `return (pass(duration))` makes it hit-for-pass. Cachetag discards pending registrations when these objects are inserted because they cannot serve cached content. Ordinary pass fetches do not create pending registrations or insert reusable objects.

Redirects often expose this. Vinyl assigns TTLs by status before your backend-response VCL runs:

| Status | TTL source |
| --- | --- |
| 200, 203, 204, 300, 301, 304, 404, 410, 414 | Cache-Control `s-maxage` or `max-age`, then `Expires`, then `default_ttl` |
| 302, 307 | Explicit Cache-Control freshness or `Expires`; otherwise negative |
| Other statuses, including 303 and 308 | Negative, even with explicit Cache-Control freshness |

A 302 without freshness, or a 308 even with `max-age`, therefore reaches the backend on every request under the builtin policy. `add_header()` succeeds and `pending()` returns to zero, but membership stays empty and `MAIN.cache_hitmiss` rises on repeats. The namespace's `transient_discards` counter counts each inserted object whose registration was discarded, rather than the number of tag keys. It excludes pass fetches and fetches abandoned before insertion. A backend-transaction `Notice` also reports the number of discarded keys and the object kind:

```text
vmod_cachetag: Discarded 1 tag registration(s), object is hit-for-miss
```

Hit-for-pass takes precedence when both flags are set. Cachetag writes transaction-log records for problems an operator needs to fix, never for per-request accounting. This Notice is emitted once per affected insert and namespace when a transaction log is available, without rate limiting or an opt-out. Its volume follows the backend fetch rate for tagged uncacheable responses.

`purge()` still returns `-1` for accepted publication whether or not any object carries the tag. It publishes purge history rather than finding or counting matching objects. The strict freshness guarantee holds: hit-for-miss and hit-for-pass objects never serve cached content.

If your policy permits caching redirects, this backend-response example restores explicit freshness when the TTL is non-positive. The VCL TTL getter can expose a negative core TTL as zero, so the guard uses `<= 0s`. Add `import std;` alongside `import cachetag;` and your namespace constructor. It prefers `s-maxage` over `max-age`, subtracts Age, and clamps exhausted freshness to zero. `max-age=60` with `Age: 50` gets about ten seconds, not another minute. There is no `beresp.age` variable; assigning the full lifetime would extend freshness because the TTL setter expects remaining time.

The parser deliberately accepts only unquoted token-valued Cache-Control directives. Quoted arguments, escapes, empty list members and malformed directives leave the response uncacheable. Duplicate selected freshness directives, invalid or overflowing selected freshness values, and invalid, duplicate or overflowing Age values also leave it uncacheable. A missing Age means zero; a missing freshness directive means no caching. An invalid `s-maxage` never falls back to `max-age`. Numeric conversion rejects values longer than 15 digits, including excessively padded numbers. The two scratch headers are cleared before use so backend values cannot supply freshness.

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

The example sets redirect grace to zero and falls through to the builtin VCL. Its cookie, cache-control, Vary and non-positive TTL checks still apply; an existing `beresp.uncacheable` flag stays set. Keep that fall-through when adapting the example.

## Tag interning

By default, each in-memory object with more than one tag keeps its own tag list attached to the object. If you enable tag interning, the namespace stores each distinct tag set once and shares it between all objects that carry exactly that set:

```vcl
sub vcl_init {
    new tags = cachetag.namespace("default", interning = true);
}
```

Interning saves memory when many objects carry exactly the same tags. Each unique set still adds an entry to the shared registry, though, and every registration has to look its set up. Measure it on your own traffic before assuming you'll save memory. Oh and two things are the same with interning on or off:

- Objects with no tags have no overhead
- Objects with a single tag store it inline

The setting covers objects in either engine's bundled in-memory storage and in Buddy on Vinyl, plus the in-memory fallbacks a persistent namespace uses. If you're using on-disk storage, objects stored in Fellow store their tags in an attribute on the object, so interning has no effect.

`interning` is fixed when the namespace is created. Changing it means loading a new VCL, which purges every tagged in-memory object cached under the old setting (see [VCL reloads](#vcl-reloads)).

## VCL reloads

Cached objects outlive the VCL that fetched them, so cachetag has to decide what effect a reload has on cache tagging. In this section "in-memory objects" means objects whose tags cachetag keeps in its own index: everything in a memory-only namespace, plus the in-memory fallbacks in a persistent namespace. Fellow objects are covered at the end.

A memory-only namespace in the new VCL shares the old VCL's index when it has the same name and the same values for `interning`, `max_keys_per_object`, `max_key_length`, `max_tag_header_bytes`, `sweep_interval`, `purge_history_max_entries`, `sweep_batch_objects`, `sweep_batch_hold` and `sweep_batch_yield`. The old VCL must still be loaded, and its index must not have been retired (see below). Objects cached before the reload keep their tags, and a purge sent through the new VCL reaches them. The index stays in service while either VCL is warm.

When a VCL that imports cachetag becomes warm, cachetag retires every index belonging to another VCL that the new VCL doesn't share. That covers namespaces whose settings changed, namespaces that were renamed, and namespaces that were dropped. Retiring an index kills its tagged objects, as the new VCL has no tag record for them and a later tag purge would miss them. Both engines normally warm a VCL as part of `vcl.load`, so this happens before you switch to it with `vcl.use`. Expect a spike in backend traffic after a reload like this.

A retired index is sealed. A fetch still running under the old VCL can't register its object, so the object is killed and not cached. The request can still reach `vcl_deliver`; if that calls `stale()` as in the example above, it sees the object is dead and restarts instead of delivering the old response. The seal is permanent, so switching back to the old VCL with `vcl.use` doesn't bring back tagged caching.

Cachetag also kills an index's remaining tagged objects when its VCL goes cold and no warm VCL shares the index. Cachetag only hears about VCLs that import it, so if the new VCL doesn't import cachetag at all, nothing is retired when it warms. Flush or ban the old tagged objects before you activate a VCL like that. Objects cached without tags are never affected.

Fellow objects keep their tags on disk with the object, and cachetag checks them against the purge history in `persist_path`. The rules above don't apply to them, and retiring an index doesn't touch them. Keep each persistent namespace's name and `persist_path` the same across the reload, including when a VCL has several namespaces with the same name. If you rename or remove one, or change its `persist_path`, flush the affected Fellow objects before activating the new VCL.

## Purging

To purge one or more tags, send a `PURGE` request with the tags in the purge header:

```http
PURGE / HTTP/1.1
Host: www.example.com
Cache-Tag-Purge: article:123, section:news
```

To purge a single tag from VCL, call `tags.purge("article:123")`.

`purge_header()` checks every tag in the header and drops duplicates before it publishes anything, so a header with one bad tag purges nothing. Due to Cachetag's design we can't tell you how many objects were purged. The return values are:

- `-1`: success. Every tag was accepted into the purge history.
- `-2`: rejected because cachetag ran out of a resource or hit a configured limit.
- `-3`: invalid input.
- `-4`: persistence is configured but unavailable, or the write failed.

To keep purge history across a restart with Fellow, create the namespace with `persist_path` as shown in [Fellow storage](install.md#fellow-storage). Without `persist_path`, the namespace is memory-only.

When `persist_path` is set, cachetag writes purges to a write-ahead log (WAL). The call only returns success once they're on disk. All the tags in one `purge_header()` call go into a single WAL transaction with one sync, however many tags there are. After a restart, either every tag from that header is recovered or none is. `wal_fsync = grouped` is still accepted, but it behaves exactly like `strict` for now, as group commit across calls isn't implemented. Persistent namespaces write the purge history to checkpoint files and delete the WAL segments a checkpoint covers.

`purge_history_max_entries` (default 1,000,000) caps how much purge history is kept in memory. Setting it to `0` removes the cap, which you should only do on purpose.

Soft purges are supported and don't remove matching objects. They are treated as expired but stay usable for their grace and keep periods while a fresh copy is fetched:

```vcl
set req.http.purged = tags.purge_header(req.http.Cache-Tag-Purge, mode = soft);
```

## Counters

Cachetag publishes VSC counters at three levels:

- `info`: the counters to watch in production. Call volumes, rejections, persistence health and purge-map state.
- `diag`: what you'll want during an incident. The memory breakdown, resize and publication state, and the denominators behind the `info` counters.
- `debug`: implementation internals and opt-in instrumentation, for working on cachetag itself.

Watch `CACHETAG.*.transient_discards` in production: it counts objects whose pending tags were discarded at a hit-for-miss or hit-for-pass insert. It counts objects, not keys, and excludes pass fetches and fetches abandoned before insertion. A rising value points to backend freshness or VCL settings that prevent tagged responses from being cached; the backend-transaction `vmod_cachetag:` Notice identifies the discarded key count and object kind.

The interactive (curses) view of `varnishstat` or `vinylstat` filters by level and starts at `info`. One-shot (`-1`) and JSON (`-j`) output include every counter, whatever its level.

Each namespace has a background thread that publishes its counters every 0.1 s by default, so a value you read is at most one interval old. `objects()`, `pending()`, `edges()` and `compact()` publish straight away, so after a request calls one of them the counters are exact. The `vsc_publish_interval` constructor parameter changes the interval, and `0s` makes every VMOD call publish synchronously.

When two VCLs share an index after a reload, each warm VCL's namespace publishes gauges for that same index. `index_memory_bytes` and its component gauges then describe one allocation, so don't add them up across VCLs. A cold VCL can keep showing its last values until it is discarded.
