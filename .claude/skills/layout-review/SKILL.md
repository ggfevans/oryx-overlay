---
name: layout-review
description: Review the keyboard layout for ergonomics, consistency and dead weight, and suggest concrete changes to make in Oryx or custom/. Use when the user asks to review, critique, audit or improve their layout, asks why something feels awkward, or wants to simplify layers.
argument-hint: "[focus, e.g. thumbs, symbols, home-row mods]"
---

# Review the layout

Focus: $ARGUMENTS (if empty, review everything)

## Gather

1. `make render` so `docs/keymap.md` matches `layout/keymap.c`, then read `docs/keymap.md`.
2. `make lint` for mechanical problems.
3. Skim `custom/` so you don't recommend something that's already there.
4. Ask one question if the answer changes your advice and isn't in the files: main OS,
   main language, what they mostly type (prose, code, which languages).

## What to look for

- **Reachability:** layers nothing reaches, layers you can enter but not leave
  (TO/TG with no way back), momentary layers whose activator sits on the same hand as
  the keys you use most on that layer.
- **Thumbs:** which thumb keys do the heaviest work (Space, Backspace, Enter, layer
  keys), and whether the strongest thumb positions hold them. On a Moonlander the big
  red key and the outermost thumb key are the least comfortable.
- **Home-row mods:** mirrored order on both hands, the same mod in the same finger
  position on every layer where it appears, and whether `CHORDAL_HOLD` or
  `FLOW_TAP_TERM` in `custom/config.h` would stop misfires.
- **Symbols and numbers:** pairs (`()`, `[]`, `{}`, `<>`) adjacent and in the same
  order; programming bigrams (`->`, `=>`, `!=`, `::`) on comfortable rolls; digits in one
  consistent arrangement.
- **Dead weight:** duplicate keys that do the same thing on the same layer, `KC_NO`
  in prime positions, transparent keys on the base layer.
- **Consistency:** the same function in the same place across layers (e.g. Esc,
  Enter, arrows), so muscle memory carries over.

## Report

Lead with the three changes that would help most, then the rest grouped by theme.
For each suggestion give:
- the layer name and position (`L2.3` style, from `docs/keymap.md`),
- current key, proposed key, and why in one sentence,
- where to make it: **Oryx** (key assignments, layers, colours: the user does this)
  or **custom/** (behaviour: offer to do it with `/qmk-feature`).

Be direct. If the layout is already good in some area, say so in one line and move on.
Don't edit `layout/` during a review.
