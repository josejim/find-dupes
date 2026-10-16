# Changelog

All notable changes to this project are documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project adheres to [Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-10-15

### Added

- `--comment` as the canonical flag to post comments on overlap groups
  (`--notify` is kept as an alias, so existing invocations keep working).
- `--version` flag; the version is also shown in the run banner.
- README: SmartTube case study (50 open PRs -> 5 overlap groups, 11 PRs,
  8 of the 11 flagged `CONFLICTING` on GitHub; see the correction under Fixed).
- Loud guardrail for `--comment`: dry-run first, never unattended.
- `test_v02.py`: network-free CLI contract tests, wired into CI.

### Changed

- README is now English (`README.es.md` keeps the Spanish copy).
- User-facing CLI text (help, progress, errors) translated to English.

### Fixed

- README defaults that did not match the code: `--workers` (was documented as
  10, actual default 6), cache path (`find-dupes` vs actual `find_dupes`), and
  the publish flag (README documented `--comment`, the code only had `--notify`).
- Case-study wording corrected (same day) after diff-level verification of all
  five groups: the "~22% duplicate" figure did not survive the human step — the
  actual figure is one genuine duplicate pair (2 of 50 PRs, ~4%), and the rest is
  file-level collision plus one metric false positive. Overlap is a review
  candidate, never a verdict.

## [0.1.0] - 2026-10-06

### Added

- Initial release: Jaccard-over-identifiers overlap detection for open PRs of
  any GitHub repository, diff cache, `--out` JSON export, control and
  regression test suites, CI workflow.
