import contextlib
import io
import json
import os
import unittest
from unittest import mock

from prsieve import __version__, checks, github, report
from prsieve.checks import Finding, Result
from prsieve.cli import main
from prsieve.gitio import ChangeSet

from helpers import Repo, git, lines

DOC = lines("# Title", "", "Intro paragraph.", "", "## Usage", "", "Run the tool.", "", "The end.")


def run(argv, stdin=""):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
            mock.patch("sys.stdin", io.StringIO(stdin)):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def two_prs():
    r = Repo({"README.md": DOC, "src/app.py": "x = 1\n"})
    r.branch("a", {"README.md": DOC.replace("Run the tool.", "A")})
    r.branch("b", {"README.md": DOC.replace("Run the tool.", "B"), "src/app.py": "x = 2\n"})
    return r


class CliTests(unittest.TestCase):
    def test_text_report_and_default_exit_code(self):
        with two_prs() as r:
            code, out, err = run(["check", "-C", r.path, "--base", "main", "--head", "#1=a", "--other", "#2=b"])
            self.assertEqual((code, err), (0, ""))
            self.assertIn("MEDIUM  PS100  README.md", out)
            self.assertIn("Conflicts with #2", out)
            self.assertIn("1 finding: 1 medium", out)
            self.assertNotIn("\033", out)

    def test_fail_on_levels(self):
        with two_prs() as r:
            base = ["check", "-C", r.path, "--base", "main", "--head", "a", "--other", "b"]
            self.assertEqual(run(base + ["--fail-on", "medium"])[0], 1)
            self.assertEqual(run(base + ["--fail-on", "high"])[0], 0)
            self.assertEqual(run(base + ["--fail-on", "medium", "--ignore", "PS100"])[0], 0)
            docs = ["check", "-C", r.path, "--base", "main", "--head", "b", "--title", "docs: tweak"]
            self.assertEqual(run(docs)[0], 1)
            self.assertEqual(run(docs + ["--fail-on", "none"])[0], 0)

    def test_json_report(self):
        with two_prs() as r:
            code, out, _ = run(["check", "-C", r.path, "--base", "main", "--head", "b", "--other", "a",
                                "--title", "docs: tweak", "--body-file", "-", "-f", "json"],
                               stdin="- [ ] todo\n")
            doc = json.loads(out)
            self.assertEqual(code, 1)
            self.assertEqual((doc["tool"], doc["version"], doc["mode"]), ("prsieve", __version__, "check"))
            self.assertEqual([f["rule"] for f in doc["findings"]], ["PS300", "PS100", "PS303"])
            self.assertEqual(doc["summary"], {"info": 1, "low": 0, "medium": 1, "high": 1})
            self.assertEqual(doc["pull_requests"][0]["files"], 2)
            self.assertEqual(doc["compared"], ["a"])

    def test_min_severity_hides_but_does_not_change_exit(self):
        with two_prs() as r:
            code, out, _ = run(["check", "-C", r.path, "--base", "main", "--head", "b", "--other", "a",
                                "--title", "docs: x", "--min-severity", "high", "-f", "json"])
            self.assertEqual(code, 1)
            self.assertEqual([f["rule"] for f in json.loads(out)["findings"]], ["PS300"])

    def test_output_file_and_summary(self):
        with two_prs() as r:
            dest, summary = os.path.join(r.tmp, "out.json"), os.path.join(r.tmp, "summary.md")
            code, out, _ = run(["check", "-C", r.path, "--base", "main", "--head", "a", "--other", "b",
                                "-f", "json", "-o", dest, "--summary", summary])
            self.assertEqual((code, out), (0, ""))
            with open(dest, encoding="utf-8") as fh:
                self.assertEqual(len(json.load(fh)["findings"]), 1)
            with open(summary, encoding="utf-8") as fh:
                self.assertIn("| medium | PS100 | README.md |", fh.read())

    def test_queue_markdown(self):
        with two_prs() as r:
            r.branch("c", {"src/new.py": "y = 1\n"})
            code, out, _ = run(["queue", "-C", r.path, "--base", "main", "--pr", "a", "--pr", "b",
                                "--pr", "c", "-f", "markdown"])
            self.assertEqual(code, 0)
            self.assertIn("Clear to merge in any order: c", out)
            self.assertIn("Pick an order: a and b conflict", out)

    def test_usage_errors_exit_2(self):
        with two_prs() as r:
            self.assertEqual(run([])[0], 2)
            self.assertEqual(run(["check", "--base", "main"])[0], 2)
            self.assertEqual(run(["check", "-C", r.path, "--base", "main", "--head", "nope"])[0], 2)
            self.assertEqual(run(["check", "-C", r.path, "--base", "main", "--head", "--evil"])[0], 2)
            self.assertEqual(run(["check", "-C", r.path, "--base", "main", "--head", "a", "--ignore", "XX1"])[0], 2)
            self.assertEqual(run(["check", "-C", os.path.join(r.tmp, "missing"), "--base", "m", "--head", "a"])[0], 2)
            code, _, err = run(["check", "-C", r.path, "--base", "main", "--head", "a", "-o",
                                os.path.join(r.tmp, "no", "dir", "x")])
            self.assertEqual(code, 2)
            self.assertTrue(err.startswith("prsieve: "))

    def test_rules_command(self):
        code, out, _ = run(["rules"])
        self.assertEqual(code, 0)
        self.assertEqual(len(out.strip().splitlines()), len(checks.RULES))
        code, out, _ = run(["rules", "ps100"])
        self.assertEqual(code, 0)
        self.assertIn("What to do:", out)
        self.assertEqual(run(["rules", "PS999"])[0], 2)
        self.assertEqual(run(["--version"])[1].strip(), "prsieve " + __version__)

    def test_every_rule_is_documented(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "docs", "rules.md"), encoding="utf-8") as fh:
            doc = fh.read()
        for rule in checks.RULES.values():
            self.assertIn("## %s %s" % (rule.id, rule.name), doc)
            self.assertIn(rule.severity, checks.SEVERITIES)


