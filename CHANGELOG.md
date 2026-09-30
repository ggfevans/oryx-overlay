# Changelog

## Unreleased

- `oryx_overlay/screensaver` community module: after `SCREENSAVER_TIMEOUT` idle it runs `SCREENSAVER_MODE` with layer colours off, and restores both on the next input without writing EEPROM. Off by default; enable it in `custom/keymap.json`. Tested by `tests/test_screensaver.py`. (#3)
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
