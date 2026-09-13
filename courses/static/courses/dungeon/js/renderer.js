/* Dungeon Quest — renderer.
 *
 * Owns the sprite manifest and the canvas. Two rules it never breaks:
 *
 *  1. No image path is written here. Every image is looked up by logical key
 *     through sprites.json, so replacing art is a file drop (see ART_GUIDE.md).
 *  2. No pixel size is written here. Tile and icon sizes arrive from the
 *     server, which reads them from dungeon/combat_config.py.
 */
(function (global) {
    'use strict';

    var DungeonQuest = global.DungeonQuest = global.DungeonQuest || {};

    // Tile code -> manifest key. The codes themselves are defined in
    // dungeon/rooms.py; this is the only place they map to art.
    var TILE_KEYS = {
        '.': 'floor',
        'S': 'floor',
        '#': 'wall',
        '~': 'grass'
    };

    var FACING_KEYS = {
        up: 'player_up',
        down: 'player_down',
        left: 'player_left',
        right: 'player_right'
    };

    // Default milliseconds per animation frame; sprites.json may override it
    // with a top-level "frame_ms", so animation speed stays an art decision.
    var DEFAULT_FRAME_MS = 220;

    function Renderer(options) {
        this.canvas = options.canvas;
        this.context = this.canvas.getContext('2d');
        this.manifestUrl = options.manifestUrl;
        this.tileSize = options.display.tile_size;
        this.minScale = options.display.min_scale;
        this.maxScale = options.display.max_scale;
        this.verticalReserve = options.display.vertical_reserve;
        this.reducedMotion = !!options.reducedMotion;

        this.manifest = null;
        this.frameMs = DEFAULT_FRAME_MS;
        this.images = {};
        this.scale = this.minScale;
        this.state = null;
        this.tween = null;
        this.flashTile = null;
        this.frameStartedAt = Date.now();
    }

    /* ----- manifest ------------------------------------------------ */

    Renderer.prototype.load = function () {
        var self = this;
        return fetch(this.manifestUrl, { credentials: 'same-origin' })
            .then(function (response) {
                if (!response.ok) { throw new Error('Sprite manifest unavailable.'); }
                return response.json();
            })
            .then(function (manifest) {
                self.manifest = manifest;
                self.frameMs = parseInt(manifest.frame_ms, 10) || DEFAULT_FRAME_MS;
                if (manifest.tile_size && manifest.tile_size !== self.tileSize) {
                    // The server is authoritative; a mismatch means the manifest
                    // and combat_config.TILE_SIZE have drifted apart.
                    console.warn(
                        'sprites.json declares tile_size ' + manifest.tile_size +
                        ' but the server renders at ' + self.tileSize + '.'
                    );
                }
                return self.preload();
            });
    };

    Renderer.prototype.entry = function (group, key) {
        var section = (this.manifest && this.manifest[group]) || {};
        var value = section[key];
        if (!value) { return null; }
        if (typeof value === 'string') {
            return {
                file: value,
                frames: 1,
                frame_width: null,
                frame_height: null,
                anchor: null,
                render_scale: 1.0,
                slice: 0
            };
        }
        return {
            file: value.file,
            frames: Math.max(parseInt(value.frames, 10) || 1, 1),
            frame_width: parseInt(value.frame_width, 10) || null,
            frame_height: parseInt(value.frame_height, 10) || null,
            anchor: value.anchor || null,
            render_scale: parseFloat(value.render_scale) || 1.0,
            slice: parseInt(value.slice, 10) || 0
        };
    };

    Renderer.prototype.resolve = function (file) {
        // Paths in the manifest are relative to the manifest itself.
        return new URL(file, new URL(this.manifestUrl, global.location.href)).href;
    };

    /** Public: the URL for a logical key, for the HUD's <img> elements. */
    Renderer.prototype.urlFor = function (group, key) {
        var entry = this.entry(group, key);
        return entry ? this.resolve(entry.file) : '';
    };

    Renderer.prototype.preload = function () {
        var self = this;
        var groups = ['tiles', 'actors', 'items', 'ui', 'portraits'];
        var pending = [];

        groups.forEach(function (group) {
            var section = self.manifest[group] || {};
            Object.keys(section).forEach(function (key) {
                var entry = self.entry(group, key);
                if (!entry) { return; }
                pending.push(new Promise(function (resolve) {
                    var image = new Image();
                    image.onload = function () {
                        self.images[group + ':' + key] = { image: image, frames: entry.frames };
                        resolve();
                    };
                    image.onerror = function () {
                        console.warn('Missing dungeon sprite: ' + group + '.' + key);
                        resolve();
                    };
                    image.src = self.resolve(entry.file);
                }));
            });
        });

        return Promise.all(pending);
    };

    /* ----- geometry ------------------------------------------------ */

    Renderer.prototype.fitScale = function () {
        var room = this.state && this.state.room;
        if (!room || !room.width) { return; }

        var available = this.canvas.parentElement
            ? this.canvas.parentElement.clientWidth
            : room.width * this.tileSize;

        // Fit both ways: a tall room at 3x would otherwise run off the bottom of
        // the screen and make the player scroll to see where they are walking.
        // The reserve (page chrome around the board) comes from combat_config.
        var verticalRoom = (global.innerHeight || 800) - this.verticalReserve;

        var scale = Math.min(
            Math.floor(available / (room.width * this.tileSize)),
            Math.floor(verticalRoom / (room.height * this.tileSize))
        );
        scale = Math.min(Math.max(scale, this.minScale), this.maxScale);

        var cssWidth = room.width * this.tileSize * scale;
        var cssHeight = room.height * this.tileSize * scale;
        var pixelRatio = Math.min(global.devicePixelRatio || 1, 2);

        var canvasWidth = Math.round(cssWidth * pixelRatio);
        var canvasHeight = Math.round(cssHeight * pixelRatio);

        if (this.canvas.width === canvasWidth && this.canvas.height === canvasHeight && this.scale === scale) { return; }

        this.scale = scale;
        this.canvas.width = canvasWidth;
        this.canvas.height = canvasHeight;
        this.canvas.style.width = cssWidth + 'px';
        this.canvas.style.height = cssHeight + 'px';

        this.context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
        this.context.imageSmoothingEnabled = false;
    };

    /* ----- drawing ------------------------------------------------- */

    Renderer.prototype.setState = function (state) {
        this.state = state;
        this.fitScale();
        this.draw();
    };

    /** Slide the player between two tiles; instant when motion is reduced. */
    Renderer.prototype.tweenTo = function (from, to, durationMs) {
        if (this.reducedMotion) { this.draw(); return Promise.resolve(); }

        var self = this;
        var startedAt = performance.now();
        this.tween = { from: from, to: to, progress: 0 };

        return new Promise(function (resolve) {
            function step(now) {
                var progress = Math.min((now - startedAt) / durationMs, 1);
                self.tween.progress = progress;
                self.draw();
                if (progress < 1) {
                    requestAnimationFrame(step);
                } else {
                    self.tween = null;
                    self.draw();
                    resolve();
                }
            }
            requestAnimationFrame(step);
        });
    };

    /** Flash an encounter highlight over an encounter tile before battle starts. */
    Renderer.prototype.flashEncounter = function (tileX, tileY, durationMs) {
        if (this.reducedMotion) { return Promise.resolve(); }

        var self = this;
        var startedAt = performance.now();
        var duration = durationMs || 240;
        this.flashTile = { x: tileX, y: tileY, progress: 0 };

        return new Promise(function (resolve) {
            function step(now) {
                var progress = Math.min((now - startedAt) / duration, 1);
                self.flashTile.progress = progress;
                self.draw();
                if (progress < 1) {
                    requestAnimationFrame(step);
                } else {
                    self.flashTile = null;
                    self.draw();
                    resolve();
                }
            }
            requestAnimationFrame(step);
        });
    };

    Renderer.prototype.sprite = function (group, key) {
        return this.images[group + ':' + key] || null;
    };

    Renderer.prototype.blit = function (group, key, tileX, tileY) {
        var sprite = this.sprite(group, key);
        if (!sprite) { return; }

        var size = this.tileSize;
        var scale = this.scale;
        var frameWidth = sprite.image.width / sprite.frames;
        var frame = 0;

        if (sprite.frames > 1 && !this.reducedMotion) {
            if (key.indexOf('player_') === 0 && !this.tween) {
                frame = 0;
            } else {
                frame = Math.floor((Date.now() - this.frameStartedAt) / this.frameMs) % sprite.frames;
            }
        }

        this.context.drawImage(
            sprite.image,
            frame * frameWidth, 0, frameWidth, sprite.image.height,
            Math.round(tileX * size * scale),
            Math.round(tileY * size * scale),
            size * scale,
            size * scale
        );
    };

    Renderer.prototype.drawActor = function (group, key, tileX, tileY) {
        var sprite = this.sprite(group, key);
        var entry = this.entry(group, key);
        if (!sprite || !entry) { return; }

        var size = this.tileSize;
        var scale = this.scale;
        var renderedTileSize = size * scale;

        var frameWidth = entry.frame_width || Math.round(sprite.image.width / sprite.frames);
        var frameHeight = entry.frame_height || sprite.image.height;
        var anchor = entry.anchor || { x: 0.5, y: 1.0 };
        var renderScale = entry.render_scale || 1.0;

        var frame = 0;
        if (sprite.frames > 1 && !this.reducedMotion) {
            if (key.indexOf('player_') === 0 && !this.tween) {
                frame = 0;
            } else {
                frame = Math.floor((Date.now() - this.frameStartedAt) / this.frameMs) % sprite.frames;
            }
        }

        var renderedActorWidth = Math.round((frameWidth / size) * renderedTileSize * renderScale);
        var renderedActorHeight = Math.round((frameHeight / size) * renderedTileSize * renderScale);

        var cellCenterX = tileX * renderedTileSize + (renderedTileSize / 2);
        var cellBottomY = tileY * renderedTileSize + renderedTileSize;

        var drawX = Math.round(cellCenterX - (renderedActorWidth * anchor.x));
        var drawY = Math.round(cellBottomY - (renderedActorHeight * anchor.y));

        this.context.drawImage(
            sprite.image,
            frame * frameWidth, 0, frameWidth, frameHeight,
            drawX, drawY, renderedActorWidth, renderedActorHeight
        );
    };

    Renderer.prototype.draw = function () {
        var state = this.state;
        if (!state || !state.room || !state.room.grid.length) { return; }

        var context = this.context;
        context.imageSmoothingEnabled = false;
        var cssWidth = state.room.width * this.tileSize * this.scale;
        var cssHeight = state.room.height * this.tileSize * this.scale;
        context.clearRect(0, 0, cssWidth, cssHeight);

        // 1. Terrain layer
        var grid = state.room.grid;
        for (var y = 0; y < grid.length; y += 1) {
            for (var x = 0; x < grid[y].length; x += 1) {
                var code = grid[y].charAt(x);
                if (code === 'D') {
                    this.blit('tiles', state.room.door_unlocked ? 'door_unlocked' : 'door_locked', x, y);
                } else {
                    this.blit('tiles', TILE_KEYS[code] || 'floor', x, y);
                }
            }
        }

        // 2. Y-Sorted actors layer
        var actors = [];
        (state.enemies || []).forEach(function (enemy) {
            if (!enemy.is_defeated) {
                actors.push({
                    group: 'actors',
                    key: enemy.role ? ('enemy_' + enemy.role) : 'enemy_default',
                    x: enemy.x,
                    y: enemy.y
                });
            }
        });

        var position = this.playerPosition();
        actors.push({
            group: 'actors',
            key: FACING_KEYS[state.player.facing] || 'player_down',
            x: position.x,
            y: position.y
        });

        actors.sort(function (a, b) {
            return a.y - b.y;
        });

        actors.forEach(function (actor) {
            this.drawActor(actor.group, actor.key, actor.x, actor.y);
        }, this);

        // 3. Effects / Overlays
        if (this.flashTile) {
            var size = this.tileSize * this.scale;
            var p = this.flashTile.progress || 0;
            var alpha = Math.sin((1 - p) * Math.PI * 0.5) * 0.7;
            context.save();
            context.fillStyle = 'rgba(255, 220, 100, ' + alpha + ')';
            context.fillRect(this.flashTile.x * size, this.flashTile.y * size, size, size);
            context.strokeStyle = 'rgba(239, 68, 68, ' + alpha + ')';
            context.lineWidth = Math.max(2, Math.round(2 * this.scale));
            context.strokeRect(this.flashTile.x * size, this.flashTile.y * size, size, size);
            context.restore();
        }
    };

    Renderer.prototype.playerPosition = function () {
        var player = this.state.player;
        if (!this.tween) { return { x: player.x, y: player.y }; }

        var t = this.tween;
        return {
            x: t.from.x + (t.to.x - t.from.x) * t.progress,
            y: t.from.y + (t.to.y - t.from.y) * t.progress
        };
    };

    /** A spoken description of the player's surroundings, for screen readers. */
    Renderer.prototype.describeSurroundings = function () {
        var state = this.state;
        if (!state) { return ''; }

        var names = { '.': 'floor', 'S': 'floor', '#': 'wall', '~': 'tall grass', 'D': 'the exit door' };
        var grid = state.room.grid;
        var parts = [];

        [['north', 0, -1], ['south', 0, 1], ['west', -1, 0], ['east', 1, 0]].forEach(function (dir) {
            var x = state.player.x + dir[1];
            var y = state.player.y + dir[2];
            var row = grid[y] || '';
            var code = row.charAt(x) || '#';
            parts.push(dir[0] + ': ' + (names[code] || 'wall'));
        });

        var standing = (grid[state.player.y] || '').charAt(state.player.x);
        var living = (state.enemies || []).filter(function (e) { return !e.is_defeated; }).length;

        return 'Standing on ' + (names[standing] || 'floor') + '. ' + parts.join(', ') +
            '. ' + living + ' enem' + (living === 1 ? 'y' : 'ies') + ' remaining.';
    };

    DungeonQuest.Renderer = Renderer;
}(window));
