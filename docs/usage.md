# Using cachetag

The usual pattern is to have the backend application return a response header listing the cache tags for the object, then teach Vinyl Cache VCL to register those tags while the object is being fetched. The header name is up to you; this example uses `Cache-Tag` for backend responses and `Cache-Tag-Purge` for purge requests.

Example backend response:

```http
HTTP/1.1 200 OK
Cache-Control: max-age=3600
Cache-Tag: article:123, author:42, section:news
```

Example VCL:

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

`add_header()` splits on commas by default, trims tokens, and rejects tokens containing embedded whitespace. If your application already emits a different separator, pass it explicitly, for example `tags.add_header(beresp.http.Cache-Tag, sep = " ")`; using comma-separated tags avoids ambiguity in tag names.

Registration limits fail closed. A namespace accepts at most `max_keys_per_object` unique tags per object (default 512, sized from production traces showing real 333-tag objects) and at most `max_tag_header_bytes` per header value (default 16 KiB). Exceeding either limit during a fetch **fails that fetch** (the client sees a 503 and the namespace's `limit_rejections` counter increments); tags are never silently dropped, because caching an object with a truncated tag set would let a later purge miss it. There is no separate per-header token ceiling — raise `max_keys_per_object` explicitly if your workload carries more tags per object.

By default, each volatile object with multiple tags stores its own compact membership vector. A namespace can instead canonicalise complete membership sets:

```vcl
sub vcl_init {
    new tags = cachetag.namespace("default", interning = true);
}
```

`interning` is fixed when the namespace is created. To change it you load a new VCL, and that purges every tagged in-memory object cached under the old setting (see [VCL reloads](#vcl-reloads)). Interning can reduce retained membership memory when many objects have exactly the same complete tag set, but unique sets add registry overhead and lookup work. Measure your workload rather than assuming it is a saving. Zero tags create no volatile membership, and one tag stays inline in either mode.

The option affects Default and Buddy memberships, plus transient volatile fallbacks in a persistent namespace. Fellow-direct objects continue to store and probe membership in their FDO attributes, so their representation is unchanged. Both modes are compiled into every VMOD build; this is a VCL choice, not a configure option or a separate binary.

### VCL reloads

Cached objects outlive the VCL that fetched them, so cachetag has to decide what a reload does to their tags. In this section "in-memory objects" means objects whose tags cachetag keeps in its own index: everything in a memory-only namespace, plus the transient fallbacks in a persistent namespace. Fellow objects are covered at the end.

A memory-only namespace in the new VCL shares the old VCL's index when it has the same name and the same values for `interning`, `max_keys_per_object`, `max_key_length`, `max_tag_header_bytes`, `sweep_interval`, `purge_history_max_entries`, `sweep_batch_objects`, `sweep_batch_hold` and `sweep_batch_yield`. The old VCL must still be loaded, and its index must not have been retired (see below). Objects cached before the reload keep their tags, and a purge sent through the new VCL reaches them. The index stays in service while either VCL is warm.

When a VCL that imports cachetag becomes warm, cachetag retires every index belonging to another VCL that the new VCL doesn't share. That covers namespaces whose settings changed, namespaces that were renamed, and namespaces that were dropped. Retiring an index kills its tagged objects, because the new VCL has no tag record for them and a later tag purge would miss them. Vinyl normally warms a VCL as part of `vcl.load`, so this happens before you switch to it with `vcl.use`. Expect a spike in backend traffic after a reload like this.

A retired index is sealed. A fetch still running under the old VCL can't register its object, so the object is killed and not cached. The request can still reach `vcl_deliver`; if that calls `stale()` as in the example above, it sees the object is dead and restarts instead of delivering the old response. The seal is permanent, so switching back to the old VCL with `vcl.use` doesn't bring back tagged caching. Load a fresh copy of that VCL instead.

Cachetag also kills an index's remaining tagged objects when its VCL goes cold and no warm VCL shares the index. Cachetag only hears about VCLs that import it, so if the new VCL doesn't import cachetag at all, nothing is retired when it warms. Flush or ban the old tagged objects before you activate a VCL like that. Objects cached without tags are never affected.

Fellow objects keep their tags on disk with the object, and cachetag checks them against the purge history in `persist_path`. The index rules above don't apply to them, and retiring an index doesn't touch them. Keep each persistent namespace's name and `persist_path` the same across the reload, including when a VCL has several namespaces with the same name. If you rename or remove one, or change its `persist_path`, flush the affected Fellow objects before activating the new VCL.

To purge one or more tags, send the purge endpoint a matching request header:

```http
PURGE / HTTP/1.1
Host: www.example.com
Cache-Tag-Purge: article:123, section:news
```

`purge_header()` returns `-1` only after every tag has an accepted purge-history publication, `-2` for resource or configured-limit rejection, `-3` for invalid input, and `-4` when required persistence is unavailable or fails. It never reports an affected-object count. Input is fully validated and deduplicated before publication starts. A successful multi-tag header is one durable WAL transaction, so a restart recovers every tag from the header or none of them; it uses one sync regardless of the number of tags. For a single tag, call `tags.purge("article:123")`. When `persist_path` is configured, every purge in a successful call is durable before success is returned.

`wal_fsync = grouped` is retained as a compatibility spelling but currently has the same durability behavior as `strict`: each accepted purge transaction is synced before success. `purge_header()` is one transaction, while real bounded-window group commit across calls is not implemented. Persistent namespaces checkpoint purge history as immutable streamed generations and collect checkpoint-covered WAL segments. The default `purge_history_max_entries = 1000000` bounds the resident history; set it to `0` only when deliberately choosing unbounded retention.

Soft purging keeps the object available for grace and keep while forcing a refresh:

```vcl
set req.http.purged = tags.purge_header(req.http.Cache-Tag-Purge, mode = soft);
```

Cachetag uses one purge-map model. It stores namespace-qualified purged-tag digests and snapshots the current purge sequence during registration. A purge before registration does not invalidate a later object; a purge after registration is detected by the insert probe and later `stale()` checks.

With Fellow persistence, complete object membership is stored only in a checksummed variable-length FDO attribute. Restart replays purge history; cachetag performs no per-object resurrection and builds no object-side index for Fellow objects. A hit materializes normal Fellow metadata, validates the attribute, and probes serialized folds directly against the resident purge map. Even after every object has been touched, Fellow-direct membership does not populate cachetag's volatile object, edge, or fold tables.

The `stale()` checks in `vcl_hit` and `vcl_deliver` are part of the hard-purge pattern. They catch objects invalidated after registration, including fetch races and objects that remain physically resident, then restart so the request can fetch fresh content. The second call for the same object within one request is answered from a per-request memo while the namespace's fully published purge-map sequence has not moved; a purge or kill between the two calls forces a full recheck. `stale_calls` counts every call either way, and `stale_memo_hits` reports how many calls the memo answered.

Cachetag's VSC counters are published at three levels. `info` counters are the production contract worth watching routinely: call volumes, rejection counts, persistence health, and purge-map state. `diag` counters are what you reach for during an incident: memory decomposition, resize and publication state, and the denominators behind the `info` counters. `debug` counters are implementation internals and opt-in instrumentation, useful when working on cachetag itself rather than operating it. `vinylstat`'s interactive (curses) view filters by level and starts at `info`; one-shot (`-1`) and JSON (`-j`) output always report every counter regardless of level.

When two VCLs share an index after a reload, each warm VCL's namespace publishes gauges for that same index. `index_memory_bytes` and its component gauges then describe one allocation, so don't add them up across VCLs. A cold VCL can keep showing its last values until it is discarded.

Those counters reach `vinylstat` on a cadence: a per-namespace background thread publishes them every 0.1 s by default, so a value you read is at most one tick old. `objects()`, `pending()`, `edges()` and `compact()` publish immediately, so a request that calls one of them leaves an exact snapshot behind. The `vsc_publish_interval` constructor parameter changes the cadence, and `0s` restores synchronous publishing on every VMOD call.

For persistent cache tags with Fellow, create the namespace with `persist_path` as shown in [Fellow / Slash Integration](#fellow--slash-integration). Without `persist_path`, the namespace is memory-only.
