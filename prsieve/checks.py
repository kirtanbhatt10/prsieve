"""The rules. Each one is deterministic: same commits and same text, same findings."""

from __future__ import annotations

import posixpath
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Sequence, Tuple

from . import gitio
from .gitio import ChangeSet, FileChange

SEVERITIES = ("info", "low", "medium", "high")


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    severity: str
    summary: str
    why: str
    fix: str


RULES: Dict[str, Rule] = {r.id: r for r in (
    Rule("PS100", "conflicts-with-open-pr", "medium",
         "This pull request and another open one cannot both merge as they stand.",
         "A trial merge of the two branches (git merge-tree) ends in a conflict. Each one looks "
         "mergeable on its own, so the problem only shows up after the first is merged.",
         "Decide the order now. Merge one, then ask the other author to rebase, or ask one of "
         "them to drop the overlapping change."),
    Rule("PS101", "shares-files-with-open-pr", "info",
         "Another open pull request edits the same files, but the two merge cleanly.",
         "Clean text merges can still disagree in meaning, for example two edits to one function.",
         "Read the two diffs side by side for the shared files before merging the second one."),
    Rule("PS102", "conflicts-with-base", "medium",
         "The branch no longer merges cleanly into its base branch.",
         "The base branch moved on after this branch was created.",
         "Ask the author to rebase or merge the base branch."),
    Rule("PS200", "formatting-churn", "medium",
         "Most changed lines in a file are re-formatted or moved, not changed.",
         "Whitespace, quote style, table padding and re-ordered lines inflate the diff, hide the "
         "real change and collide with every other open pull request that touches the file.",
         "Ask for the formatting to be split out, or dropped if the project has no formatter."),
    Rule("PS300", "docs-only-claim", "high",
         "The pull request says it is documentation only but changes other files.",
         "Reviewers skim documentation changes. A description that undersells the diff is the "
         "easiest way for an unreviewed code change to get merged.",
         "Review the listed files as code, or ask the author to correct the description."),
    Rule("PS301", "no-dependency-claim", "medium",
         "The pull request says it adds no dependencies but edits a dependency manifest.",
         "Manifests and lockfiles decide what gets installed and run.",
         "Open the manifest diff and confirm nothing new is pulled in."),
    Rule("PS302", "test-count-claim", "low",
         "The description promises more new tests than the diff adds.",
         "The count is matched against added test definitions in common frameworks, so treat "
         "it as a prompt to look, not as proof.",
         "Check the test files, then ask the author to fix the description or add the tests."),
    Rule("PS303", "unchecked-checklist", "info",
         "The description still has unchecked checklist items.",
         "The author may not have finished the steps the project asks for.",
         "Ask about the open items before reviewing in depth."),
    Rule("PS400", "changes-automation", "medium",
         "The pull request changes CI, action or ownership files.",
         "Workflows, action definitions and CODEOWNERS decide what runs with the repository's "
         "permissions and who must approve.",
         "Review these files line by line, whatever the rest of the pull request is about."),
    Rule("PS500", "oversized", "low",
         "The pull request is larger than most reviewers can check carefully.",
         "Review quality drops sharply as diffs grow.",
         "Ask for it to be split into smaller pull requests."),
)}


@dataclass
class Finding:
    rule: str
    message: str
    pr: str = ""
    path: str = ""
    other: str = ""
    detail: List[str] = field(default_factory=list)

    @property
    def severity(self) -> str:
        return RULES[self.rule].severity

    def to_dict(self) -> dict:
        return {"rule": self.rule, "name": RULES[self.rule].name, "severity": self.severity,
                "pr": self.pr, "path": self.path, "other": self.other,
                "message": self.message, "detail": list(self.detail)}


def sev_rank(level: str) -> int:
    return SEVERITIES.index(level)


# ---- pairs of pull requests -------------------------------------------------

def _label(cs: ChangeSet) -> str:
    return "%s (%s)" % (cs.name, cs.title) if cs.title else cs.name


def pair_findings(repo: str, a: ChangeSet, b: ChangeSet) -> List[Finding]:
    """PS100 / PS101 for one pair, reported from ``a``'s point of view."""
    shared = sorted(a.paths & b.paths)
    if not shared:
        return []
    conflicted = [p for p in gitio.trial_merge(repo, a.sha, b.sha) if p in shared]
    out = [Finding("PS100", "Conflicts with %s: both change the same part of this file. "
                            "Whichever merges second will need a rebase." % _label(b),
                   pr=a.name, path=p, other=b.name) for p in conflicted]
    clean = [p for p in shared if p not in conflicted]
    if clean and not conflicted:
        out.append(Finding("PS101", "Also edited by %s. The two merge cleanly; compare them "
                                    "before merging the second." % _label(b),
                           pr=a.name, path=clean[0], other=b.name, detail=clean))
    return out


def base_findings(repo: str, base_sha: str, cs: ChangeSet) -> List[Finding]:
    conflicted = gitio.trial_merge(repo, base_sha, cs.sha)
    if not conflicted:
        return []
    return [Finding("PS102", "No longer merges cleanly into the base branch.",
                    pr=cs.name, path=conflicted[0], detail=conflicted)]


