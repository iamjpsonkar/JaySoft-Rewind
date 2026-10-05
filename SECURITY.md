# Security policy

Rewind 0.1.0a1 is a local alpha for synthetic fixtures and controlled test environments. It has no production security certification or guarantee of complete secret removal.

## Report a vulnerability

Contact Jay Prakash Sonkar at [iamjpsonkar@gmail.com](mailto:iamjpsonkar@gmail.com). Include the affected commit/version, impact, and a minimal synthetic reproduction. Do not attach credentials, customer recordings, or private application data to public issues. No response-time commitment is currently offered.

## Artifact handling

- Treat recordings as sensitive application data. Default policy excludes bodies, values, and exception arguments, but metadata and permitted strings can contain sensitive information.
- Known sensitive keys are removed from supported values, headers, and URLs. This is a key-based filter, not a semantic PII detector. Binary content and arbitrary strings are not automatically anonymized.
- `CapturePolicy.synthetic()` opts into fixture content. Use only with data you control. Detected policy transformations make strict replay ineligible.
- Use a private store directory; artifacts use restrictive file permissions. No built-in encryption, identity/access service, or secure export workflow is provided. A digest detects accidental modification, not sender authenticity.
- Retention runs on save, not on a schedule. Cross-process quota coordination, directory-fsync crash durability, and background persistence are not implemented.

## Loading and replay

Inspection validates bounded JSON without loading application code. Artifacts do not select imports. Developers explicitly select the factory using `--app module:function`.

Replay executes local application code with process permissions. Never run factories from untrusted checkouts. Strict adapters reject unmatched interactions without live fallback. This does not intercept every side effect: application code can write files, access uninstrumented state, or invoke native code outside supported boundaries.

The default `python-guard` installs a Python audit hook before application import and blocks selected socket, subprocess, and native-loading events. It is not an OS sandbox or a guarantee against arbitrary native code. `adapter-only` disables that additional guard and should only be used inside an independently isolated environment.

For stronger network containment use the documented Docker `--network none` check or an independently administered equivalent. The provided container has a read-only root filesystem, dropped capabilities, and a non-root user; its temporary filesystem and ordinary process resources remain accessible. It is not a service for executing hostile code. Do not mount credentials, production configuration, or sensitive host directories into replay containers.

## Supported version policy

Fixes currently target the latest development branch and any subsequently published alpha. There is no long-term support branch. Strict replay rejects source/runtime incompatibility; regenerate controlled fixtures when upgrading application code or dependencies.
