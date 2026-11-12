"""Thin wrappers around the git CLI.

prsieve never checks out, builds or executes anything from a pull request. It asks
git for diffs and for trial merges (``git merge-tree``), which touch neither the
working tree nor the index.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple


class GitError(Exception):
    """git failed, or was asked for something prsieve refuses to pass on."""


_REF_OK = re.compile(r"^[\w./+@#~^{}-]+$")


def check_ref(ref: str) -> str:
    """Reject refs that could be read as an option or smuggle a refspec."""
    if not ref or ref.startswith("-") or ".." in ref or not _REF_OK.match(ref):
        raise GitError("unsupported ref name: %r" % ref)
    return ref


def git(repo: str, *args: str, ok: Sequence[int] = (0,), timeout: int = 300) -> Tuple[int, str]:
    env = dict(os.environ)
    env.update(LC_ALL="C", GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    cmd = ["git", "-C", repo, "-c", "core.quotepath=off", *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, env=env, timeout=timeout)
    except FileNotFoundError:
        raise GitError("git executable not found") from None
    except subprocess.TimeoutExpired:
        raise GitError("git %s timed out" % args[0]) from None
    if proc.returncode not in ok:
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        exc = GitError("git %s failed: %s" % (args[0], err[-1] if err else "exit %d" % proc.returncode))
        exc.stderr = "\n".join(err)  # type: ignore[attr-defined]
        raise exc
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def resolve(repo: str, ref: str) -> str:
    """Full commit id for ``ref``."""
    check_ref(ref)
    try:
        _, out = git(repo, "rev-parse", "--verify", "--quiet", ref + "^{commit}")
    except GitError:
        raise GitError("cannot resolve %r to a commit" % ref) from None
    return out.strip()


def merge_base(repo: str, a: str, b: str) -> str:
    try:
        _, out = git(repo, "merge-base", a, b)
    except GitError:
        raise GitError(
            "no common history between %s and %s (shallow clone? fetch with full depth)" % (a[:12], b[:12])
        ) from None
    return out.strip()


@dataclass
class FileChange:
    path: str
    old_path: str
    status: str
    added: List[str] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)
    binary: bool = False


@dataclass
class ChangeSet:
    """What one branch changes relative to its merge base with the base branch."""

    name: str
    sha: str
    base_sha: str
    title: str = ""
    files: Dict[str, FileChange] = field(default_factory=dict)

    @property
    def paths(self) -> Set[str]:
        out: Set[str] = set()
        for change in self.files.values():
            out.add(change.path)
            out.add(change.old_path)
        return out

    @property
    def added(self) -> int:
        return sum(len(c.added) for c in self.files.values())

    @property
    def removed(self) -> int:
        return sum(len(c.removed) for c in self.files.values())


_DIFF = ("diff", "--no-color", "--no-ext-diff", "--no-textconv", "-M")


def _parse_raw(out: str) -> List[FileChange]:
    tokens = out.split("\0")
    entries: List[FileChange] = []
    i = 0
    while i < len(tokens):
        meta = tokens[i]
        if not meta.startswith(":"):
            i += 1
            continue
        status = meta.split(" ")[-1]
        if status[:1] in ("R", "C") and i + 2 < len(tokens):
            entries.append(FileChange(path=tokens[i + 2], old_path=tokens[i + 1], status=status[:1]))
            i += 3
        elif i + 1 < len(tokens):
            entries.append(FileChange(path=tokens[i + 1], old_path=tokens[i + 1], status=status[:1]))
            i += 2
        else:
            break
    return entries


def _parse_patch(patch: str) -> List[FileChange]:
    """Split a ``-U0`` patch into per-file added / removed lines.

    Lines are split on ``\\n`` only: content lines always carry a ``+`` or ``-``
    prefix, so file text can never be mistaken for a ``diff --git`` header.
    """
    blocks: List[FileChange] = []
    cur: Optional[FileChange] = None
    in_hunk = False
    for line in patch.split("\n"):
        if line.startswith("diff --git "):
            cur = FileChange(path="", old_path="", status="")
            blocks.append(cur)
            in_hunk = False
        elif cur is None:
            continue
        elif line.startswith("@@"):
            in_hunk = True
        elif not in_hunk:
            if line.startswith("Binary files ") or line.startswith("GIT binary patch"):
                cur.binary = True
        elif line.startswith("+"):
            cur.added.append(line[1:])
        elif line.startswith("-"):
            cur.removed.append(line[1:])
    return blocks


def changes(repo: str, base_sha: str, head_sha: str, name: str, title: str = "") -> ChangeSet:
    """Everything ``head_sha`` changes since it forked from ``base_sha``."""
    fork = merge_base(repo, base_sha, head_sha)
    _, raw = git(repo, *_DIFF, "--raw", "-z", "--no-abbrev", fork, head_sha)
    entries = _parse_raw(raw)
    _, patch = git(repo, *_DIFF, "-U0", fork, head_sha)
    blocks = _parse_patch(patch)
    if len(blocks) == len(entries):
        pairs = list(zip(entries, [[b] for b in blocks]))
    else:
        # Type changes can emit two patches for one path; ask per file instead.
        pairs = []
        for entry in entries:
            spec = sorted({entry.old_path, entry.path})
            _, one = git(repo, *_DIFF, "-U0", fork, head_sha, "--", *spec)
            pairs.append((entry, _parse_patch(one)))
    cs = ChangeSet(name=name, sha=head_sha, base_sha=fork, title=title)
    for entry, parts in pairs:
        for part in parts:
            entry.added.extend(part.added)
            entry.removed.extend(part.removed)
            entry.binary = entry.binary or part.binary
        cs.files[entry.path] = entry
    return cs


def trial_merge(repo: str, a: str, b: str) -> List[str]:
    """Paths that conflict when ``a`` and ``b`` are merged. Empty means a clean merge.

    Uses ``git merge-tree --write-tree`` (git 2.38+): a real three-way merge done
    in memory. It writes loose objects but never touches the index or work tree.
    """
    try:
        code, out = git(repo, "merge-tree", "--write-tree", "--name-only", "-z", "--no-messages", a, b, ok=(0, 1))
    except GitError as exc:
        detail = getattr(exc, "stderr", "").lower()
        if "usage" in detail or "unknown option" in detail:
            raise GitError("prsieve needs git 2.38 or newer (git merge-tree --write-tree)") from None
        raise
    if code == 0:
        return []
    names = [n for n in out.split("\0")[1:] if n]
    return sorted(set(names))


def is_shallow(repo: str) -> bool:
    _, out = git(repo, "rev-parse", "--is-shallow-repository")
    return out.strip() == "true"


def fetch(repo: str, remote: str, refspecs: Sequence[str]) -> None:
    if not remote or remote.startswith("-"):
        raise GitError("unsupported remote: %r" % remote)
    args = ["fetch", "--quiet", "--no-tags", "--force"]
    if is_shallow(repo):
        args.append("--unshallow")
    git(repo, *args, remote, *refspecs, timeout=900)