# ---- formatting churn -------------------------------------------------------

_WS = re.compile(r"\s+")
_RULES_RUN = re.compile(r"[-=]{3,}")
_MARKUP = (".md", ".mdx", ".markdown", ".rst", ".txt", ".adoc")
CHURN_MIN_LINES = 20
CHURN_MIN_SHARE = 0.5


def _normalise(line: str, markup: bool) -> str:
    text = _WS.sub("", line).replace("'", '"').rstrip(",;")
    if markup:
        text = _RULES_RUN.sub("---", text).replace("_", "*")
    return text


def cosmetic_lines(change: FileChange) -> int:
    """Changed lines that only differ in formatting, or were moved unchanged."""
    markup = change.path.lower().endswith(_MARKUP)
    removed = Counter(_normalise(line, markup) for line in change.removed)
    added = Counter(_normalise(line, markup) for line in change.added)
    blank = removed.pop("", 0) + added.pop("", 0)
    return blank + 2 * sum((removed & added).values())


def churn_findings(cs: ChangeSet) -> List[Finding]:
    out = []
    for change in cs.files.values():
        total = len(change.added) + len(change.removed)
        if change.binary or change.status != "M" or not total:
            continue
        cosmetic = cosmetic_lines(change)
        if cosmetic >= CHURN_MIN_LINES and cosmetic / total >= CHURN_MIN_SHARE:
            real = total - cosmetic
            out.append(Finding("PS200", "%d of %d changed lines are formatting only (whitespace, quotes, "
                                        "table padding or moved lines); %d line%s a real change."
                               % (cosmetic, total, real, " carries" if real == 1 else "s carry"),
                               pr=cs.name, path=change.path))
    return out


# ---- claims in the title and description ------------------------------------

_COMMENT = re.compile(r"<!--.*?-->", re.S)
_UNCHECKED = re.compile(r"^\s*[-*+]\s+\[ \].*$", re.M)
_DOCS_TITLE = re.compile(r"^\s*docs?(\([^)]*\))?!?:", re.I)
_DOCS_TEXT = re.compile(
    r"\b(?:documentation|docs?)[- ]only\b"
    r"|\bno (?:code|functional|behaviou?r(?:al)?) changes?\b"
    r"|\bonly (?:updates?|changes?|touches|edits?) (?:the )?(?:docs|documentation|readme)\b", re.I)
_NO_DEPS = re.compile(
    r"\bno (?:(?:new|additional|extra|added|runtime|third[- ]party|external) )*dependenc(?:y|ies)\b"
    r"|\bwithout (?:adding|any|new) (?:new )?dependenc(?:y|ies)\b", re.I)
_TEST_CLAIM = re.compile(
    r"\b(?:adds?|added|adding|introduc\w+|includ\w+|wrote|writes?)\s+(\d{1,4})\s+"
    r"(?:(?:new|additional|more|extra)\s+)?(?:[a-z-]+\s+)?tests?\b", re.I)
_TEST_DEF = re.compile(
    r"^\s*(?:async\s+)?def\s+test\w*\s*\("
    r"|^\s*(?:it|test)(?:\.\w+)*\s*\("
    r"|^\s*func\s+Test\w*\s*\("
    r"|^\s*#\[(?:\w+::)?test\]"
    r"|^\s*@(?:Test|ParameterizedTest)\b"
    r"|^\s*(?:public\s+)?function\s+test\w*\s*\(")

