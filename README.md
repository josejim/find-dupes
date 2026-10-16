# find-dupes

**Find overlapping (possibly duplicate) PRs in any GitHub repository.**

Large repos accumulate duplicate work nobody notices: several people solve the
same bug in parallel because they can't see what the others are doing. This
script compares the diffs of open PRs and flags pairs with high overlap, so a
human can decide whether it's worth acting on.

**It does not judge redundancy — it only measures similarity. The decision is human.**

## Requirements

- Python 3.10+
- [GitHub CLI](https://cli.github.com/) (`gh`), authenticated

No Python dependencies. Stdlib only.

## Install

```bash
git clone https://github.com/josejim/find-dupes.git
cd find-dupes
chmod +x find_dupes.py
```

## Usage

```bash
# Analyze the 120 most recent open PRs of a repo
./find_dupes.py --repo django/django

# More PRs, more workers
./find_dupes.py --repo psf/requests --limit 300 --workers 12

# Stricter threshold (more similarity required)
./find_dupes.py --repo pallets/flask --threshold 0.25

# Only count certain file types as code (ignores docs, tests, CI)
./find_dupes.py --repo python/cpython --code-ext py

# No cache (always re-download)
./find_dupes.py --repo rust-lang/rust --no-cache

# See what would be commented, without posting anything
./find_dupes.py --repo owner/name --comment --dry-run
```

### Options

| Flag | Default | Description |
|---|---|---|
| `--repo` | `django/django` | Repository as `owner/name` |
| `--limit` | `120` | How many of the most recent open PRs to analyze (more = slower) |
| `--threshold` | `0.18` | Minimum identifier similarity (0–1) to flag a pair |
| `--workers` | `6` | Parallel diff downloads |
| `--cache` / `--no-cache` | cache on | Diff cache at `~/.cache/find_dupes/prs.json` (respects `$XDG_CACHE_HOME`; TTL 6 h) |
| `--code-ext` | `py,ts,tsx,js,jsx,rs,go,java,rb,sh,c,cc,cpp,h,hpp,php,swift,kt,scala` | Extensions that count as code |
| `--out` | — | Write the detected group numbers as JSON to a file |
| `--comment` (alias: `--notify`) | off | Post a comment on the oldest PR of each group |
| `--dry-run` | off | With `--comment`, print the comments instead of posting them |
| `--version` | — | Show version |

### ⚠️ The `--comment` guardrail

Comments are public actions on other people's PRs. Run `--comment --dry-run`
first, read the output, and only then post. **Never run `--comment` unattended
from automation.** The tool never claims "this is a duplicate" — it claims
"these overlap", which anyone can verify in 30 seconds by reading the PRs.

## How it works

1. **Fetches** the diffs of the N most recent open PRs via `gh api`.
2. **Extracts identifiers** from each diff: function names, variables,
   constants, file paths — ignoring formatting (comments, imports, boilerplate).
3. **Computes Jaccard** similarity between pairs of PRs that share at least
   one code file.
4. **Filters** pairs below the threshold.
5. **Groups** connected pairs (Union-Find) and reports clusters.

### Why Jaccard on identifiers, not text

Two PRs can fix the same bug with completely different code but the same
functions, variables and paths. Jaccard over identifiers captures the
**concept**, not the syntax.

### Why it doesn't judge redundancy

A pair at 80 % similarity can be:

- a real duplicate (two people, same approach)
- complementary work (one refactors, the other adds a feature)
- coincidence (same domain, different problem)

The script flags. The human decides.

## Case study: SmartTube (October 2026)

Run against the open PR queue of [SmartTube](https://github.com/yuliskov/SmartTube),
a busy Android TV media client:

```
50 open PRs analyzed  ->  5 overlap groups, 11 PRs flagged for review
                         (diff review: 1 genuine duplicate pair)
```

What the groups looked like, *after reading the diffs*:

- **one genuine competitive pair** — two voice-over implementations
  (#5817, #6265) that add the *same new file paths*: only one can ever merge
- **one complementary pair** — two subtitle reworks (#5839, #6272) that stack as
  features but touch the same files, so merge order matters
- **three groups that are not duplicates at all** — three playlist features
  (#5790, #5791, #6162) and two unrelated settings features (#5437, #5495) that
  merely collide on shared UI files and on 46-locale `strings.xml` churn; the
  similarity score is dominated by that churn
- two parallel player-error fixes (#6122, #6188) — a third (#6295) was later
  applied by the maintainer by hand

So: 2 of 50 PRs (~4 %) were genuinely duplicate work; 8 of the 11 grouped PRs are
flagged `CONFLICTING` on GitHub, which is the *real* pattern — independent features
colliding on the same hot files, not people solving the same problem twice.
Clusters are a watch-list, never a verdict.

## Tests

```bash
python3 test_find_dupes.py      # control: known-positive / known-negative PR sets
python3 test_regressions.py     # regression tests for previously fixed bugs
python3 test_v02.py             # v0.2 CLI contract (network-free)
```

The control and regression suites require an authenticated `gh` and network
access; the repo under test defaults to `django/django` and can be overridden
with `DUPE_REPO=owner/name`.

## Use cases

- **Maintainers**: catch duplicates before reviewing
- **Contributors**: avoid working on something someone else already is
- **Researchers**: measure the duplication rate of a repo

## Contributing

Issues and PRs welcome. Tests must pass before merging.

## License

MIT. See [LICENSE](LICENSE).
