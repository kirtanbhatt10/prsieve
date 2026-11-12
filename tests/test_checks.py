import os
import subprocess
import sys
import unittest

from prsieve import checks, gitio
from prsieve.checks import Target

from helpers import Repo, git, lines

DOC = lines("# Title", "", "Intro paragraph.", "", "## Usage", "", "Run the tool.", "", "## Notes", "", "The end.")


def rules(result):
    return sorted(f.rule for f in result.findings)


class PairTests(unittest.TestCase):
    def test_same_lines_conflict(self):
        with Repo({"README.md": DOC}) as r:
            r.branch("a", {"README.md": DOC.replace("Run the tool.", "Run the tool with -v.")})
            r.branch("b", {"README.md": DOC.replace("Run the tool.", "Run it.")})
            res = checks.check(r.path, "main", Target("a", "a"), [Target("b", "b")])
            self.assertEqual(rules(res), ["PS100"])
            f = res.findings[0]
            self.assertEqual((f.path, f.other, f.severity), ("README.md", "b", "medium"))
            self.assertEqual(res.compared, ["b"])

    def test_same_file_different_parts_is_info(self):
        with Repo({"README.md": DOC}) as r:
            r.branch("a", {"README.md": DOC.replace("# Title", "# New title")})
            r.branch("b", {"README.md": DOC.replace("The end.", "The very end.")})
            res = checks.check(r.path, "main", Target("a", "a"), [Target("b", "b")])
            self.assertEqual(rules(res), ["PS101"])
            self.assertEqual(res.findings[0].severity, "info")

    def test_different_files_are_silent(self):
        with Repo({"README.md": DOC, "a.txt": "a\n"}) as r:
            r.branch("a", {"a.txt": "changed\n"})
            r.branch("b", {"README.md": DOC + "more\n"})
            res = checks.check(r.path, "main", Target("a", "a"), [Target("b", "b")])
            self.assertEqual(res.findings, [])

    def test_stale_branch_is_not_blamed_on_the_other_pr(self):
        # a conflicts with main on x.txt; b never touches x.txt but both edit README cleanly.
        with Repo({"README.md": DOC, "x.txt": "one\n"}) as r:
            r.branch("a", {"x.txt": "from a\n", "README.md": DOC.replace("# Title", "# A")})
            r.on_main({"x.txt": "from main\n"})
            r.branch("b", {"README.md": DOC.replace("The end.", "The very end.")})
            res = checks.check(r.path, "main", Target("a", "a"), [Target("b", "b")])
            self.assertEqual(rules(res), ["PS101", "PS102"])
            stale = [f for f in res.findings if f.rule == "PS102"][0]
            self.assertEqual(stale.detail, ["x.txt"])

    def test_branch_behind_main_without_conflict_is_clean(self):
        with Repo({"README.md": DOC, "x.txt": "one\n"}) as r:
            r.branch("a", {"README.md": DOC + "more\n"})
            r.on_main({"x.txt": "two\n"})
            res = checks.check(r.path, "main", Target("a", "a"))
            self.assertEqual(res.findings, [])
            self.assertEqual(sorted(res.subjects[0].files), ["README.md"])

    def test_unresolvable_other_is_a_warning_not_a_crash(self):
        with Repo({"a.txt": "a\n"}) as r:
            r.branch("a", {"a.txt": "b\n"})
            res = checks.check(r.path, "main", Target("a", "a"), [Target("gone", "no-such-branch")])
            self.assertEqual(res.findings, [])
            self.assertEqual(len(res.errors), 1)
            self.assertIn("gone", res.errors[0])

    def test_trial_merge_leaves_work_tree_and_index_alone(self):
        with Repo({"README.md": DOC}) as r:
            r.branch("a", {"README.md": DOC.replace("Run the tool.", "A")})
            r.branch("b", {"README.md": DOC.replace("Run the tool.", "B")})
            before = (git(r.path, "status", "--porcelain"), git(r.path, "rev-parse", "HEAD"),
                      git(r.path, "for-each-ref"))
            checks.check(r.path, "main", Target("a", "a"), [Target("b", "b")])
            after = (git(r.path, "status", "--porcelain"), git(r.path, "rev-parse", "HEAD"),
                     git(r.path, "for-each-ref"))
            self.assertEqual(before, after)

    def test_queue_checks_each_pair_once_and_plans(self):
        with Repo({"README.md": DOC, "c.txt": "c\n"}) as r:
            r.branch("a", {"README.md": DOC.replace("Run the tool.", "A")})
            r.branch("b", {"README.md": DOC.replace("Run the tool.", "B")})
            r.branch("c", {"c.txt": "changed\n"})
            res = checks.queue(r.path, "main", [Target(n, n) for n in "abc"])
            self.assertEqual(rules(res), ["PS100"])
            self.assertEqual(checks.merge_plan(res), (["c"], [("a", "b")]))

    def test_ignore(self):
        with Repo({"README.md": DOC}) as r:
            r.branch("a", {"README.md": DOC.replace("Run the tool.", "A")})
            r.branch("b", {"README.md": DOC.replace("Run the tool.", "B")})
            res = checks.check(r.path, "main", Target("a", "a"), [Target("b", "b")], ignore=["ps100"])
            self.assertEqual(res.findings, [])


