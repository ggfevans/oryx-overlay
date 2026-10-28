# Changelog

## Unreleased

- The viewer's embedded JSON escapes `<`, `>`, `&`, U+2028 and U+2029 as `\uXXXX` (was only `</`), so a layer name like `<!--<script` can't blank the viewer by pushing the HTML parser into its double-escaped script state. Covered by `tests/test_oryx_layout.py`. (#11)
- `make docker-<target>` forwards the settings `load_config` lets the environment override (`KEYBOARD`, `ORYX_LAYOUT_ID`, `KEYMAP_NAME`, `LEGEND_STYLE`, `ORYX_GEOMETRY`, `QMK_BRANCH`) when set, read from `CONFIG_KEYS` in `scripts/lib.sh` so the lists can't drift, so `KEYBOARD=moonlander/revb make docker-build` no longer silently builds rev A. It also passes the host's git identity (`GIT_AUTHOR_*`/`GIT_COMMITTER_*`, from `git config`, environment wins), so `make docker-sync` can commit to the `oryx` branch. Covered by `tests/test_docker_env.py`. (#9)
- `KEYMAP_NAME` is validated (`A-Za-z0-9_-` only) in `load_config`, so `KEYMAP_NAME=x:dfu-util` can't turn `make build` into a flash and `../../..` can't make `rm -rf` escape the keymaps folder. `.claude/settings.json` also denies `make *:dfu*`, `*:teensy*`, `*:avrdude*` and `*:st-*`, the other flash targets in ZSA's fork. Covered by `tests/test_keymap_name.py`. (#10)
- Workflow actions are pinned to commit SHAs (tag in a trailing comment), and `.github/dependabot.yml` opens one weekly PR to bump them, so a moved upstream tag can't change what CI runs. Both are template-owned, so `make self-update` ships them. Covered by `tests/test_workflow_pins.py`. (#2)
- `.gitattributes` forces LF line endings (images stay binary), so a Windows clone with `core.autocrlf=true` no longer gets CRLF scripts that fail with `bash\r: No such file or directory`; `load_config` also ignores a CR in `oryx.conf`. `.gitattributes` is template-owned, so `make self-update` ships it. An existing CRLF clone needs the one-off re-checkout in README Troubleshooting. Covered by `tests/test_line_endings.py`. (#8)
- `make self-update [TO=<ref>]` brings a repo made with *Use this template* up to a newer template release, with no shared history needed: a 3-way merge per file between the release in `.oryx-overlay-version`, your copy and the new release, for the template-owned paths in `.oryx-overlay-manifest`. Conflicts get markers and a non-zero exit; files the template removed are deleted only if unchanged; `layout/`, `custom/`, `oryx.conf` and `docs/` are never written, nor paths in `.oryx-overlay-keep`. The result is left uncommitted. Covered by `tests/test_self_update.py`. (#25)
- Bundled modules moved from `custom/modules/` to a template-owned `modules/`, so fixes reach existing copies. Module names are unchanged (`oryx_overlay/screensaver`, `example/hello_overlay`), so `custom/keymap.json` keeps working. `build.sh` stages `modules/` first, then `custom/modules/`; a module of yours with the same name replaces the template's, and the build log says so. (#25)
- The scheduled Oryx sync is switched on by the repository variable `ORYX_SYNC_SCHEDULE=true` instead of a workflow edit. (#25)
- Personal Claude notes go in `CLAUDE.local.md`, now git-ignored with `.claude/settings.local.json`. (#25)
- `oryx_overlay/screensaver` community module: after `SCREENSAVER_TIMEOUT` idle it runs `SCREENSAVER_MODE` with layer colours off, and restores both on the next input without writing EEPROM. Off by default; enable it in `custom/keymap.json`. Tested by `tests/test_screensaver.py`. (#3)
- Builds start from a clean QMK tree: `build.sh` resets the cached tree's `keyboards/` and `modules/` to ZSA's branch before staging, and stages `custom/modules/` in a per-build `QMK_USERSPACE` instead of copying them into the tree. A keymap left at another keyboard level no longer hijacks later builds with a stale `SERIAL_NUMBER` (#5), a deleted module fails the build with a clear message instead of building from a stale copy, and custom modules can't replace ZSA's (`zsa/`, `qmk/`) (#7). `KEYBOARD` must name a board, not a family (`moonlander/reva`, not `moonlander`). Builds refuse a cached tree that isn't ZSA's fork on the layout's firmware branch, and only one build runs per tree at a time. A relative `CACHE_DIR` is made absolute. `build.sh --stage-only` stages without compiling; `tests/test_build_staging.py` covers it. (#27)
- A repo path with spaces builds: the QMK cache moves to `~/.cache/oryx-overlay/` (or `$XDG_CACHE_HOME`), and `build.sh` and `make doctor` explain a `CACHE_DIR` with spaces. (#22)
- Screensaver no longer overrides Keymapp or Oryx when they take the LEDs, or any other mode change: it ends and restores only the layer-colour setting, leaving the new mode alone. (#6)

## 0.1.0 (2026-09-30)

First release.

- Sync from Oryx via the public GraphQL API into a machine-managed `oryx` branch, merged into `main` with key-level changelogs in the commit body.
- Zero-conflict `custom/` overlay: `config.h`, `rules.mk`, `keymap_extra.c`, `keymap.json` modules, and local community modules.
- Firmware builds against ZSA's QMK fork at the layout's firmware version, in GitHub Actions, natively, or in Docker.
- Installs whatever Python packages a firmware branch needs (e.g. `appdirs` for firmware24) into `.cache/`, so any firmware version builds from the same toolchain.
- Drawings with keymap-drawer tinted by Oryx LED colours, an interactive `docs/index.html` viewer, and a text view in `docs/keymap.md`.
- Lighting effect picker in `docs/index.html` (Lighting tab): every RGB Matrix effect the board supports, running on your layout through a JavaScript port of QMK's effect code, grouped like Oryx's list. Shows which effects are in your firmware (Oryx `#undef`s and `custom/config.h` included), RGB Mode presses to reach each, your RGB Mode and Toggle Layer Colors keys, and copyable defines for the rest. Reactive effects respond to typing.
- Warns when Oryx layer colours on the base layer hide all effects, and reports `RGB_MATRIX_TIMEOUT`.
- A Lighting section in `docs/keymap.md`, so Claude Code can answer lighting questions.
- `tests/test_rgb_effects.py` compiles QMK's effect headers and checks the port frame for frame (7,020 frames across 39 deterministic effects); the build workflow runs it as an informational check.
- Layout checks for unreachable layers, dangling layer keys and one-way layers.
- Claude Code integration: `CLAUDE.md`, project permissions, and `/oryx-sync`, `/qmk-feature`, `/layout-review` skills.
- Built and tested with Moonlander rev A (firmware v25), Voyager (v24 and v25), ErgoDox EZ and Planck EZ (v24) exports. Moonlander layouts still on v24 build with `KEYBOARD=moonlander/reva`, which maps to the pre-revision board folder.
