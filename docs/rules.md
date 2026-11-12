# Rules

Every rule is deterministic: the same commits and the same description always give the same findings.
Run `prsieve rules` for this list in your terminal, or `prsieve rules PS100` for one rule.

| Rule | Name | Severity | What it means |
| --- | --- | --- | --- |
| PS100 | conflicts-with-open-pr | medium | This pull request and another open one cannot both merge as they stand. |
| PS101 | shares-files-with-open-pr | info | Another open pull request edits the same files, but the two merge cleanly. |
| PS102 | conflicts-with-base | medium | The branch no longer merges cleanly into its base branch. |
| PS200 | formatting-churn | medium | Most changed lines in a file are re-formatted or moved, not changed. |
| PS300 | docs-only-claim | high | The pull request says it is documentation only but changes other files. |
| PS301 | no-dependency-claim | medium | The pull request says it adds no dependencies but edits a dependency manifest. |
| PS302 | test-count-claim | low | The description promises more new tests than the diff adds. |
| PS303 | unchecked-checklist | info | The description still has unchecked checklist items. |
| PS400 | changes-automation | medium | The pull request changes CI, action or ownership files. |
| PS500 | oversized | low | The pull request is larger than most reviewers can check carefully. |

## PS100 conflicts-with-open-pr

**Severity:** medium

This pull request and another open one cannot both merge as they stand.

**Why it matters.** A trial merge of the two branches (git merge-tree) ends in a conflict. Each one looks mergeable on its own, so the problem only shows up after the first is merged.

**What to do.** Decide the order now. Merge one, then ask the other author to rebase, or ask one of them to drop the overlapping change.

## PS101 shares-files-with-open-pr

**Severity:** info

Another open pull request edits the same files, but the two merge cleanly.

**Why it matters.** Clean text merges can still disagree in meaning, for example two edits to one function.

**What to do.** Read the two diffs side by side for the shared files before merging the second one.

## PS102 conflicts-with-base

**Severity:** medium

The branch no longer merges cleanly into its base branch.

**Why it matters.** The base branch moved on after this branch was created.

**What to do.** Ask the author to rebase or merge the base branch.

## PS200 formatting-churn

**Severity:** medium

Most changed lines in a file are re-formatted or moved, not changed.

**Why it matters.** Whitespace, quote style, table padding and re-ordered lines inflate the diff, hide the real change and collide with every other open pull request that touches the file.

**What to do.** Ask for the formatting to be split out, or dropped if the project has no formatter.

## PS300 docs-only-claim

**Severity:** high

The pull request says it is documentation only but changes other files.

**Why it matters.** Reviewers skim documentation changes. A description that undersells the diff is the easiest way for an unreviewed code change to get merged.

**What to do.** Review the listed files as code, or ask the author to correct the description.

## PS301 no-dependency-claim

**Severity:** medium

The pull request says it adds no dependencies but edits a dependency manifest.

**Why it matters.** Manifests and lockfiles decide what gets installed and run.

**What to do.** Open the manifest diff and confirm nothing new is pulled in.

## PS302 test-count-claim

**Severity:** low

The description promises more new tests than the diff adds.

**Why it matters.** The count is matched against added test definitions in common frameworks, so treat it as a prompt to look, not as proof.

**What to do.** Check the test files, then ask the author to fix the description or add the tests.

## PS303 unchecked-checklist

**Severity:** info

The description still has unchecked checklist items.

**Why it matters.** The author may not have finished the steps the project asks for.

**What to do.** Ask about the open items before reviewing in depth.

## PS400 changes-automation

**Severity:** medium

The pull request changes CI, action or ownership files.

**Why it matters.** Workflows, action definitions and CODEOWNERS decide what runs with the repository's permissions and who must approve.

**What to do.** Review these files line by line, whatever the rest of the pull request is about.

## PS500 oversized

**Severity:** low

The pull request is larger than most reviewers can check carefully.

**Why it matters.** Review quality drops sharply as diffs grow.

**What to do.** Ask for it to be split into smaller pull requests.

## Thresholds

- PS200 fires for a modified file when at least 20 changed lines, and at least half of all changed lines in that file, are formatting only.
  A line counts as formatting only when it is blank, or when it matches a line on the other side of the diff after removing whitespace, unifying quote style and dropping a trailing comma or semicolon. In Markdown and other text files, `*` and `_` emphasis and the length of `---` rules are also ignored.
- PS500 fires above 1500 changed lines or 60 files.
- PS302 counts added lines that define a test in pytest and unittest, Jest, Vitest and Mocha, Go, Rust, JUnit and PHPUnit.