_DOC_EXT = _MARKUP + (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")
_DOC_DIRS = ("docs/", "doc/")
_DOC_NAMES = {"license", "licence", "notice", "authors", "contributors", "copying"}
_MANIFESTS = {
    "package.json", "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml",
    "bun.lock", "bun.lockb", "pyproject.toml", "setup.py", "setup.cfg", "pipfile", "pipfile.lock",
    "poetry.lock", "uv.lock", "cargo.toml", "cargo.lock", "go.mod", "go.sum", "gemfile",
    "gemfile.lock", "composer.json", "composer.lock", "pom.xml", "build.gradle", "build.gradle.kts",
}
_REQUIREMENTS = re.compile(r"^requirements[\w.-]*\.(?:txt|in)$", re.I)
_AUTOMATION = re.compile(
    r"^\.github/workflows/[^/]+\.ya?ml$|^\.github/actions/|^action\.ya?ml$"
    r"|^(?:\.github/|docs/)?CODEOWNERS$|^\.github/dependabot\.ya?ml$"
    r"|^\.gitlab-ci\.yml$|^\.circleci/|^Jenkinsfile$|^azure-pipelines\.ya?ml$")


def is_doc(path: str) -> bool:
    low = path.lower()
    return (low.endswith(_DOC_EXT) or low.startswith(_DOC_DIRS)
            or posixpath.basename(low) in _DOC_NAMES)


def is_manifest(path: str) -> bool:
    name = posixpath.basename(path).lower()
    return name in _MANIFESTS or bool(_REQUIREMENTS.match(name))


def _some(paths: Sequence[str], limit: int = 5) -> str:
    shown = ", ".join(paths[:limit])
    return shown + (" and %d more" % (len(paths) - limit) if len(paths) > limit else "")


def claim_findings(cs: ChangeSet, title: str, body: str) -> List[Finding]:
    out: List[Finding] = []
    body = _COMMENT.sub("", body or "")
    unchecked = _UNCHECKED.findall(body)
    prose = _UNCHECKED.sub("", body)
    paths = sorted(cs.files)

    if _DOCS_TITLE.match(title or "") or _DOCS_TEXT.search(prose):
        code = [p for p in paths if not is_doc(p)]
        if code:
            out.append(Finding("PS300", "Described as documentation only, but it also changes %s."
                               % _some(code), pr=cs.name, path=code[0], detail=code))

    if _NO_DEPS.search(prose):
        manifests = [p for p in paths if is_manifest(p)]
        if manifests:
            out.append(Finding("PS301", "Says it adds no dependencies, but edits %s."
                               % _some(manifests), pr=cs.name, path=manifests[0], detail=manifests))

    claimed = [int(n) for n in _TEST_CLAIM.findall(prose)]
    if claimed:
        found = sum(1 for c in cs.files.values() for line in c.added if _TEST_DEF.match(line))
        if found < max(claimed):
            out.append(Finding("PS302", "Says it adds %d tests; the diff adds %d test definitions."
                               % (max(claimed), found), pr=cs.name))

    if unchecked:
        out.append(Finding("PS303", "%d checklist item%s left unchecked in the description."
                           % (len(unchecked), "" if len(unchecked) == 1 else "s"), pr=cs.name))
    return out


# ---- scope ------------------------------------------------------------------

MAX_LINES = 1500
MAX_FILES = 60


def scope_findings(cs: ChangeSet) -> List[Finding]:
    out: List[Finding] = []
    auto = [p for p in sorted(cs.paths) if _AUTOMATION.match(p)]
    if auto:
        out.append(Finding("PS400", "Changes automation that runs with repository permissions: %s."
                           % _some(auto), pr=cs.name, path=auto[0], detail=auto))
    lines = cs.added + cs.removed
    if lines > MAX_LINES or len(cs.files) > MAX_FILES:
        out.append(Finding("PS500", "%d changed lines across %d files." % (lines, len(cs.files)), pr=cs.name))
    return out


# ---- entry points -----------------------------------------------------------

@dataclass
class Target:
    name: str
    ref: str
    title: str = ""
    body: str = ""


@dataclass
class Result:
    mode: str
    base: str
    subjects: List[ChangeSet] = field(default_factory=list)
    compared: List[str] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


def _load(repo: str, base_sha: str, targets: Iterable[Target], errors: List[str]) -> List[ChangeSet]:
    loaded = []
    for t in targets:
        try:
            loaded.append(gitio.changes(repo, base_sha, gitio.resolve(repo, t.ref), t.name, t.title))
        except gitio.GitError as exc:
            errors.append("%s: %s" % (t.name, exc))
    return loaded


def _finish(result: Result, ignore: Iterable[str]) -> Result:
    skip = {r.upper() for r in ignore}
    result.findings = [f for f in result.findings if f.rule not in skip]
    result.findings.sort(key=lambda f: (f.pr, -sev_rank(f.severity), f.rule, f.other, f.path))
    return result


def check(repo: str, base: str, head: Target, others: Sequence[Target] = (),
          ignore: Iterable[str] = ()) -> Result:
    """Triage one pull request against its base branch and the other open ones."""
    base_sha = gitio.resolve(repo, base)
    subject = gitio.changes(repo, base_sha, gitio.resolve(repo, head.ref), head.name, head.title)
    result = Result(mode="check", base=base, subjects=[subject])
    result.findings += base_findings(repo, base_sha, subject)
    for other in _load(repo, base_sha, others, result.errors):
        if other.sha == subject.sha:
            continue
        result.compared.append(other.name)
        result.findings += pair_findings(repo, subject, other)
    result.findings += churn_findings(subject)
    result.findings += claim_findings(subject, head.title, head.body)
    result.findings += scope_findings(subject)
    return _finish(result, ignore)


def queue(repo: str, base: str, targets: Sequence[Target], ignore: Iterable[str] = ()) -> Result:
    """Check every open pull request against every other one, once per pair."""
    base_sha = gitio.resolve(repo, base)
    result = Result(mode="queue", base=base)
    result.subjects = _load(repo, base_sha, targets, result.errors)
    for cs in result.subjects:
        result.findings += base_findings(repo, base_sha, cs)
    for i, a in enumerate(result.subjects):
        for b in result.subjects[i + 1:]:
            if a.sha != b.sha:
                result.findings += pair_findings(repo, a, b)
    return _finish(result, ignore)


def merge_plan(result: Result) -> Tuple[List[str], List[Tuple[str, str]]]:
    """(pull requests that conflict with nothing, conflicting pairs)."""
    pairs = sorted({(f.pr, f.other) for f in result.findings if f.rule == "PS100"})
    stale = {f.pr for f in result.findings if f.rule == "PS102"}
    busy = {name for pair in pairs for name in pair} | stale
    return [cs.name for cs in result.subjects if cs.name not in busy], pairs
