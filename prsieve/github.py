"""The only part of prsieve that uses the network: reading pull request metadata
from the GitHub REST API and fetching pull request heads with ``git fetch``."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Sequence

from . import __version__, gitio
from .checks import Target

_SLUG = re.compile(r"^[A-Za-z0-9][\w.-]{0,99}/[\w.-]{1,100}$")
_BRANCH = re.compile(r"^[\w./+@#-]+$")
REF_PREFIX = "refs/prsieve/"


class GitHubError(Exception):
    pass


def _get(url: str, token: Optional[str]):
    if not url.startswith("https://"):
        raise GitHubError("refusing non-https API URL: %s" % url)
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "prsieve/%s" % __version__,
               "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = "Bearer %s" % token
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise GitHubError("GitHub API returned %d for %s" % (exc.code, url)) from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise GitHubError("GitHub API request failed for %s: %s" % (url, exc)) from None


def _pull(raw: dict) -> Dict[str, object]:
    return {"number": int(raw["number"]), "title": raw.get("title") or "", "body": raw.get("body") or "",
            "base": (raw.get("base") or {}).get("ref") or "", "draft": bool(raw.get("draft"))}


def check_slug(slug: str) -> str:
    if not _SLUG.match(slug or ""):
        raise GitHubError("expected OWNER/REPO, got %r" % slug)
    return slug


def open_pulls(slug: str, api: str, token: Optional[str], limit: int = 100) -> List[Dict[str, object]]:
    """Open pull requests, most recently updated first (one page, at most 100)."""
    url = "%s/repos/%s/pulls?state=open&sort=updated&direction=desc&per_page=%d" % (
        api.rstrip("/"), check_slug(slug), max(1, min(limit, 100)))
    data = _get(url, token)
    if not isinstance(data, list):
        raise GitHubError("unexpected response listing pull requests")
    return [_pull(item) for item in data]


def one_pull(slug: str, number: int, api: str, token: Optional[str]) -> Dict[str, object]:
    data = _get("%s/repos/%s/pulls/%d" % (api.rstrip("/"), check_slug(slug), number), token)
    if not isinstance(data, dict) or "number" not in data:
        raise GitHubError("unexpected response for pull request %d" % number)
    return _pull(data)


def event_pull(path: Optional[str]) -> Optional[Dict[str, object]]:
    """The pull request from a GitHub Actions event payload, if there is one."""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh).get("pull_request")
    except (OSError, ValueError, AttributeError):
        return None
    return _pull(raw) if isinstance(raw, dict) and "number" in raw else None


def base_ref(branch: str) -> str:
    return REF_PREFIX + "base/" + branch


def pull_ref(number: int) -> str:
    return "%spull/%d" % (REF_PREFIX, number)


def fetch_refs(repo: str, remote: str, branch: str, numbers: Sequence[int]) -> List[str]:
    """Fetch the base branch and pull request heads into refs/prsieve/.

    Returns warnings for pull requests whose head could not be fetched.
    """
    if not _BRANCH.match(branch) or branch.startswith("-") or ".." in branch:
        raise GitHubError("unsupported base branch name: %r" % branch)
    specs = ["+refs/heads/%s:%s" % (branch, base_ref(branch))]
    specs += ["+refs/pull/%d/head:%s" % (n, pull_ref(n)) for n in numbers]
    try:
        gitio.fetch(repo, remote, specs)
        return []
    except gitio.GitError:
        pass
    gitio.fetch(repo, remote, specs[:1])
    warnings = []
    for n, spec in zip(numbers, specs[1:]):
        try:
            gitio.fetch(repo, remote, [spec])
        except gitio.GitError as exc:
            warnings.append("#%d: %s" % (n, exc))
    return warnings


def target(pull: Dict[str, object]) -> Target:
    number = int(pull["number"])  # type: ignore[arg-type]
    return Target(name="#%d" % number, ref=pull_ref(number), title=str(pull["title"]), body=str(pull["body"]))
