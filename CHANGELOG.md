# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-11-12

First release.

### Added

- `prsieve check`: triage one branch against its base and other open branches.
- `prsieve queue`: check every pair of open branches and suggest a merge order.
- `prsieve github`: read pull requests from the GitHub REST API, for GitHub Actions or a local clone.
- `prsieve rules`: list the rules or explain one.
- Rules PS100, PS101, PS102 (collisions), PS200 (formatting churn), PS300, PS301, PS302,
  PS303 (description claims), PS400 (automation files) and PS500 (size).
- Text, JSON, Markdown and GitHub annotation output.
- A composite GitHub Action with annotations and a job summary.

[0.1.0]: https://github.com/kirtanbhatt10/prsieve/releases/tag/v0.1.0
