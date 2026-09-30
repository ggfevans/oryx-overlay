# oryx-overlay

A ZSA keyboard layout managed in two layers:

- **Oryx** (ZSA's web configurator) owns the visual layout: which key is where, layers, LED colours, macros, tap dances. It exports QMK source into `layout/`.
- **This repo** owns everything Oryx can't do, in `custom/`, layered onto the Oryx export at build time.

The user edits keys in Oryx. You help with everything around that: syncing, QMK features, builds, reviews.

## Repo map

| Path | Owner | Notes |
|---|---|---|
| `layout/` | Oryx | Overwritten by every sync. Edit only as a last resort (see below). `.oryx.json` holds the revision, firmware version and layer names. |
| `custom/` | User + you | `config.h`, `rules.mk`, `keymap_extra.c`, `keymap.json`, `modules/`. See `custom/README.md`. |
| `oryx.conf` | User | Layout ID, keyboard (e.g. `moonlander/reva`), legend style. |
| `docs/keymap.md` | Generated | **Read this first** to understand the layout: every layer as a text grid, layer access, lint checks, special keys with raw keycodes. |
| `docs/keymap.svg`, `docs/index.html` | Generated | Drawings for humans, plus the Lighting tab (RGB effect picker). Never edit. |
| `scripts/rgb_effects.js` | Tooling | JavaScript port of QMK's RGB Matrix effects (GPL-2.0-or-later). `tests/test_rgb_effects.py` checks it against the C. |
| `scripts/` | Tooling | `sync.sh`, `build.sh`, `render.sh`, `oryx_layout.py`, `oryx_fetch.py`. |
| `.cache/qmk_firmware-firmwareNN/` | Generated | ZSA's QMK fork at the layout's firmware version. Read it to check what a feature supports; never edit it. |

For lighting questions, read the `## Lighting` section of `docs/keymap.md`: which effects are in the firmware, the RGB Mode and Toggle Layer Colors keys, and whether Oryx layer colours hide effects. To add an effect, the user can tick it in Oryx (Advanced Settings › RGB), or you add `#define ENABLE_RGB_MATRIX_<NAME>` to `custom/config.h`.

Positions like `L2.3` mean left half, row 2, fourth key from the left in that half-row (all 0-based), matching the grids in `docs/keymap.md` and the sync commit messages. Use them, plus the key legend, when telling the user which key to change in Oryx.

## Commands

```sh
make sync      # fetch the latest Oryx export onto the oryx branch and merge it here
make build     # compile firmware into build/, then redraw docs/
make render    # redraw docs/ only
make diff REF=HEAD~1   # key-level changes to the layout since REF
make lint      # unreachable layers, dangling layer keys, layers you can't leave
make test      # tooling unit tests
```

If `make build` says `qmk` or `arm-none-eabi-gcc` is missing, use `make docker-build` or `make setup`.

## Rules

1. **Never flash firmware.** Build it and tell the user the path in `build/`. They flash with Keymapp. Flashing commands are denied in `.claude/settings.json`.
2. **Key assignments belong in Oryx.** If the user wants a key moved or a layer changed, tell them what to change in Oryx (layer name, position, new key), then run `make sync`. Editing the keymaps array in `layout/keymap.c` makes the repo disagree with Oryx and guarantees conflicts later.
3. **Prefer `custom/` over `layout/`**, in this order: a setting in `custom/config.h` or `custom/rules.mk`; code in `custom/keymap_extra.c`; a community module in `custom/modules/` enabled via `custom/keymap.json` (firmware v25+). Only when none fits, make a minimal edit to `layout/keymap.c` and mark every added line `// [custom]`.
4. **Check the pinned firmware, not memory.** ZSA's fork lags mainline QMK. Before using a feature, grep `.cache/qmk_firmware-*/quantum/` and `builddefs/common_features.mk` to confirm it exists at this version. docs.qmk.fm describes mainline and may be ahead.
5. **Redefining an Oryx `#define`** in `custom/config.h` needs `#undef` first, or the build fails on redefinition.
6. **Prove it compiles.** After any change to `custom/` or `layout/`, run `make build` and report the result. Read `build/build.log` on failure.
7. **The `oryx` branch is machine-managed.** Never commit to it by hand; `scripts/sync.sh` does.

## Workflows

Project skills cover the common jobs: `/oryx-sync` (pull from Oryx, resolve conflicts), `/qmk-feature` (add a QMK feature safely), `/layout-review` (ergonomics and consistency review).
