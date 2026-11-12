"""Output formats: text, json, markdown and GitHub Actions annotations."""

from __future__ import annotations

import json
import re
from typing import Dict, List

from . import __version__
from .checks import RULES, SEVERITIES, Finding, Result, merge_plan, sev_rank

FORMATS = ("text", "json", "markdown", "github")

# Control characters, bidi overrides and zero-width characters. Titles, branch
# names and paths come from pull request authors, so they are never printed raw.
_RANGES = ((0x00, 0x1F), (0x7F, 0x9F), (0x200B, 0x200F), (0x2028, 0x202E), (0x2060, 0x2069),
           (0xFEFF, 0xFEFF), (0xE0000, 0xE007F))
_UNSAFE = re.compile("[%s]" % "".join("%s-%s" % (re.escape(chr(lo)), re.escape(chr(hi))) for lo, hi in _RANGES))


def safe(text: str) -> str:
    def esc(match):
        code = ord(match.group())
        return "\\u%04x" % code if code <= 0xFFFF else "\\U%08x" % code
    return _UNSAFE.sub(esc, text)


def visible(result: Result, min_severity: str) -> List[Finding]:
    floor = sev_rank(min_severity)
    return [f for f in result.findings if sev_rank(f.severity) >= floor]


def counts(findings: List[Finding]) -> Dict[str, int]:
    out = {level: 0 for level in SEVERITIES}
    for f in findings:
        out[f.severity] += 1
    return out


def _tally(findings: List[Finding]) -> str:
    if not findings:
        return "No findings."
    c = counts(findings)
    parts = ["%d %s" % (c[level], level) for level in reversed(SEVERITIES) if c[level]]
    return "%d finding%s: %s" % (len(findings), "" if len(findings) == 1 else "s", ", ".join(parts))


def _headline(result: Result) -> str:
    if result.mode == "queue":
        return "%d open pull requests against %s" % (len(result.subjects), result.base)
    cs = result.subjects[0]
    title = " %s" % cs.title if cs.title else ""
    return "%s%s (%s) into %s: %d files, +%d -%d, compared with %d open pull request%s" % (
        cs.name, title, cs.sha[:7], result.base, len(cs.files), cs.added, cs.removed,
        len(result.compared), "" if len(result.compared) == 1 else "s")


def _plan_lines(result: Result) -> List[str]:
    clear, pairs = merge_plan(result)
    lines = []
    if clear:
        lines.append("Clear to merge in any order: %s" % ", ".join(clear))
    for a, b in pairs:
        lines.append("Pick an order: %s and %s conflict; the second one needs a rebase" % (a, b))
    return lines


_COLOURS = {"high": "\033[31;1m", "medium": "\033[33;1m", "low": "\033[36m", "info": "\033[2m"}


def render_text(result: Result, min_severity: str = "info", colour: bool = False) -> str:
    findings = visible(result, min_severity)
    out = ["prsieve %s  %s" % (__version__, safe(_headline(result))), ""]
    for f in findings:
        level = f.severity.upper().ljust(6)
        if colour:
            level = _COLOURS[f.severity] + level + "\033[0m"
        where = " ".join(x for x in (f.pr if result.mode == "queue" else "", f.path) if x)
        out.append("%s  %s  %s" % (level, f.rule, safe(where) or RULES[f.rule].name))
        out.append("        %s" % safe(f.message))
        if len(f.detail) > 1:
            out.append("        files: %s" % safe(", ".join(f.detail[:8]))
                       + (" ..." if len(f.detail) > 8 else ""))
    if findings:
        out.append("")
    if result.mode == "queue":
        out += [safe(line) for line in _plan_lines(result)]
    for err in result.errors:
        out.append("warning: %s" % safe(err))
    out.append(_tally(findings))
    return "\n".join(out) + "\n"


def to_json(result: Result, min_severity: str = "info") -> str:
    findings = visible(result, min_severity)
    clear, pairs = merge_plan(result)
    doc = {
        "tool": "prsieve",
        "version": __version__,
        "mode": result.mode,
        "base": result.base,
        "pull_requests": [{"name": cs.name, "title": cs.title, "sha": cs.sha, "files": len(cs.files),
                           "added": cs.added, "removed": cs.removed} for cs in result.subjects],
        "compared": list(result.compared),
        "summary": counts(findings),
        "findings": [f.to_dict() for f in findings],
        "errors": list(result.errors),
    }
    if result.mode == "queue":
        doc["plan"] = {"clear": clear, "conflicts": [list(p) for p in pairs]}
    return json.dumps(doc, indent=2, ensure_ascii=True) + "\n"


def _md(text: str) -> str:
    text = safe(text).replace("\\", "\\\\")
    for ch in "|`*_<>[]#":
        text = text.replace(ch, "\\" + ch)
    return text


def render_markdown(result: Result, min_severity: str = "info") -> str:
    findings = visible(result, min_severity)
    out = ["### prsieve", "", _md(_headline(result)), ""]
    if findings:
        first = "Pull request" if result.mode == "queue" else "File"
        out += ["| Severity | Rule | %s | Finding |" % first, "| --- | --- | --- | --- |"]
        for f in findings:
            where = " ".join(x for x in (f.pr, f.path) if x) if result.mode == "queue" else f.path
            out.append("| %s | %s | %s | %s |" % (f.severity, f.rule, _md(where) or "-", _md(f.message)))
        out.append("")
    if result.mode == "queue":
        out += ["- %s" % _md(line) for line in _plan_lines(result)]
        out.append("")
    out += ["> warning: %s" % _md(err) for err in result.errors]
    out.append(_tally(findings))
    return "\n".join(out) + "\n"


def _prop(text: str) -> str:
    text = safe(text).replace("%", "%25")
    return text.replace(":", "%3A").replace(",", "%2C")


def render_github(result: Result, min_severity: str = "info") -> str:
    """Workflow commands. Text is escaped so findings cannot inject commands."""
    out = []
    for f in visible(result, min_severity):
        kind = {"high": "error", "medium": "warning"}.get(f.severity, "notice")
        props = ["title=%s" % _prop("prsieve %s %s" % (f.rule, RULES[f.rule].name))]
        if f.path and result.mode == "check" and f.rule != "PS102":
            props.insert(0, "file=%s" % _prop(f.path))
        out.append("::%s %s::%s" % (kind, ",".join(props), safe(f.message).replace("%", "%25")))
    out.append(_tally(visible(result, min_severity)))
    return "\n".join(out) + "\n"


def render(result: Result, fmt: str, min_severity: str = "info", colour: bool = False) -> str:
    if fmt == "json":
        return to_json(result, min_severity)
    if fmt == "markdown":
        return render_markdown(result, min_severity)
    if fmt == "github":
        return render_github(result, min_severity)
    return render_text(result, min_severity, colour)
