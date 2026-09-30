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
| `modules/<owner>/<name>/` | copied into the QMK tree's `modules/` | Your own or vendored community modules |

Which one to reach for, in order of preference:

1. **A setting or feature switch** goes in `config.h` or `rules.mk`.
2. **New behaviour that needs no Oryx callback** (combos, key overrides) goes in `keymap_extra.c`.
3. **New behaviour that must run on every keypress** goes in a community module
   (`modules/` + `keymap.json`). Modules get their own `process_record_<name>()`,
   so Oryx's `process_record_user` stays untouched. Needs firmware v25+.
4. **Only if none of those fit**, edit `layout/keymap.c` itself. Keep the edit
   small and mark it `// [custom]`. Git carries it across Oryx syncs, and if
   Oryx rewrites the same lines you resolve a merge conflict.

See `modules/example/hello_overlay/` for a minimal module.
