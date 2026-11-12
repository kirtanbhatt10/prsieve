"""Command line interface."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional, Sequence

from . import __version__, checks, github, gitio, report
from .checks import RULES, SEVERITIES, Target

LEVELS = SEVERITIES + ("none",)


def _target(spec: str) -> Target:
    name, sep, ref = spec.partition("=")
    return Target(name=name, ref=ref) if sep and name and ref else Target(name=spec, ref=spec)


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("-C", "--repo", default=".", metavar="PATH", help="git repository to read (default: .)")
    p.add_argument("-f", "--format", choices=report.FORMATS, default="text")
    p.add_argument("-o", "--output", metavar="FILE", help="write the report to FILE instead of stdout")
    p.add_argument("--summary", metavar="FILE", help="also append a Markdown report to FILE")
    p.add_argument("--json", metavar="FILE", dest="json_file", help="also write a JSON report to FILE")
    p.add_argument("--fail-on", choices=LEVELS, default="high", metavar="LEVEL",
                   help="exit 1 if a finding is at or above LEVEL: %s (default: high)" % ", ".join(LEVELS))
    p.add_argument("--min-severity", choices=SEVERITIES, default="info", metavar="LEVEL",
                   help="hide findings below LEVEL")
    p.add_argument("--ignore", action="append", default=[], metavar="RULE", help="skip a rule id (repeatable)")
    p.add_argument("--no-color", action="store_true", help="plain output (NO_COLOR is respected too)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prsieve", description="Triage pull requests before you spend review time on them.")
    parser.add_argument("--version", action="version", version="prsieve %s" % __version__)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    c = sub.add_parser("check", help="triage one branch against the base and other open branches")
    c.add_argument("--base", required=True, help="base branch or commit, e.g. origin/main")
    c.add_argument("--head", required=True, metavar="[NAME=]REF", help="the pull request branch or commit")
    c.add_argument("--other", action="append", default=[], metavar="[NAME=]REF",
                   help="another open pull request to compare with (repeatable)")
    c.add_argument("--title", default="", help="pull request title, for the claim checks")
    c.add_argument("--body-file", metavar="FILE", help="file holding the description ('-' for stdin)")
    _common(c)

    q = sub.add_parser("queue", help="check every pair of open branches and suggest a merge order")
    q.add_argument("--base", required=True)
    q.add_argument("--pr", action="append", default=[], metavar="[NAME=]REF", required=True,
                   help="an open pull request branch or commit (repeatable)")
    _common(q)

    g = sub.add_parser("github", help="read pull requests from GitHub (Actions or a local clone)")
    g.add_argument("--slug", metavar="OWNER/REPO", default=os.environ.get("GITHUB_REPOSITORY", ""))
    g.add_argument("--pr", type=int, metavar="NUMBER", help="pull request to check (default: the Actions event)")
    g.add_argument("--all", action="store_true", help="check the whole open queue instead of one pull request")
    g.add_argument("--remote", default="origin", help="git remote to fetch from (default: origin)")
    g.add_argument("--max-others", type=int, default=30, metavar="N",
                   help="compare with at most N other open pull requests (default: 30)")
    g.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    _common(g)

    r = sub.add_parser("rules", help="list the rules, or explain one")
    r.add_argument("rule", nargs="?", metavar="RULE")
    return parser


def _rules(rule: Optional[str]) -> int:
    if not rule:
        for r in RULES.values():
            print("%s  %-6s  %-26s %s" % (r.id, r.severity, r.name, r.summary))
        return 0
    found = RULES.get(rule.upper())
    if not found:
        print("prsieve: unknown rule %s" % report.safe(rule), file=sys.stderr)
        return 2
    print("%s %s (%s)\n\n%s\n\nWhy it matters: %s\n\nWhat to do: %s"
          % (found.id, found.name, found.severity, found.summary, found.why, found.fix))
    return 0


def _run_github(args: argparse.Namespace) -> checks.Result:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or None
    slug = github.check_slug(args.slug)
    pulls = github.open_pulls(slug, args.api_url, token)
    if args.all:
        if not pulls:
            return checks.Result(mode="queue", base="the default branch")
        branch = str(pulls[0]["base"])
        same = [p for p in pulls if p["base"] == branch][: max(2, args.max_others)]
        warnings = github.fetch_refs(args.repo, args.remote, branch, [int(p["number"]) for p in same])
        missing = {w.split(":")[0] for w in warnings}
        result = checks.queue(args.repo, github.base_ref(branch),
                              [t for t in map(github.target, same) if t.name not in missing], args.ignore)
    else:
        subject = github.one_pull(slug, args.pr, args.api_url, token) if args.pr else \
            github.event_pull(os.environ.get("GITHUB_EVENT_PATH"))
        if not subject:
            raise github.GitHubError("no pull request: pass --pr NUMBER or run on a pull_request event")
        branch = str(subject["base"])
        others = [p for p in pulls if p["number"] != subject["number"] and p["base"] == branch]
        others = others[: max(0, args.max_others)]
        numbers = [int(subject["number"])] + [int(p["number"]) for p in others]
        warnings = github.fetch_refs(args.repo, args.remote, branch, numbers)
        missing = {w.split(":")[0] for w in warnings}
        result = checks.check(args.repo, github.base_ref(branch), github.target(subject),
                              [t for t in map(github.target, others) if t.name not in missing], args.ignore)
    result.base = branch
    result.errors = warnings + result.errors
    return result


def _run(args: argparse.Namespace) -> checks.Result:
    if args.command == "github":
        return _run_github(args)
    if args.command == "queue":
        return checks.queue(args.repo, args.base, [_target(s) for s in args.pr], args.ignore)
    head = _target(args.head)
    head.title = args.title
    if args.body_file:
        if args.body_file == "-":
            head.body = sys.stdin.read()
        else:
            with open(args.body_file, encoding="utf-8", errors="replace") as fh:
                head.body = fh.read()
    return checks.check(args.repo, args.base, head, [_target(s) for s in args.other], args.ignore)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    if args.command is None:
        parser.print_help()
        return 2
    if args.command == "rules":
        return _rules(args.rule)

    unknown = [r for r in args.ignore if r.upper() not in RULES]
    if unknown:
        print("prsieve: unknown rule %s" % report.safe(", ".join(unknown)), file=sys.stderr)
        return 2
    try:
        result = _run(args)
        colour = (args.format == "text" and not args.output and not args.no_color
                  and "NO_COLOR" not in os.environ and sys.stdout.isatty())
        text = report.render(result, args.format, args.min_severity, colour)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as fh:
                fh.write(text)
        else:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(errors="backslashreplace")
            sys.stdout.write(text)
        if args.json_file:
            with open(args.json_file, "w", encoding="utf-8") as fh:
                fh.write(report.to_json(result, args.min_severity))
        if args.summary:
            with open(args.summary, "a", encoding="utf-8") as fh:
                fh.write(report.render_markdown(result, args.min_severity))
    except (gitio.GitError, github.GitHubError, OSError) as exc:
        print("prsieve: %s" % report.safe(str(exc)), file=sys.stderr)
        return 2

    if args.fail_on == "none":
        return 0
    floor = checks.sev_rank(args.fail_on)
    return 1 if any(checks.sev_rank(f.severity) >= floor for f in result.findings) else 0
