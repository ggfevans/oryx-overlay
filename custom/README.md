# custom/

Everything here is yours. Oryx never writes to this folder, so nothing in it
can conflict with an Oryx export. `scripts/build.sh` layers it onto a staging
copy of `layout/` inside the QMK tree at build time:

| File | Joins the build as | Use it for |
|---|---|---|
| `config.h` | `#include`d at the end of Oryx's `config.h` | Tapping settings, feature defines |
| `rules.mk` | `-include`d at the end of Oryx's `rules.mk` | Turning features on, `SRC +=` extra files |
| `keymap_extra.c` | `#include`d at the end of Oryx's `keymap.c` | Combos, key overrides, helper functions |
| `keymap.json` | its `modules` list is merged into Oryx's | Enabling community modules |
| `modules/<owner>/<name>/` | staged in a per-build QMK userspace, outside the QMK tree | Your own or vendored community modules. One with the same name as a template module (in `modules/` at the repo root) replaces it. Owners ZSA ships (`zsa/`, `qmk/`) are refused. |

Which one to reach for, in order of preference:

1. **A setting or feature switch** goes in `config.h` or `rules.mk`.
2. **New behaviour that needs no Oryx callback** (combos, key overrides) goes in `keymap_extra.c`.
3. **New behaviour that must run on every keypress** goes in a community module
   (`modules/` + `keymap.json`). Modules get their own `process_record_<name>()`,
   so Oryx's `process_record_user` stays untouched. Needs firmware v25+.
4. **Only if none of those fit**, edit `layout/keymap.c` itself. Keep the edit
   small and mark it `// [custom]`. Git carries it across Oryx syncs, and if
   Oryx rewrites the same lines you resolve a merge conflict.

The template's bundled modules live in `modules/` at the repo root, so
`make self-update` can ship fixes to them. They're off until you list them in
`keymap.json`. To change one, copy it into `custom/modules/` under the same
name and edit your copy:

| Module | Does |
|---|---|
| `example/hello_overlay` | A minimal example: Shift+Backspace sends Delete. |
| `oryx_overlay/screensaver` | Runs an RGB effect while the keyboard is idle and hides Oryx layer colours, restoring both on the next keypress. Settings are at the top of `screensaver.c`; set them in `config.h`. |
