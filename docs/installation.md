# Installation

[Documentation home](index.md) · [Next: your first replay](getting-started.md)

Use Python **3.11 or 3.12**. The package name is **`jaysoft-rewind`**; import it as `rewind` and run the `rewind` command.

## Choose a release or source checkout

| Version | Where to get it | What it includes |
| --- | --- | --- |
| `0.1.0a2` | Previous alpha | Async HTTPX, FastAPI/ASGI, explicit basic sources, background recording, core CLI and reproduction tests |
| `0.2.0a2` | Release covered by these guides | Adds conditional function/class decorators and direct replay without an application factory |
| `0.2.0a1` | Earlier alpha | Adds external databases, messaging, filesystem/S3, storage protection and deployment validation |
| `0.1.0a3` | Earlier alpha | Adds sync/Flask, SQLAlchemy/SQLite, Redis, tracing, environment observations, archives and changed-code comparison |

### Published package

In an activated virtual environment:

```sh
python -m pip install 'jaysoft-rewind==0.2.0a2'
python -m rewind --version
```

Install the integration you need instead of the core-only command:

```sh
python -m pip install 'jaysoft-rewind[httpx]==0.2.0a2'
# Or, for FastAPI:
python -m pip install 'jaysoft-rewind[fastapi]==0.2.0a2'
```

The wheel contains the library and CLI. The `examples.*` commands in the walkthrough require a repository checkout.

### Source checkout on macOS or Linux

```sh
git clone https://github.com/iamjpsonkar/JaySoft-Rewind.git
cd JaySoft-Rewind
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
rewind --version
```

Check `python3 --version` before creating the environment; select `python3.11` or `python3.12` if your default is different. `dev` installs the optional integrations and development tools used by the examples.

### Windows PowerShell

```powershell
git clone https://github.com/iamjpsonkar/JaySoft-Rewind.git
cd JaySoft-Rewind
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -e '.[dev]'
.venv\Scripts\python.exe -m rewind --version
```

Using the environment's interpreter directly avoids depending on shell activation settings. The following translates the HTTP walkthrough:

```powershell
$artifact = .venv\Scripts\python.exe -m examples.http_failure
.venv\Scripts\python.exe -m rewind inspect $artifact
.venv\Scripts\python.exe -m rewind replay $artifact --app examples.http_failure:replay_target
```

These are shell equivalents; current automated conformance evidence is Linux CI and local macOS. The [support matrix](support-matrix.md) describes the tested boundaries.

## Optional integrations

For a PyPI installation, extras use the same version pin, for example:

```sh
python -m pip install 'jaysoft-rewind[all]==0.2.0a2'
```

For source development, choose one of these instead of `dev`:

| Install command | Purpose |
| --- | --- |
| `python -m pip install -e .` | Core library and CLI, with no mandatory third-party runtime dependencies |
| `python -m pip install -e '.[httpx]'` | Sync/async HTTPX |
| `python -m pip install -e '.[fastapi]'` | FastAPI and HTTPX |
| `python -m pip install -e '.[flask]'` | Flask/WSGI and HTTPX |
| `python -m pip install -e '.[sqlalchemy]'` | SQLAlchemy with the SQLite adapter |
| `python -m pip install -e '.[redis]'` | redis-py sync/async adapters |
| `python -m pip install -e '.[all]'` | All supported optional integrations |

Extras install libraries. Your application still needs to configure Rewind adapters. For an example that invokes `pytest`, install `dev` or provide pytest separately.

## Verify the environment

```sh
python -m rewind --version
python -m pip check
# Available in 0.1.0a3:
python -m rewind doctor
```

If a command is missing, check the installed version and active environment first. [Troubleshooting](troubleshooting.md) covers common setup problems.

New optional extras: `postgres` (psycopg 3), `mysql` (PyMySQL), `kafka`
(kafka-python), `celery`, `s3` (boto3), and `encryption` (cryptography).
These do not install or start database servers, message brokers or AWS services.