class ReportTests(unittest.TestCase):
    def result(self, title, message, path):
        cs = ChangeSet(name="#1", sha="a" * 40, base_sha="b" * 40, title=title)
        res = Result(mode="check", base="main", subjects=[cs])
        res.findings = [Finding("PS300", message, pr="#1", path=path)]
        return res

    def test_hostile_text_cannot_inject_terminal_or_workflow_commands(self):
        bidi = chr(0x202E)
        res = self.result("x\033[2J" + bidi + "gnp.exe", "bad\n::error::owned\r\n", "a\nb.md")
        for fmt in report.FORMATS:
            with self.subTest(fmt=fmt):
                out = report.render(res, fmt)
                self.assertNotIn("\033", out)
                self.assertNotIn(bidi, out)
                self.assertNotIn("\r", out)
                self.assertFalse(any(line.startswith("::error::owned") for line in out.split("\n")))
        gh = report.render(res, "github").split("\n")
        self.assertEqual(len([line for line in gh if line.startswith("::")]), 1)
        self.assertTrue(gh[0].startswith("::error file=a%sb.md,title=prsieve PS300" % report.safe("\n")))
        self.assertEqual(report.safe("a\nb" + bidi), "a" + "\\" + "u000ab" + "\\" + "u202e")

    def test_annotation_properties_are_escaped(self):
        out = report.render(self.result("t", "100% sure", "a,b:c.md"), "github")
        self.assertIn("file=a%2Cb%3Ac.md,", out)
        self.assertIn("::100%25 sure", out)

    def test_markdown_cells_are_escaped(self):
        out = report.render(self.result("t", "a | b `c` <img>", "x|y.md"), "markdown")
        row = [line for line in out.split("\n") if line.startswith("| high")][0]
        self.assertEqual(row.count(" | "), 3)
        self.assertNotIn("<img>", out)


def pull(number, title="", body="", base="main"):
    return {"number": number, "title": title, "body": body, "base": {"ref": base}, "draft": False}


