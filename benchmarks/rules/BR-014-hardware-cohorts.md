# BR-014: Never compare rows across hardware cohorts or host rebuilds

**Rule:** Do not combine or compare medians across different CPU model/count,
RAM, kernel, Docker version, or governor settings — and treat a host rebuild
mid-campaign as a cohort change, even at the same IP.

**Why:** Identical code measured 86.1 s vs 74.2 s (a 14% swing) across two
sessions when the rented host was rebuilt mid-day on 2026-07-03, poisoning the
Proposal 4+6 attribution (`devdocs/docs/archived/churn-benchmark-determinism-plan-2026-07-04.md`).

**Comply by:** Rerunning both arms in one session on one host for any comparison;
checking the summarizer's hardware fingerprint groups before combining rows.

**Tripwire:** Implemented — the summarizer groups results by hardware fingerprint
and warns when the fingerprint changes across comparison arms.
