# BR-020: Respect the demonstrated storage envelope and residency validity

**Rule:** Outside deliberate eviction/pressure lanes, a run where loaded objects
did not stay resident until their purge (unexpected LRU nukes or expiry) is a
failed run, not a noisy datapoint. Size storage within the demonstrated
envelope: on the `51.159.203.250`-class host, Buddy 10M is valid at 32g and
fails at 64g (`buddywhen_mmap` `ENOMEM` at child startup). The `eviction`
profile is a cost measurement only — invalid for memory-efficiency claims.

**Why:** Silent early eviction changes both the memory curve and the purge work
under measurement. The 64g startup failure and silent early-eviction incidents
established both constraints.

**Comply by:** Validating residency (`BENCH_VALIDATE_RESIDENCY`) or asserting
`n_lru_nuked=0` before purge in non-pressure lanes; recording any new envelope
finding in the campaign note and this rule.

**Tripwire:** Partial — the `eviction` profile fails unless nukes prove eviction;
non-pressure lanes rely on residency validation being enabled.
