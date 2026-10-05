# Repository checks and protected main

[Documentation home](index.md)

`main` requires a pull request, up-to-date source branch and successful
`python (3.11)`, `python (3.12)`, `offline-replay`, and `redis-conformance` checks.
The rule applies to administrators. Force pushes and deleting main are disabled;
review conversations must be resolved before merging.

The solo-maintainer workflow requires zero external approvals, so the maintainer
can merge a tested PR without requiring another account. This does not bypass CI.
The reviewable API configuration is in
[.github/branch-protection.json](../.github/branch-protection.json).

Work uses isolated feature branches and genuine commits with meaningful messages.
Merge only tested heads, then delete fully integrated remote feature branches.
Published version tags and PyPI files remain immutable.