TABLE = lines(*["| `opt%d` | value %d | meaning %d |" % (i, i, i) for i in range(12)])
PADDED = lines(*["| `opt%d`   | value %d   | meaning %d   |" % (i, i, i) for i in range(12)])


class ChurnTests(unittest.TestCase):
    def test_table_padding_is_churn(self):
        with Repo({"README.md": TABLE}) as r:
            r.branch("a", {"README.md": PADDED + "One new line.\n"})
            res = checks.check(r.path, "main", Target("a", "a"))
            self.assertEqual(rules(res), ["PS200"])
            self.assertIn("24 of 25", res.findings[0].message)
            self.assertIn("1 line carries a real change", res.findings[0].message)

    def test_real_rewrite_is_not_churn(self):
        with Repo({"README.md": TABLE}) as r:
            new = lines(*["| `opt%d` | other %d | changed text %d |" % (i, i * 7, i) for i in range(12)])
            r.branch("a", {"README.md": new})
            self.assertEqual(checks.check(r.path, "main", Target("a", "a")).findings, [])

    def test_small_reformat_is_below_threshold(self):
        with Repo({"a.py": "x = 'a'\ny = 'b'\n"}) as r:
            r.branch("a", {"a.py": 'x = "a"\ny = "b"\n'})
            self.assertEqual(checks.check(r.path, "main", Target("a", "a")).findings, [])

    def test_quote_and_indent_reformat_of_code(self):
        src = lines(*["    v%d = 'text %d'" % (i, i) for i in range(15)])
        out = lines(*['  v%d = "text %d"' % (i, i) for i in range(15)])
        with Repo({"a.py": src}) as r:
            r.branch("a", {"a.py": out})
            self.assertEqual(rules(checks.check(r.path, "main", Target("a", "a"))), ["PS200"])

    def test_underscore_is_not_emphasis_in_code(self):
        src = lines(*["v%d = a_b" % i for i in range(15)])
        out = lines(*["v%d = a*b" % i for i in range(15)])
        with Repo({"a.py": src}) as r:
            r.branch("a", {"a.py": out})
            self.assertEqual(checks.check(r.path, "main", Target("a", "a")).findings, [])

    def test_new_file_is_never_churn(self):
        with Repo({"a.txt": "a\n"}) as r:
            r.branch("a", {"b.md": "\n" * 40})
            self.assertEqual(checks.check(r.path, "main", Target("a", "a")).findings, [])


def claims(files, title="", body=""):
    with Repo({"README.md": DOC, "src/app.py": "x = 1\n", "tests/test_app.py": "import app\n",
               "pyproject.toml": "[project]\n"}) as r:
        r.branch("a", files)
        return checks.check(r.path, "main", Target("a", "a", title=title, body=body))


class ClaimTests(unittest.TestCase):
    def test_docs_title_with_code_change(self):
        res = claims({"README.md": DOC + "x\n", "src/app.py": "x = 2\n"}, title="docs: fix typo")
        self.assertEqual(rules(res), ["PS300"])
        self.assertEqual(res.findings[0].severity, "high")
        self.assertEqual(res.findings[0].detail, ["src/app.py"])

    def test_docs_title_with_docs_change_is_fine(self):
        res = claims({"README.md": DOC + "x\n", "docs/ci.md": "# CI\n"}, title="docs(ci): add examples")
        self.assertEqual(res.findings, [])

    def test_docs_only_phrase_in_body(self):
        res = claims({"src/app.py": "x = 2\n"}, body="This is a documentation-only change.")
        self.assertEqual(rules(res), ["PS300"])

    def test_claim_inside_unchecked_box_or_comment_is_not_a_claim(self):
        body = "<!-- docs only? say so -->\n- [ ] docs only\n- [x] done\n"
        res = claims({"src/app.py": "x = 2\n"}, body=body)
        self.assertEqual(rules(res), ["PS303"])
        self.assertIn("1 checklist item left", res.findings[0].message)

    def test_no_dependency_claim(self):
        body = "No new runtime dependencies."
        self.assertEqual(rules(claims({"pyproject.toml": "[project]\nx=1\n"}, body=body)), ["PS301"])
        self.assertEqual(claims({"src/app.py": "x = 2\n"}, body=body).findings, [])

    def test_test_count_claim(self):
        two = "import app\n\ndef test_a():\n    pass\n\nasync def test_b():\n    pass\n"
        self.assertEqual(rules(claims({"tests/test_app.py": two}, body="Adds 14 regression tests.")), ["PS302"])
        self.assertEqual(claims({"tests/test_app.py": two}, body="Adds 2 new tests.").findings, [])
        self.assertEqual(claims({"tests/test_app.py": two}, body="All 97 tests pass.").findings, [])

    def test_automation_and_size(self):
        res = claims({".github/workflows/ci.yml": "on: push\n", "CODEOWNERS": "* @me\n"})
        self.assertEqual(rules(res), ["PS400"])
        self.assertEqual(res.findings[0].detail, [".github/workflows/ci.yml", "CODEOWNERS"])
        big = claims({"src/big.py": "".join("v%d = %d\n" % (i, i) for i in range(1600))})
        self.assertEqual(rules(big), ["PS500"])


