# Contributing

Thanks for helping. prsieve is small on purpose: standard library only, one file per concern.

## Set up

```bash
git clone https://github.com/kirtanbhatt10/prsieve && cd prsieve
python -m unittest discover -s tests -t tests
python -m prsieve rules
```

You need Python 3.10+ and git 2.38+. There is nothing to install.

## Where things live

| File | What it holds |
| --- | --- |
| `prsieve/gitio.py` | every call to git, and diff parsing |
| `prsieve/checks.py` | the rules |
| `prsieve/report.py` | output formats and escaping |
| `prsieve/github.py` | GitHub API calls and `git fetch`; the only code that uses the network |
| `prsieve/cli.py` | argument parsing and exit codes |

## Adding or changing a rule

1. Add the rule to `RULES` in `prsieve/checks.py` with a summary, a reason and a fix.
2. Add a test that fires it and a look-alike that must stay silent.
3. Add the rule to `docs/rules.md` and the README table. A test fails if `docs/rules.md` misses a rule.
4. Add a line to `CHANGELOG.md`.

## Ground rules

- No runtime dependencies.
- Never check out, build or execute anything from the branches being inspected.
- Never read configuration from the repository being inspected.
- Anything printed that comes from a pull request goes through `report.safe`.
- Keep pull requests focused. Please do not re-format files you are not changing;
  prsieve runs on its own pull requests and will say so.

## Licence

By submitting a contribution you agree that it is licensed under the MIT License of this
repository, and you confirm that you have the right to submit it.
