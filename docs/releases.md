# PyPI release plan

[Documentation home](index.md) · [Quick start](getting-started.md)

Distribution: **jaysoft-rewind**. Import and command: **rewind**.
Maintainer: Jay Prakash Sonkar (`iamjpsonkar`, `iamjpsonkar@gmail.com`).
The first public alpha, [`0.1.0a2`](https://pypi.org/project/jaysoft-rewind/0.1.0a2/),
was published on 2026-10-05 from tag `v0.1.0a2` at commit
`03e85640a759cfa652eaeba3855c56481382efeb`.
The [release workflow](https://github.com/iamjpsonkar/JaySoft-Rewind/actions/runs/37321247773)
passed validation, TestPyPI upload and wheel verification, and PyPI publication.
A fresh PyPI installation verified the package version, public API imports,
maintainer metadata, and CLI. Publishing remains a separate manual workflow.

The current development candidate is `0.1.0a3`, extending the supported adapter
and tooling scope. It is not published by merging its implementation. Release
validation also gates builds on real Redis conformance and checks earlier
SQLAlchemy/redis-py/Flask families on Python 3.11.

## Release sequence

| Stage | Candidate and gate |
| --- | --- |
| First public alpha | `0.1.0a2`, published 2026-10-05 after the complete release workflow passed. Retains the local/test-environment support boundary. |
| Further alphas | `0.1.0a3`, `a4`, and so on for meaningful capability batches or alpha fixes. Record breaking changes explicitly. |
| Beta | `0.1.0b1` after public API/schema behavior settles and user reports are resolved; no calendar deadline is promised. |
| Release candidate | `0.1.0rc1` after clean installation, compatibility, offline replay, and lifecycle gates pass on supported runtimes. |
| Stable local release | `0.1.0` after those contracts are satisfied. Local stability does not certify production deployment or expand the documented database-driver scope. |
| Stable fixes/features | `0.1.1` for compatible fixes; `0.2.0` for the next planned feature/API batch. Snapshot schema compatibility is tracked separately. |

Every published version has an immutable `v<version>` Git tag pointing to a
commit already merged into `main`. `pyproject.toml`, `src/rewind/version.py`, and
the tag must match. Update the changelog, README, installation guide and
`docs/pypi.md` release labels in the release PR. Do not
backdate tags or rewrite a published version to hide a correction.

## One-time account configuration

The project is already published as `jaysoft-rewind`; the following records
document the setup used for its first release. Maintainer accounts and publisher
registrations are separate on PyPI and TestPyPI. For a new project, a pending
publisher can create the project at first publication but does not reserve its
name.
[PyPI pending-publisher documentation](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/).

Register these publisher records on their respective indexes:

| Setting | TestPyPI | PyPI |
| --- | --- | --- |
| Project | `jaysoft-rewind` | `jaysoft-rewind` |
| Repository owner | `iamjpsonkar` | `iamjpsonkar` |
| Repository | `JaySoft-Rewind` | `JaySoft-Rewind` |
| Workflow filename | `release.yml` | `release.yml` |
| GitHub environment | `testpypi` | `pypi` |

Create the two GitHub environments and restrict deployment to release tags
matching `v*`. The workflow validates that the tag commit is on `main` too.
Choose environment reviewer rules according to maintainer preference; the
manual workflow dispatch is already an explicit release action. These account
and environment settings are not configured merely by committing YAML.
[Publisher configuration](https://docs.pypi.org/trusted-publishers/adding-a-publisher/).

The workflow uses short-lived OIDC credentials, with `id-token: write` limited
to the upload jobs. No PyPI API-token secret is needed. TestPyPI and PyPI use
separate publisher registrations and upload endpoints.
[Trusted publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/).

## Implemented workflow

`.github/workflows/release.yml` runs only through **Actions → Package release →
Run workflow**, selecting the version tag. Ordinary pushes, PRs, and tag creation
do not upload anything.

With `publish=false` (default), it validates tag/source/main ancestry, runs tests,
lint and type checks on Python 3.11/3.12, checks the offline Docker examples,
builds one wheel and one source archive, verifies their contents and isolated
wheel import, and runs `twine check --strict`. It retains the distributions as
a seven-day workflow artifact. No upload job executes.

After inspecting that rehearsal, dispatch the same unchanged tag with
`publish=true`. The workflow repeats the gates, builds the distributions once,
uploads them to TestPyPI, downloads that exact wheel and compares its bytes,
installs it without the source tree, then uploads the **same built artifacts**
to PyPI. The upload jobs do not check out or build source code.

The TestPyPI installation uses `--no-deps` because the core has no runtime
dependencies. Optional integrations are tested earlier from the normal package
index. This avoids treating TestPyPI as a complete dependency mirror or mixing
indexes for dependency resolution.
[TestPyPI installation guidance](https://packaging.python.org/en/latest/guides/using-testpypi/).

The first publication verified the OIDC exchange on both indexes. Its initial
TestPyPI attempt failed because no matching publisher existed; after registering
the separate TestPyPI publisher with environment `testpypi`, **Re-run failed jobs**
completed publication using the original build artifacts.

## Release operation and recovery

1. Finish and merge the release PR; inspect current Python and offline CI.
   Review `docs/pypi.md`, which supplies the package description through
   `pyproject.toml`. Update its install commands and version status for the
   candidate being published, along with the README and installation guide.
   The build checks verify that the guide is included in the source archive and
   embedded in both distributions. GitHub documentation updates immediately
   after merge; an existing PyPI release keeps its uploaded description.
2. Create and push the matching tag when ready to release. Run the default
   non-publishing rehearsal and inspect its artifacts and rendered metadata.
3. Run the publishing workflow once. If TestPyPI propagation or a later job
   fails, use **Re-run failed jobs** so already-uploaded versions are not rebuilt
   or uploaded again. Keep the original build artifact available.
4. Verify the public project metadata, maintainer, license, supported runtimes,
   and a clean `pip install --no-deps jaysoft-rewind==<version>`. Verify an
   optional extra separately. Add a GitHub release with the matching changelog
   and mark alpha/beta/rc versions as prereleases.
5. If a published build is defective, diagnose before the next upload. Prefer
   a new version and a documented correction. Consider yanking the defective
   version rather than deleting history; yanking does not guarantee users
   pinned to that exact version stop receiving it.
   [PyPI yanking behavior](https://docs.pypi.org/project-management/yanking/).

Upload retries must use the original artifacts. The workflow does not suppress
existing-file errors: rebuilding an already uploaded version can produce
different archive bytes. If the original artifact is gone or a source change
is needed, advance the version and start with a new tag.

Local preparation commands:

```sh
python -m pip install -e '.[dev,release]'
python scripts/check_release.py --tag v0.1.0a3 --ref-type tag
python -m build --outdir dist/release-check
python scripts/verify_distribution.py dist/release-check
python -m twine check --strict dist/release-check/*
```

Use a clean output directory per version. Keep real recordings, environment
files, and credentials out of distributions. For each new version, complete a
successful dry run before deliberately dispatching publication.