class GitTests(unittest.TestCase):
    def test_bad_refs_are_rejected_before_reaching_git(self):
        for ref in ("--upload-pack=x", "-h", "a..b", "a b", "", "a:b", "a;b"):
            with self.subTest(ref=ref):
                with self.assertRaises(gitio.GitError):
                    gitio.check_ref(ref)

    def test_rename_and_binary_and_odd_names(self):
        with Repo({"old name.txt": "same\ncontent\nhere\n", "bin.dat": "x\n"}) as r:
            git(r.path, "checkout", "-q", "-b", "a")
            new = "n" + chr(0xE9) + "w.txt"
            git(r.path, "mv", "old name.txt", new)
            with open(os.path.join(r.path, "bin.dat"), "wb") as fh:
                fh.write(b"\x00\x01\x02" * 50)
            r.commit("a")
            cs = gitio.changes(r.path, gitio.resolve(r.path, "main"), gitio.resolve(r.path, "a"), "a")
            self.assertEqual(cs.files[new].old_path, "old name.txt")
            self.assertEqual(cs.files[new].status, "R")
            self.assertTrue(cs.files["bin.dat"].binary)
            self.assertIn("old name.txt", cs.paths)

    def test_file_text_cannot_forge_a_diff_header(self):
        evil = "a\x0cdiff --git a/x b/x\n@@ -1 +1 @@\n+def test_fake():\n"
        with Repo({"a.txt": "a\n", "b.txt": "b\n"}) as r:
            r.branch("a", {"a.txt": evil, "b.txt": "c\n"})
            cs = gitio.changes(r.path, gitio.resolve(r.path, "main"), gitio.resolve(r.path, "a"), "a")
            self.assertEqual(sorted(cs.files), ["a.txt", "b.txt"])
            self.assertEqual(cs.files["b.txt"].added, ["c"])

    def test_typechange_falls_back_to_per_file_diffs(self):
        if sys.platform == "win32":
            self.skipTest("symlinks")
        with Repo({"a.txt": "a\n", "b.txt": "b\n"}) as r:
            git(r.path, "checkout", "-q", "-b", "a")
            os.remove(os.path.join(r.path, "a.txt"))
            os.symlink("b.txt", os.path.join(r.path, "a.txt"))
            r.write({"b.txt": "changed\n"})
            r.commit("a")
            cs = gitio.changes(r.path, gitio.resolve(r.path, "main"), gitio.resolve(r.path, "a"), "a")
            self.assertEqual(sorted(cs.files), ["a.txt", "b.txt"])
            self.assertEqual(cs.files["b.txt"].added, ["changed"])

    def test_unrelated_history_is_a_clear_error(self):
        with Repo({"a.txt": "a\n"}) as r:
            git(r.path, "checkout", "-q", "--orphan", "island")
            r.commit("island")
            git(r.path, "checkout", "-q", "main")
            with self.assertRaises(gitio.GitError) as ctx:
                checks.check(r.path, "main", Target("island", "island"))
            self.assertIn("no common history", str(ctx.exception))

    def test_nothing_from_the_branch_is_executed(self):
        hook = "#!/bin/sh\ntouch \"$(git rev-parse --show-toplevel)/PWNED\"\n"
        with Repo({"README.md": DOC}) as r:
            r.branch("a", {"README.md": DOC + "x\n", "setup.py": "open('PWNED','w')\n",
                           ".githooks/post-checkout": hook, ".gitattributes": "*.md diff=evil merge=evil\n"})
            r.branch("b", {"README.md": DOC + "y\n"})
            real = subprocess.run
            seen = []

            def spy(cmd, *a, **kw):
                seen.append(cmd)
                return real(cmd, *a, **kw)
            subprocess.run = spy
            try:
                checks.check(r.path, "main", Target("a", "a"), [Target("b", "b")])
            finally:
                subprocess.run = real
            self.assertTrue(seen)
            self.assertTrue(all(cmd[0] == "git" for cmd in seen))
            self.assertFalse(os.path.exists(os.path.join(r.path, "PWNED")))


if __name__ == "__main__":
    unittest.main()
