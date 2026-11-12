"""Build throwaway git repositories for the tests."""

import os
import subprocess
import tempfile


def git(repo, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com",
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, env=env)
    if out.returncode:
        raise AssertionError("git %s: %s" % (" ".join(args), out.stderr.decode()))
    return out.stdout.decode().strip()


class Repo:
    """A repository with one commit on ``main``; ``branch()`` adds pull request branches."""

    def __init__(self, files, bare=False):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "repo")
        os.makedirs(self.path)
        git(self.path, "init", "-q", "-b", "main")
        git(self.path, "config", "core.autocrlf", "false")
        self.write(files)
        self.commit("initial")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._tmp.cleanup()

    @property
    def tmp(self):
        return self._tmp.name

    def write(self, files):
        for rel, text in files.items():
            full = os.path.join(self.path, *rel.split("/"))
            if text is None:
                os.remove(full)
                continue
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)

    def commit(self, message):
        git(self.path, "add", "-A")
        git(self.path, "commit", "-q", "--allow-empty", "-m", message)
        return git(self.path, "rev-parse", "HEAD")

    def branch(self, name, files, start="main"):
        git(self.path, "checkout", "-q", "-b", name, start)
        self.write(files)
        sha = self.commit(name)
        git(self.path, "checkout", "-q", "main")
        return sha

    def on_main(self, files):
        self.write(files)
        return self.commit("main moves on")


def lines(*parts):
    return "\n".join(parts) + "\n"
