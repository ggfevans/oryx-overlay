# Contributing

Thanks for helping. This file is for changes to the template's tooling. Your own
layout lives in your own copy of the template.

## Ground rules

- **Keep the overlay zero-conflict.** Nothing in `scripts/` may write to `layout/`
  except `oryx_fetch.py` (on the `oryx` branch), and nothing may write to `custom/`.
- **Template files vs user files.** Copies of the template get tooling updates
  through `make self-update`, which only touches paths in `.oryx-overlay-manifest`.
  A new tooling file must be under a listed path (`tests/test_self_update.py`
  checks). Bundled modules go in `modules/`, never `custom/`.
- **Portable shell.** Scripts must run on macOS's bash 3.2: no associative arrays,
  no `${var,,}`, no `mapfile`, and guard empty arrays under `set -u`
  (`${arr[@]+"${arr[@]}"}`).
- **Standard-library Python** for everything except drawing (keymap-drawer) and
  building (qmk). The scripts must run on Python 3.9 (macOS system Python); keymap-drawer needs 3.10+.
- **Test against real exports.** Oryx's output changes over time. If you touch the
  parser, try it on a couple of public layouts (`python3 scripts/oryx_fetch.py
  --layout-id default --geometry voyager --out /tmp/x`) as well as the unit tests.

## Before opening a PR

```sh
make check      # shellcheck, actionlint, pytest (the RGB golden test needs `make build` first)
make build      # the stock layout must still build
```

Add a line to `CHANGELOG.md` under *Unreleased*. A release renames *Unreleased* to
`## X.Y.Z (date)`, sets `.oryx-overlay-version` to `vX.Y.Z` in the same commit, and
tags that commit `vX.Y.Z`: self-update finds releases by tag. If your change affects how Claude
Code should work in the repo, update `CLAUDE.md` or the skill in `.claude/skills/`.
