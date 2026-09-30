---
name: oryx-sync
description: Pull the latest layout from Oryx, merge it with the custom QMK code in this repo, resolve any merge conflict, rebuild, and summarise what changed. Use when the user says they changed their layout in Oryx, asks to sync, pull or update from Oryx, or a sync stopped on a merge conflict.
argument-hint: "[resolve]"
---

# Sync from Oryx

The `oryx` branch holds untouched Oryx exports. `scripts/sync.sh` commits the newest
export there, then merges `oryx` into the current branch, so git replays only what the
user changed in Oryx on top of the repo's own edits.

## Steps

1. **Check the tree.** `git status`. If there are uncommitted changes, stop and ask the
   user whether to commit or stash them. Never discard them.
2. **Sync.** `make sync`. Outcomes:
   - "Oryx layout unchanged": say so, and stop unless the user wants a rebuild.
   - "Merged": continue to step 4.
   - Exit code 3, "Merge conflict": go to step 3.
   - "could not find layout": the layout ID in `oryx.conf` is wrong, the layout is
     private, or `KEYBOARD` names a different board. Tell the user which, then stop.
3. **Resolve conflicts** (also the entry point for `/oryx-sync resolve`).
   `git diff --name-only --diff-filter=U` lists the files. For each conflict hunk:
   - Oryx's side ("theirs", the `oryx` branch) wins for anything Oryx generates: the
     `keymaps` array, `ledmap`, macros (`ST_MACRO_*`), tap dances, `DUAL_FUNC_*` handling,
     and everything in `config.h`, `rules.mk` and `keymap.json`.
   - The repo's side ("ours") wins for lines marked `// [custom]`. Re-apply each one
     onto Oryx's new version of the surrounding code. If Oryx renamed or removed what the
     custom line depended on, stop and explain the choice to the user.
   - If a hunk is anything else and the right answer isn't obvious, show both sides and ask.
   Then `git add` the files and `git commit --no-edit`.
4. **Build.** `make build`. On failure, read `build/build.log`. The usual cause after a
   sync is custom code referring to something Oryx changed (a keycode, a layer number).
   Fix it in `custom/` and rebuild.
5. **Report**, briefly:
   - What changed in Oryx: `make diff REF=HEAD~1` if the merge was a single commit,
     or `git log -1 oryx --format=%B` (the sync commit body lists key-level changes).
   - Any new warnings in the `## Checks` section of `docs/keymap.md`.
   - The firmware path in `build/`. The user flashes it with Keymapp; never flash.
   - If `qmk_version` in `layout/.oryx.json` changed, say so: Oryx moved to a new
     firmware branch, so check that `custom/` features still exist there (rule 4 in
     CLAUDE.md).