class GitHubTests(unittest.TestCase):
    """End to end against a local 'origin' that carries refs/pull/N/head, with the API mocked."""

    def setUp(self):
        self.origin = two_prs()
        self.addCleanup(self.origin.__exit__)
        o = self.origin.path
        git(o, "update-ref", "refs/pull/1/head", "a")
        git(o, "update-ref", "refs/pull/2/head", "b")
        self.origin.branch("c", {"src/new.py": "y = 1\n"})
        git(o, "update-ref", "refs/pull/3/head", "c")
        self.clone = os.path.join(self.origin.tmp, "clone")
        git(self.origin.tmp, "clone", "-q", "--depth", "1", "file://" + o, self.clone)
        self.pulls = [pull(1, "Change usage"), pull(2, "docs: tweak", "- [ ] todo"), pull(3, "Add module"),
                      pull(9, "Other base", base="release")]

    def api(self, url, token):
        self.urls.append((url, token))
        if "/pulls?" in url:
            return self.pulls
        number = int(url.rsplit("/", 1)[1])
        return [p for p in self.pulls if p["number"] == number][0]

    def run_github(self, *extra, env=None):
        self.urls = []
        environ = {"GITHUB_REPOSITORY": "me/proj", "GITHUB_TOKEN": "t0ken", "GITHUB_API_URL": "https://api.example"}
        environ.update(env or {})
        with mock.patch.dict(os.environ, environ), mock.patch("prsieve.github._get", side_effect=self.api):
            return run(["github", "-C", self.clone, "-f", "json", *extra])

    def test_check_from_actions_event_in_a_shallow_clone(self):
        event = os.path.join(self.origin.tmp, "event.json")
        with open(event, "w", encoding="utf-8") as fh:
            json.dump({"pull_request": self.pulls[1]}, fh)
        code, out, err = self.run_github(env={"GITHUB_EVENT_PATH": event})
        self.assertEqual(err, "")
        doc = json.loads(out)
        self.assertEqual(code, 1)
        self.assertEqual(doc["base"], "main")
        self.assertEqual(doc["pull_requests"][0]["name"], "#2")
        self.assertEqual(doc["compared"], ["#1", "#3"])
        self.assertEqual([(f["rule"], f["other"]) for f in doc["findings"]],
                         [("PS300", ""), ("PS100", "#1"), ("PS303", "")])
        self.assertIn("Conflicts with #1 (Change usage)", doc["findings"][1]["message"])
        self.assertEqual(self.urls, [("https://api.example/repos/me/proj/pulls?state=open&sort=updated"
                                      "&direction=desc&per_page=100", "t0ken")])
        self.assertTrue(all(u.startswith("https://api.example/repos/me/proj/pulls") for u, _ in self.urls))
        self.assertNotIn("t0ken", out)
        refs = git(self.clone, "for-each-ref", "--format=%(refname)", "refs/prsieve")
        self.assertEqual(refs.split(), ["refs/prsieve/base/main", "refs/prsieve/pull/1",
                                        "refs/prsieve/pull/2", "refs/prsieve/pull/3"])

    def test_check_by_number_and_max_others(self):
        code, out, _ = self.run_github("--pr", "1", "--max-others", "1")
        doc = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(doc["pull_requests"][0]["title"], "Change usage")
        self.assertEqual(doc["compared"], ["#2"])

    def test_whole_queue(self):
        code, out, _ = self.run_github("--all")
        doc = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(doc["plan"], {"clear": ["#3"], "conflicts": [["#1", "#2"]]})

    def test_empty_queue_is_not_an_error(self):
        self.pulls = []
        code, out, err = self.run_github("--all")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["pull_requests"], [])

    def test_json_side_file(self):
        dest = os.path.join(self.origin.tmp, "side.json")
        self.urls = []
        env = {"GITHUB_REPOSITORY": "me/proj", "GITHUB_API_URL": "https://api.example"}
        with mock.patch.dict(os.environ, env), mock.patch("prsieve.github._get", side_effect=self.api):
            code, out, _ = run(["github", "-C", self.clone, "--pr", "2", "-f", "github", "--json", dest])
        self.assertEqual(code, 1)
        self.assertIn("::error file=src/app.py,title=prsieve PS300 docs-only-claim::", out)
        self.assertIn("::warning file=README.md,title=prsieve PS100 conflicts-with-open-pr::", out)
        with open(dest, encoding="utf-8") as fh:
            self.assertEqual(len(json.load(fh)["findings"]), 3)

    def test_missing_pull_ref_is_a_warning(self):
        self.pulls.insert(0, pull(7, "Ghost"))
        code, out, _ = self.run_github("--pr", "1")
        doc = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(doc["compared"], ["#2", "#3"])
        self.assertEqual(len(doc["errors"]), 1)
        self.assertTrue(doc["errors"][0].startswith("#7: "))

    def test_no_pull_request_context(self):
        code, _, err = self.run_github(env={"GITHUB_EVENT_PATH": ""})
        self.assertEqual(code, 2)
        self.assertIn("pass --pr NUMBER", err)

    def test_input_validation(self):
        with self.assertRaises(github.GitHubError):
            github._get("http://api.example/x", None)
        with self.assertRaises(github.GitHubError):
            github._get("file:///etc/passwd", None)
        for slug in ("", "me", "me/proj/x", "../x", "me/pr oj", "-x/y"):
            with self.subTest(slug=slug), self.assertRaises(github.GitHubError):
                github.check_slug(slug)
        for branch in ("-x", "a..b", "a:b", "a b", ""):
            with self.subTest(branch=branch), self.assertRaises(github.GitHubError):
                github.fetch_refs(self.clone, "origin", branch, [])
        self.assertEqual(run(["github", "-C", self.clone, "--slug", "bad slug", "--pr", "1"])[0], 2)


if __name__ == "__main__":
    unittest.main()
