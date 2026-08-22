# Benchmark fixtures

`cms-trace-static-v1` is reserved for an exact public, anonymised ordered CMS population. Its manifest records the reported aggregate claims but intentionally has no payload or fingerprint yet. The loader rejects it, rather than generating an approximation, until all of the following are supplied:

- the redistributable anonymised ordered object/tag payload, or a public deterministic transformation with its input and transformation hashes;
- a reviewed anonymisation and redistribution basis;
- the expected fixture and ordered-value SHA-256 fingerprints; and
- confirmation that the payload is a static regeneration-derived population, not a request-access or purge-history trace.

The small `cms-trace-static-v1-test` payload is only a deterministic loader regression fixture. It is not a sample or down-scaling of the source trace.

`generate_benchmark_fixture.py` produces the declared synthetic bound scenarios. Its output is named by the synthetic scenario and can never be published under `cms-trace-static-v1`.
