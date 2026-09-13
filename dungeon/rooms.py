"""Room layouts as data.

A room is a 2D grid of single-character tile codes. Collision, encounter
eligibility and spawn/exit placement are all read off the tile code — no view,
service or renderer ever hardcodes a coordinate.

Tile codes
    ``.``  floor        walkable, inert
    ``#``  wall         solid
    ``~``  grass        walkable, rolls encounters
    ``D``  door         the exit; walkable only once the final key is assembled
    ``S``  spawn        walkable floor the player starts on
"""

from random import Random

from .combat_config import MAX_ENEMIES

TILE_FLOOR = "."
TILE_WALL = "#"
TILE_GRASS = "~"
TILE_DOOR = "D"
TILE_SPAWN = "S"

# Spawn is a floor tile with a marker role, so it walks like floor.
WALKABLE_TILES = frozenset({TILE_FLOOR, TILE_GRASS, TILE_SPAWN})
ENCOUNTER_TILES = frozenset({TILE_GRASS})

# The hand-authored starter room: a walled chamber with two grass thickets and
# a door on the north wall.
STARTER_ROOM_GRID = [
    "###########D###",
    "#.....#.......#",
    "#.~~..#..~~~..#",
    "#.~~..#..~~~..#",
    "#.....#...~~..#",
    "#..#..#.......#",
    "#..#..........#",
    "#..#####...##.#",
    "#........~~.#.#",
    "#...S....~~...#",
    "###############",
]


class RoomError(ValueError):
    """The grid could not be used as a room (no spawn, no door, no grass...)."""


def grid_size(grid):
    height = len(grid)
    width = max((len(row) for row in grid), default=0)
    return width, height


def tile_at(grid, x, y):
    if y < 0 or y >= len(grid):
        return TILE_WALL
    row = grid[y]
    if x < 0 or x >= len(row):
        return TILE_WALL
    return row[x]


def is_walkable(grid, x, y, *, door_unlocked=False):
    """Collision is a function of the tile code, never of a coordinate list."""
    tile = tile_at(grid, x, y)
    if tile == TILE_DOOR:
        return bool(door_unlocked)
    return tile in WALKABLE_TILES


def is_encounter_tile(grid, x, y):
    return tile_at(grid, x, y) in ENCOUNTER_TILES


def find_tiles(grid, code):
    return [
        (x, y)
        for y, row in enumerate(grid)
        for x, tile in enumerate(row)
        if tile == code
    ]


def find_single_tile(grid, code, label):
    matches = find_tiles(grid, code)
    if not matches:
        raise RoomError(f"Room grid has no {label} tile ({code!r}).")
    return matches[0]


def build_room(grid, *, enemy_count, seed=0):
    """Turn a grid into the ``room_data`` blob stored on a run.

    Enemy lairs are dealt to distinct grass tiles, spread out so a run never
    hides two enemies in the same thicket while another sits empty.
    """
    grid = [str(row) for row in grid]
    spawn_x, spawn_y = find_single_tile(grid, TILE_SPAWN, "spawn")
    door_x, door_y = find_single_tile(grid, TILE_DOOR, "door")

    grass = find_tiles(grid, TILE_GRASS)
    if enemy_count > len(grass):
        raise RoomError(
            f"Room has {len(grass)} grass tiles but {enemy_count} enemies to place."
        )

    rng = Random(seed)
    lairs = _spread_lairs(grass, enemy_count, rng)

    width, height = grid_size(grid)
    return {
        "grid": grid,
        "width": width,
        "height": height,
        "spawn": {"x": spawn_x, "y": spawn_y},
        "door": {"x": door_x, "y": door_y},
        "enemy_positions": [{"x": x, "y": y} for x, y in lairs],
    }


def _spread_lairs(grass, enemy_count, rng):
    """Greedy furthest-point placement: each lair is the candidate tile with the
    largest minimum distance to the lairs already chosen."""
    if enemy_count <= 0:
        return []

    candidates = list(grass)
    rng.shuffle(candidates)
    lairs = [candidates.pop()]

    while len(lairs) < enemy_count and candidates:
        best = max(
            candidates,
            key=lambda tile: min(
                abs(tile[0] - lx) + abs(tile[1] - ly) for lx, ly in lairs
            ),
        )
        candidates.remove(best)
        lairs.append(best)

    return lairs


def starter_room(*, enemy_count, seed=0):
    return build_room(STARTER_ROOM_GRID, enemy_count=enemy_count, seed=seed)


def generate_room(seed, enemy_count, *, width=15, height=11):
    """Procedurally carve a room for the given seed.

    The result is always solvable: the carved floor is one connected region,
    grass is only ever placed on carved floor, and the door is cut into a wall
    adjacent to that region.
    """
    if enemy_count > MAX_ENEMIES:
        raise RoomError(f"enemy_count {enemy_count} exceeds MAX_ENEMIES {MAX_ENEMIES}.")

    rng = Random(seed)
    width = max(width, 9)
    height = max(height, 9)

    grid = [[TILE_WALL for _ in range(width)] for _ in range(height)]

    # Drunkard's walk from the centre carves one guaranteed-connected cavern.
    x, y = width // 2, height // 2
    target_floor = int((width - 2) * (height - 2) * 0.55)
    carved = {(x, y)}
    grid[y][x] = TILE_FLOOR

    steps = 0
    max_steps = target_floor * 40
    while len(carved) < target_floor and steps < max_steps:
        steps += 1
        dx, dy = rng.choice([(1, 0), (-1, 0), (0, 1), (0, -1)])
        nx, ny = x + dx, y + dy
        if 1 <= nx <= width - 2 and 1 <= ny <= height - 2:
            x, y = nx, ny
            grid[y][x] = TILE_FLOOR
            carved.add((x, y))

    floor_tiles = sorted(carved)
    rng.shuffle(floor_tiles)

    spawn = floor_tiles.pop()
    grid[spawn[1]][spawn[0]] = TILE_SPAWN

    # Grass: enough thickets to hide every enemy several times over.
    grass_target = max(enemy_count * 4, len(floor_tiles) // 4)
    for gx, gy in floor_tiles[:grass_target]:
        grid[gy][gx] = TILE_GRASS

    door = _carve_door(grid, carved, spawn, rng)
    if door is None:
        raise RoomError("Could not place a door adjacent to the carved room.")

    rows = ["".join(row) for row in grid]
    return build_room(rows, enemy_count=enemy_count, seed=seed)


def _carve_door(grid, carved, spawn, rng):
    """Cut the exit into a border wall that touches the carved region, as far
    from the spawn as possible so the player has to cross the room."""
    height = len(grid)
    width = len(grid[0])
    candidates = []

    for cx, cy in carved:
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            nx, ny = cx + dx, cy + dy
            on_border = nx in (0, width - 1) or ny in (0, height - 1)
            if on_border and grid[ny][nx] == TILE_WALL:
                candidates.append((nx, ny))

    if not candidates:
        return None

    rng.shuffle(candidates)
    door = max(candidates, key=lambda t: abs(t[0] - spawn[0]) + abs(t[1] - spawn[1]))
    grid[door[1]][door[0]] = TILE_DOOR
    return door
