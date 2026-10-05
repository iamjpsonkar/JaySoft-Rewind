# Operational alpha measurements

These local synthetic runs were collected on 2026-10-05 from source commit
[`fc5ebf5`](https://github.com/iamjpsonkar/JaySoft-Rewind/commit/fc5ebf54dd4391e7c4f4e313d037c4485218ad62)
using CPython 3.12.13 on macOS 26.6 ARM64. The measured source tree was clean.
No tests or builds were launched concurrently by this session, but the machine
was not isolated from background system activity. They are observations, not
release performance guarantees or staging evidence.

Raw reports: [forward mode order](0.1.0a2-forward.json),
[reversed mode order](0.1.0a2-reverse.json). Both include individual trials,
settings, resolved dependency versions, CPU time, drain time, memory samples,
queue accounting, and source provenance.

## Reproduce

```sh
python -m scripts.benchmark_capture \
  --iterations 200 --warmup 20 --repeats 3 --concurrency 4 \
  --payload-bytes 256 --queue-items 256 --queue-bytes 1048576 \
  --memory-iterations 30 --output /tmp/rewind-forward.json
```

Repeat with `--modes background,synchronous,discarded,disabled,baseline,storm`
to reverse the measurement order. The I/O workload is an arithmetic operation
plus a 1 ms cooperative sleep; it does not exercise a real external service.
See the [methodology](../performance.md) for closed-loop load and memory limits.

## Observed latency and completion

Ranges below span the two runs' median per-trial p50 values, in microseconds.
Each workload/mode has three trials of 200 measured operations per run.

| Workload | Baseline | Disabled | Discarded | Synchronous retention | Background retention |
| --- | ---: | ---: | ---: | ---: | ---: |
| CPU | 6.83–7.04 | 7.25–7.38 | 56.42–56.77 | 1,734.94–2,228.04 | 135.50–138.12 |
| Simulated I/O | 1,158.71–1,171.06 | 1,158.69–1,186.62 | 1,185.62–1,212.44 | 7,257.96–9,347.06 | 1,255.56–1,302.15 |

Both persistence modes saved **600 of 600 measured artifacts per workload per
run**, with no writer rejections or storage failures. The queue was intentionally
large enough to retain each measured trial, making these latency comparisons
independent of dropping recordings.

Background persistence reduced foreground waiting for disk in these samples.
It did not remove the disk work: CPU-workload throughput **including final drain**
was 384–566 operations/second in background mode and 393–563 synchronously.
The simulated-I/O equivalents were 460–557 and 390–550 operations/second.
Run-to-run variation is substantial. Capture-and-discard also has measurable
CPU overhead; no universal low-overhead percentage is established.

## Failure storm

Both gated storms attempted 258 failing captures. Each accepted 256, rejected
two at the item limit, and kept the in-flight save charged to the budget.
Peak owned payload was 344,832 bytes, below the 1,048,576-byte limit. All 256
accepted artifacts were eventually saved, the worker stopped, and pending items
and bytes returned to zero without storage failures or shutdown drops.

All storm invariants passed. These samples exercise item saturation; targeted
tests separately exercise byte saturation, all-rejected byte budgets, shutdown
drops, storage errors, and cancellation. They do not prove fixed-arrival-rate
capacity, process RSS bounds, disk-failure durability, or production readiness.
