# Your first replay

[Documentation home](index.md) · [Installation](installation.md) · [CLI reference](cli.md)

This walkthrough uses a synthetic provider response with a missing field. It needs no credentials or external service. Complete the source installation with `.[dev]` and run the commands from the repository root.

## 1. Capture the failure

```sh
artifact="$(python -m examples.http_failure)"
```

The example calls a mocked provider and expects `payment_id`. The fixture omits that field, so the application raises `KeyError`. Rewind captures the request, response and failure. The example catches the error and prints the new recording path; the shell stores it in `artifact`.

The file lives under `.rewind/demo/` and has a generated ID. Keep the same shell for the next commands. On PowerShell, use the [equivalent commands](installation.md#windows-powershell).

## 2. Inspect the recording

```sh
rewind list --store .rewind/demo
rewind inspect "$artifact"
```

Look for `complete: true`, one interaction and an `exception` outcome. Inspection validates and summarizes data without importing the application. The fixture uses `CapturePolicy.synthetic()` because its data is known to be synthetic; default capture policy is more restrictive.

## 3. Reproduce it in a fresh process

```sh
rewind replay "$artifact" --app examples.http_failure:replay_target
```

The `--app` argument selects a local factory that returns a `ReplayTarget`: the configured Rewind instance and the application entry point. Replay consumes the recorded HTTP response, rather than asking the original provider.

Expected report:

```json
{
  "status": "reproduced",
  "detail": "recorded interactions and outcome matched",
  "consumed": 1,
  "total": 1,
  "isolation": "python-guard"
}
```

Exit code `0` means the original failure reproduced. It does not mean the application bug is fixed. See [result meanings](cli.md#results-and-exit-codes) if your output differs.

## 4. Keep a test

```sh
rewind test "$artifact" --app examples.http_failure:replay_target --output test_reproduction.py
python -m pytest test_reproduction.py
```

This creates `test_reproduction.py` and a neighboring recording fixture. Existing files are never overwritten, so choose a new output filename when repeating this step. The generated test calls the local factory and checks that the recorded failure reproduces. The application module and compatible dependencies must remain available.

When changing application code, strict replay rejects the changed source fingerprint. Development `0.1.0a3` provides [explicit comparison](comparison.md) and developer-supplied expected outcomes. Rewind cannot infer your application's correct business result from a failure.

## 5. Share a recording

This step requires development `0.1.0a3`:

```sh
rewind export "$artifact" -o failure.rewind
rewind import failure.rewind --store .rewind/imported
rewind list --store .rewind/imported
```

Review recordings before sharing. The archive contains the recording and checksum metadata, not application source or dependencies. Import validates the archive and refuses to replace an existing snapshot ID. Use a new archive name or destination store when repeating the walkthrough.

## 6. Choose your next guide

| Next goal | Continue with |
| --- | --- |
| Add capture to a real entry point | [HTTPX and FastAPI](http-and-fastapi.md) |
| Try database, Redis and HTTP together | [Combined example](../examples/combined_failure.py) |
| Reproduce time and random values | [Sources](sources.md) |
| Read execution diagnostics | [Tracing](tracing.md) |
| Verify network-disabled replay | Run `./scripts/verify_offline.sh`; see [security](../SECURITY.md) |
| Understand a failed replay | [Troubleshooting](troubleshooting.md) |
