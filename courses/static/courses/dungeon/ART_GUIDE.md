# Dungeon Quest — Art Guide

This is the contract between the art and the code. Every image the game draws
is resolved through [`sprites.json`](sprites.json); nothing in the JS, the CSS
or the templates names an image file. Replacing the placeholder art means
dropping files into `sprites/` — **no code changes, ever.**

## How to replace a sprite

1. Draw it at the **exact source size** in the table below (pixel art, 1×).
2. Save it as a PNG.
3. Either overwrite the placeholder with the same filename, **or** save under a
   new name and point that key at it in `sprites.json`.
4. Hard-refresh the dungeon page. That's the whole process.

The board upscales by whole numbers only (1×–3×, picked to fit the viewport)
and everything renders with `image-rendering: pixelated`, so art drawn at the
sizes below stays crisp and never lands on a half pixel. Do **not** pre-scale
your art — ship it at 1× and let the renderer scale it.

## Sizes are derived, not guessed

The source tile size lives in one place in the codebase:
`dungeon/combat_config.py → TILE_SIZE` (currently **32**), with
`ICON_SIZE = TILE_SIZE // 2` (**16**). `sprites.json` mirrors both so the
manifest is readable on its own. If the game's tile size ever changes, the
config is the thing that changes, and every size in this table moves with it.

## The sprites

### `tiles` — 32 × 32, fully opaque

These butt up against each other with no gaps, so they must fill the frame
edge to edge. Transparency here will show the page background through the
floor.

| Key | File | Size | Placeholder | What it is |
| --- | --- | --- | --- | --- |
| `floor` | `sprites/floor.png` | 32 × 32 | slate, letter **F** | Plain walkable ground. |
| `wall` | `sprites/wall.png` | 32 × 32 | dark, letter **W** | Solid. The player can never enter it. |
| `grass` | `sprites/grass.png` | 32 × 32 | green, letter **G** | Walkable, and the only tile that rolls encounters. Should read as "something could be hiding here". |
| `door_locked` | `sprites/door_locked.png` | 32 × 32 | brown, letter **D** | The exit, sealed. Drawn until every enemy is down. |
| `door_unlocked` | `sprites/door_unlocked.png` | 32 × 32 | gold, letter **O** | The exit, open. Swaps in the moment the final key is assembled — make the difference obvious at a glance. |

### `actors` — 32 × 32, transparent background

Drawn *on top of* a floor tile, so the area around the character must be
transparent. Keep the figure inside the 32 × 32 box; anything bleeding past the
edge is clipped by the neighbouring tile.

| Key | File | Size | Placeholder | What it is |
| --- | --- | --- | --- | --- |
| `player_down` | `sprites/player_down.png` | 32 × 32 | blue, ▼ | Player facing the camera. Also the resting pose. |
| `player_up` | `sprites/player_up.png` | 32 × 32 | blue, ▲ | Player facing away. |
| `player_left` | `sprites/player_left.png` | 32 × 32 | blue, ◀ | Player facing left. |
| `player_right` | `sprites/player_right.png` | 32 × 32 | blue, ▶ | Player facing right. |
| `enemy_default` | `sprites/enemy_default.png` | 32 × 32 | red, letter **E** | Every enemy, for now. Enemies stand on their grass tile until defeated, then disappear. |

### `items` — 16 × 16, transparent background

Shown in the inventory bar and in the "you found…" drop toast. Never drawn on
the board.

| Key | File | Size | Placeholder | What it is |
| --- | --- | --- | --- | --- |
| `key_piece` | `sprites/key_piece.png` | 16 × 16 | dull gold, **K** | One fragment. Every defeated enemy drops exactly one. |
| `final_key` | `sprites/final_key.png` | 16 × 16 | bright gold, **F** | The assembled key. Replaces the pieces once you hold them all — it should look like the pieces joined up. |
| `skip_potion` | `sprites/skip_potion.png` | 16 × 16 | blue, **S** | Skips the current question, no damage either way. |
| `health_potion` | `sprites/health_potion.png` | 16 × 16 | green, **H** | Restores HP. |

### `ui` — transparent background

| Key | File | Size | Placeholder | What it is |
| --- | --- | --- | --- | --- |
| `heart_full` | `sprites/heart_full.png` | 16 × 16 | pink heart | One point of remaining HP. The bar draws one per HP, so a Plus run draws 15 of them — keep it readable when repeated. |
| `heart_empty` | `sprites/heart_empty.png` | 16 × 16 | grey heart | One point of lost HP. |
| `panel_frame` | `sprites/panel_frame.png` | 24 × 24 | purple frame | A 9-slice border used as `border-image` around the battle and inventory panels. **Corners must be 8 × 8** and the middle must be transparent; the edges are stretched. |

## Animated sprites (optional)

Any entry can become a horizontal animation strip. Change the value from a
path string to an object:

```json
"actors": {
  "player_down": { "file": "sprites/player_down.png", "frames": 4 }
}
```

The strip is one image, `frames × width` wide and one frame tall — so a 4-frame
32 × 32 player walk cycle is a single **128 × 32** PNG, frames left to right.
`frames` defaults to `1` when omitted, which is why every current entry can
stay a plain string. The renderer cycles frames on a fixed timer and, for
players who have **Reduce Motion** enabled, holds frame 0 instead.

## Conventions worth keeping

- **PNG, RGBA, no colour profile.** Indexed PNGs work too.
- **Design for both themes.** The page follows StudyQuest's light/dark toggle,
  and the board sits on `--surface` in both. Art that is pure white or pure
  black will vanish in one of them; give shapes their own outline.
- **One light source**, top-left, if you want the tiles to sit together.
- **The placeholders are not a style guide.** Flat colours and letters exist
  only so the wiring is visible. Nothing about them needs preserving except the
  dimensions and the transparency rules above.
