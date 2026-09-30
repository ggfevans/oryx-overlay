---
name: qmk-feature
description: Add or tune a QMK feature on top of the Oryx layout without breaking future Oryx syncs, for example Chordal Hold or Flow Tap for home-row mods, tapping terms, combos, key overrides, Caps Word, Repeat Key, autocorrect, or custom per-keypress behaviour. Use when the user wants something Oryx can't configure, or asks to change firmware behaviour rather than which key is where.
argument-hint: "<what you want the keyboard to do>"
---

# Add a QMK feature

Goal: $ARGUMENTS

## 1. Understand the layout first

Read `docs/keymap.md` (run `make render` if it's missing or older than `layout/keymap.c`).
It shows every layer, how each is reached, and the raw keycodes of special keys. Note
anything the feature touches: home-row mods, thumb keys, keys Oryx already uses for macros.

## 2. Confirm the feature exists at this firmware version

ZSA's fork is pinned to `firmwareNN` (see `qmk_version` in `layout/.oryx.json`) and lags
mainline QMK. Check the source, not memory or docs.qmk.fm:

```sh
make build                      # makes sure .cache/qmk_firmware-firmwareNN exists
grep -rn "FEATURE_NAME" .cache/qmk_firmware-firmware*/quantum .cache/qmk_firmware-firmware*/builddefs/common_features.mk
```

If it's missing, say so and offer the closest alternative that does exist.

## 3. Pick the least invasive place (stop at the first that fits)

| Need | Where |
|---|---|
| A define (timing, behaviour flags) | `custom/config.h`. `#undef` first if Oryx's `config.h` already defines it. |
| An RGB effect Oryx left out | `#define ENABLE_RGB_MATRIX_<NAME>` in `custom/config.h` (or the user ticks it in Oryx, Advanced Settings › RGB). The Lighting tab in `docs/index.html` previews it. |
| Turn a feature on or off | `custom/rules.mk` (`X_ENABLE = yes`) |
| Data tables: combos, key overrides, autocorrect | `custom/keymap_extra.c` (appended to keymap.c, so QMK introspection sees it) |
| Logic on every keypress or scan | A community module: `custom/modules/<owner>/<name>/` with `qmk_module.json` and `<name>.c` defining `process_record_<name>()`, enabled in `custom/keymap.json`. Needs firmware v25+. `custom/modules/example/hello_overlay/` is a template. |
| A hook that must live inside an Oryx callback | Last resort: a minimal edit to `layout/keymap.c`, every added line marked `// [custom]`. Tell the user it may conflict on a later sync. |

Don't define callbacks Oryx already defines (`process_record_user`,
`keyboard_post_init_user`, `rgb_matrix_indicators_user`, `layer_state_set_user`,
`get_tapping_term`, ...). Check with `grep -n "_user(" layout/keymap.c`.

## 4. Make the change

- Keep comments short and say why, not what.
- Reference keys by keycode (`KC_J`), not by position. Oryx can move keys; keycodes survive.
- If the feature needs a new keycode the user will place in Oryx, say that Oryx can't
  emit arbitrary custom keycodes. Suggest reusing an unused keycode (e.g. `KC_F21`–`KC_F24`)
  that the user assigns in Oryx and the module intercepts.

## 5. Prove it

`make build` must pass. Then tell the user:
- what changed and in which file,
- how to test it by hand after flashing (a concrete key sequence),
- any Oryx-side step they must do themselves.
Never flash.
