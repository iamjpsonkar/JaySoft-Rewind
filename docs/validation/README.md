# Integrated 0.2.0a1 staging evidence

The [integrated report](alpha2-integrated-2026-10-05.json) passed every predeclared
synthetic budget and rollback check at clean revision
`3f819bf4ce05b0718a1815b93041d00ed23bab1a`. Both imported source and installed package
metadata report `0.2.0a1`. Each mode offered 100 requests at 50 requests/second;
memory uses a separate fresh process. Host-specific absolute paths are omitted.

| Mode | Accepted-request p95 ms | Independent RSS peak MiB | Budgets |
| --- | ---: | ---: | --- |
| active | 16.329 | 42.77 | passed |
| background | 13.273 | 42.86 | passed |
| direct | 9.207 | 42.39 | passed |
| disabled | 10.727 | 42.83 | passed |
| retained | 19.146 | 43.16 | passed |

The lifecycle drill passed all checks; accepted work drained in
14.487 ms. These are synthetic example budgets on a shared
host, not production SLOs. Other final checks ran concurrently; inspect the profile
and raw report before comparing measurements.

Final local integration: **626 passed, 28 skipped** in the default test suite.
Opt-in service tests run separately: **28 PostgreSQL/MySQL cases passed** locally;
CI additionally passed Redis and Kafka/Celery service conformance. Counts overlap.
All **nine** Docker examples reproduced with networking disabled, including the
server handler and messaging workflows. Ruff and mypy passed.

## Earlier baseline


The [recorded run](local-staging-2026-10-05.json) passed the predeclared
[local staging profile](local-staging.json) on 2026-10-05. This is a local synthetic
exercise, not a production SLO result or customer-data approval. The workload and
measurement boundaries are explained in [deployment validation](../deployment-validation.md).

The profile, tooling, and thresholds were committed before measuring at
`fb3853b8232c41679de25414e71b15861bb98cfb`. The report records a clean worktree at
startup, the exact profile, hashes of the script and imported package source tree,
and distinct worker process IDs. Measurements ran from 16:11:09 through 16:11:25 UTC.
The imported source version was `0.1.0a3`; installed distribution metadata still
reported `0.1.0a2`. The source hash and Git revision identify the tested code rather
than treating that older editable-install metadata as the code version.

The host was a shared development machine without CPU pinning or traffic isolation.
Other development checks were running, including a regression suite during part of
this run. Mode ordering, host load, and the small sample size can affect the results;
the lower active-mode p95 does not establish a speedup from recording.

Each timing pass offered 100 requests at 50 requests/second. Every mode completed
all 100 without application errors or request admission rejections. Separate fresh
processes measured memory using 20 requests per mode.

| Mode | End-to-end p95 ms | Scheduling lag p95 ms | Memory-process RSS peak MiB | Traced peak MiB | Timing artifacts saved |
| --- | ---: | ---: | ---: | ---: | ---: |
| direct | 6.873 | 1.613 | 39.25 | 0.112 | 0 |
| disabled | 6.897 | 2.506 | 41.36 | 0.113 | 0 |
| active, discarded | 4.962 | 1.099 | 42.69 | 0.155 | 0 |
| retained, synchronous | 11.306 | 1.670 | 42.67 | 0.195 | 100 |
| background | 6.679 | 1.139 | 42.89 | 0.191 | 100 |

The background timing pass rejected no snapshots and measured 0.085 ms p95 from
submission to store entry. All declared numeric budgets and accounting invariants
passed. Raw values, including the independent memory-pass counts, are in the JSON.

All 19 lifecycle-drill assertions passed. The blocked store exercised an active
write, reached the 16-item queue limit with 93,561 serialized bytes reserved, and
rejected three new snapshots without changing application results. Releasing the
store drained the 16 accepted writes in 15.763 ms. Re-enabling capture admitted one
additional write; shutdown finished with all 17 accepted writes saved, no failed or
discarded writes, zero pending bytes, and no live writer thread. Disabled and
closed-wrapper calls, the active request crossing disable, and direct rollback
all matched the baseline application result.

Reproduce with the original profile; preserve a separate report for each run:

```sh
python scripts/validate_deployment.py \
  --profile docs/validation/local-staging.json \
  --output /tmp/rewind-local-staging.json
```

The report's acceptance claim is limited to these explicit example budgets,
synthetic adapters/services, source revision, and observed host conditions.
