# Security policy

## Reporting a vulnerability

Please report security issues privately through
[GitHub private vulnerability reporting](https://github.com/kirtanbhatt10/prsieve/security/advisories/new).
Do not open a public issue for a vulnerability.

Include the prsieve version, the command you ran and a minimal repository or pull request
that shows the problem.

## What counts

prsieve reads branches and descriptions written by people you may not trust. These are in scope:

- anything that makes prsieve execute code from an inspected branch;
- a branch name, path, title or description that injects terminal escape sequences,
  GitHub workflow commands or Markdown into a report;
- a way for a pull request to hide a finding that the rules are documented to report;
- a request sent anywhere other than the configured GitHub API URL or git remote.

## Supported versions

The latest release receives fixes.
