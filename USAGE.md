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

To purge one or more tags, send the purge endpoint a matching request header:

```http
PURGE / HTTP/1.1
Host: www.example.com
Cache-Tag-Purge: article:123, section:news
```

`purge_header()` returns `-1` only after every tag has an accepted purge-history publication, `-2` for resource or configured-limit rejection, `-3` for invalid input, and `-4` when required persistence is unavailable or fails. It never reports an affected-object count. Input is fully validated before publication starts. Publication is then sequential because a durable WAL record cannot be rolled back: if a later tag fails, earlier tags remain durably purged and the call returns `-4`. This is fail-closed over-invalidation, not a successful partial result. For a single tag, call `tags.purge("article:123")`. When `persist_path` is configured, every purge in a successful call is durable before success is returned.

`wal_fsync = grouped` is retained as a compatibility spelling but currently has the same durability behavior as `strict`: each accepted purge is synced before success. Real bounded-window group commit is not implemented. Persistent namespaces checkpoint purge history as immutable streamed generations and collect checkpoint-covered WAL segments. The default `purge_history_max_entries = 1000000` bounds the resident history; set it to `0` only when deliberately choosing unbounded retention.

Soft purging keeps the object available for grace and keep while forcing a refresh:

```vcl
set req.http.purged = tags.purge_header(req.http.Cache-Tag-Purge, mode = soft);
```

Cachetag uses one purge-map model. It stores namespace-qualified purged-tag digests and snapshots the current purge sequence during registration. A purge before registration does not invalidate a later object; a purge after registration is detected by the insert probe and later `stale()` checks.

With Fellow persistence, complete object membership is stored only in a checksummed variable-length FDO attribute. Restart replays purge history; cachetag performs no per-object resurrection and builds no object-side index for Fellow objects. A hit materializes normal Fellow metadata, validates the attribute, and probes serialized folds directly against the resident purge map. Even after every object has been touched, Fellow-direct membership does not populate cachetag's volatile object, edge, or fold tables.

The `stale()` checks in `vcl_hit` and `vcl_deliver` are part of the hard-purge pattern. They catch objects invalidated after registration, including fetch races and objects that remain physically resident, then restart so the request can fetch fresh content.

For persistent cache tags with Fellow, create the namespace with `persist_path` as shown in [Fellow / Slash Integration](#fellow--slash-integration). Without `persist_path`, the namespace is memory-only.
