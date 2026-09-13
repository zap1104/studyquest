# AGENTS.md — StudyQuest / Dungeon Quest

This file is project context for an AI coding agent (Google Antigravity or
similar). Read this in full before making any change under `dungeon/`. It
exists so you don't have to rediscover the architecture or re-litigate
product decisions that are already settled.

## What this project is

StudyQuest is a Django app (server-rendered templates + vanilla JS, SQLite
in dev) that turns uploaded study material into AI-generated courses,
chapters, and quizzes. XP, levels, streaks, and a leaderboard already exist
and are load-bearing — anything new must plug into them, not fork them.

**Dungeon Quest** is a game mode inside this app: it turns a chapter's quiz
questions into combat encounters in a small grid-based dungeon room. It is
currently mid-build. The roadmap for it lives in
`dungeon-quest-roadmap.md` in this same directory — read that too before
picking up work, so you know which sprint is active and what's explicitly
out of scope right now.

## Hard constraints — do not violate these regardless of what a task asks for

1. **The server is authoritative, always.** Movement validity, encounters,
   grading, damage, drops, inventory changes, and the exit/unlock condition
   are decided in `dungeon/services.py`, never trusted from the client. Never
   serialize a correct answer, an unanswered question ID, or any other
   client-checkable fact that would let someone infer or fake a result.
2. **`combat_config.py` is the single source of truth for every tunable
   number.** HP, damage, drop rates, XP, pixel sizes — if it's a number that
   affects balance or presentation scale, it lives there. Never hardcode a
   number in a view, template, or JS file that duplicates or shadows a value
   from this module.
3. **`DungeonRun` stays scoped to one quiz / one chapter expedition.** Do not
   grow it into a multi-room or multi-chapter aggregate. A course-level
   campaign composes multiple `DungeonRun` records and reads their outcomes;
   it does not replace this model or make it track more than one room.
4. **XP goes through the existing `UserProfile.award_xp` ledger**, following
   the same idempotency pattern already in `finish_run` (conditional update
   guarding `xp_awarded`, so a double POST or a race can't double-pay). Any
   new reward (Review Run mastery XP, campaign completion rewards) must
   follow the same guard pattern, not invent a new one.
5. **Academic progress and dungeon progress are two separate state tracks.**
   Whether a chapter's dungeon is *playable* depends only on whether the
   chapter is academically unlocked. Whether a campaign node is *complete*
   depends only on whether that chapter's dungeon has been cleared. Never
   let one control the other, and never overload `ChapterCompletion` (or
   equivalent) to mean both.
6. **Free and Plus plans stay academically equivalent.** Plan differences
   are comfort/capacity/cosmetic (starting HP, potion capacity, room
   themes, run history depth) — never a difference that changes whether
   correct knowledge is required to succeed. Current numbers: Free 5 HP /
   2 HP per potion / 3 max potions; Plus 7 HP / 3 HP per potion / 5 max
   potions.
7. **A run must be resumable.** Anything you add to run state must survive
   a page refresh or a closed tab without losing or duplicating progress —
   follow the existing `active_enemy` FK + `_lock()`/`refresh_from_db()`
   pattern in `services.py`.

## Current architecture (as of last review)

- `dungeon/models.py` — `DungeonRun` (HP, position, room_data JSON, seed,
  rules_key, active_enemy FK), `DungeonEnemy` (per-run monster with a dealt
  hand of question IDs, answered subset, HP), `DungeonInventory` (per-run
  counters: key pieces, skip potions, health potions).
- `dungeon/rooms.py` — ASCII grid rooms (`.` floor, `#` wall, `~` grass/
  encounter tile, `D` door, `S` spawn). First-ever run uses a hand-authored
  starter room; every run after that is procedurally carved (drunkard's
  walk), always connected/solvable by construction.
- `dungeon/combat_config.py` — every balance number, keyed by plan
  (`free`/`plus`). Includes an already-wired-but-currently-no-op hook
  (`_scale_hp_for_quiz_length`) for scaling HP by quiz length later.
- `dungeon/services.py` — the only place game rules are decided. Launch
  catalog building, run start/resume, movement, battle resolution, item use,
  exit/finish, and all client-safe serialization (never leaks unanswered
  question IDs or correct-answer data).
- `dungeon/views.py` — thin HTTP layer; every view proves ownership (404,
  never 403, to avoid confirming another user's content exists), then
  delegates to `services.py`.
- Templates: `launch.html` (quiz picker), `room.html` (the live run —
  canvas-rendered board via `renderer.js`/`engine.js`/`battle.js`/
  `inventory.js`, bootstrapped from a `json_script`-escaped state blob),
  `summary.html` (post-run stats).

## Current focus

Check `dungeon-quest-roadmap.md` for the active sprint. As of this writing:
**Phase 0, Sprint 1 — chapter-run presentation.** No new models this phase.
The work is UI/UX: fit the run into one viewport, replace letter tiles with
real visuals, add a persistent objective readout, make combat feedback
visible, add touch controls. Do not start Review Run, campaign, or enemy-role
work until Sprint 1's done criteria (in the roadmap file) are met.

## Working agreement for this repo

- Prefer editing `combat_config.py` over adding a new constant elsewhere,
  even for a "just this one small number."
- Any new persistent model needs a migration and should follow the existing
  naming/ownership pattern (`user`/`run` FKs, `Meta.constraints` for
  uniqueness rather than app-level checks where a race is possible).
- Match the existing test style: `SimpleTestCase` for pure-function config
  logic (see `test_combat_config.py`), full `TestCase` with DB access for
  anything touching `DungeonRun`/`DungeonEnemy` state.
- If a task seems to require breaking constraint 1–7 above to be "simpler,"
  stop and flag it instead of proceeding — that usually means the task
  description assumed something about the architecture that isn't true.
