/* Dungeon Quest — engine.
 *
 * Boots the other three modules, owns input (arrows, WASD, on-screen D-pad)
 * and talks to the server. It holds no game rules: every move, encounter,
 * grade, drop and unlock is decided server-side and this file renders the
 * answer it gets back.
 */
(function (global) {
    'use strict';

    var DungeonQuest = global.DungeonQuest = global.DungeonQuest || {};

    var KEY_DIRECTIONS = {
        ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right',
        w: 'up', a: 'left', s: 'down', d: 'right',
        W: 'up', A: 'left', S: 'down', D: 'right'
    };

    var STEP_MS = 120;

    function csrfToken() {
        var match = document.cookie.match(/(^|;)\s*csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[2]) : '';
    }

    function Api(urls) {
        this.urls = urls;
    }

    Api.prototype.post = function (url, body) {
        return fetch(url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken(),
                'X-Requested-With': 'XMLHttpRequest'
            },
            body: JSON.stringify(body || {})
        }).then(function (response) {
            return response.json().catch(function () { return {}; }).then(function (data) {
                if (!response.ok || data.ok === false) {
                    throw new Error(data.error || 'Something went wrong.');
                }
                return data;
            });
        });
    };

    Api.prototype.move = function (direction) {
        return this.post(this.urls.move, { direction: direction });
    };

    Api.prototype.answer = function (questionId, answer) {
        return this.post(this.urls.answer, { question_id: questionId, answer: answer });
    };

    Api.prototype.useItem = function (item) {
        return this.post(this.urls.use_item, { item: item });
    };

    function Engine(options) {
        this.state = options.state;
        this.state.player.facing = 'down';
        this.urls = options.urls;
        this.api = new Api(options.urls);

        this.reducedMotion = global.matchMedia
            ? global.matchMedia('(prefers-reduced-motion: reduce)').matches
            : false;

        this.liveRegion = document.querySelector('[data-live-region]');
        this.boardDescription = document.querySelector('[data-board-description]');
        this.canvas = document.getElementById('dungeon-board');
        this.dpad = document.querySelector('[data-dpad]');
        this.blockedEl = document.querySelector('[data-blocked-note]');

        this.moving = false;
        this.announce = this.announce.bind(this);
    }

    Engine.prototype.start = function () {
        var self = this;

        this.renderer = new DungeonQuest.Renderer({
            canvas: this.canvas,
            manifestUrl: this.urls.sprites,
            display: this.state.display,
            reducedMotion: this.reducedMotion
        });

        this.battle = new DungeonQuest.Battle({
            root: document.querySelector('[data-battle]'),
            api: this.api,
            announce: this.announce,
            reducedMotion: this.reducedMotion,
            onUpdated: this.applyTurn.bind(this),
            onResolved: this.handleTurnResolved.bind(this)
        });

        this.inventory = new DungeonQuest.Inventory({
            root: document.querySelector('[data-hud]'),
            toast: document.querySelector('[data-toast]'),
            renderer: this.renderer,
            api: this.api,
            announce: this.announce,
            onUsed: this.handleItemUsed.bind(this)
        });

        return this.renderer.load().then(function () {
            self.renderer.setState(self.state);
            self.inventory.render(self.state);
            self.describeBoard();
            self.bindInput();

            if (self.state.battle) {
                self.battle.open(self.state.battle);
                self.announce('A battle is already in progress.');
            }
        }).catch(function (error) {
            self.announce('The dungeon art could not be loaded: ' + error.message);
        });
    };

    /* ----- input --------------------------------------------------- */

    Engine.prototype.bindInput = function () {
        var self = this;

        document.addEventListener('keydown', function (event) {
            if (self.battle.isOpen()) { return; }

            var target = event.target;
            var typing = target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA');
            if (typing) { return; }

            var direction = KEY_DIRECTIONS[event.key];
            if (!direction) { return; }

            event.preventDefault();
            self.move(direction);
        });

        if (this.dpad) {
            this.dpad.addEventListener('click', function (event) {
                var button = event.target.closest('[data-direction]');
                if (button) { self.move(button.dataset.direction); }
            });

            // CSS already shows the pad for coarse pointers and phone widths;
            // this catches touch-capable desktops that report a fine pointer.
            // Everyone can reach the buttons by tab regardless.
            if ('ontouchstart' in global || navigator.maxTouchPoints > 0) {
                this.dpad.dataset.visible = 'true';
            }
        }

        global.addEventListener('resize', function () {
            self.renderer.fitScale();
            self.renderer.draw();
        });
    };

    /* ----- movement ------------------------------------------------ */

    Engine.prototype.move = function (direction) {
        if (this.moving || this.battle.isOpen() || !this.state.is_active) { return; }

        var self = this;
        var from = { x: this.state.player.x, y: this.state.player.y };
        this.moving = true;
        this.state.player.facing = direction;

        this.api.move(direction).then(function (response) {
            var result = response.result;

            if (!result.moved) {
                self.renderer.setState(self.state);
                if (result.blocked_reason) {
                    self.showBlocked(result.blocked_reason);
                    self.announce(result.blocked_reason);
                }
                return null;
            }

            self.state.player.x = result.x;
            self.state.player.y = result.y;
            self.showBlocked('');

            return self.renderer.tweenTo(from, { x: result.x, y: result.y }, STEP_MS)
                .then(function () {
                    self.renderer.setState(self.state);
                    self.describeBoard();

                    if (result.exit) {
                        self.finish(result.exit);
                        return null;
                    }

                    if (result.encounter) {
                        self.state.battle = result.encounter;
                        self.inventory.render(self.state);
                        self.battle.open(result.encounter);
                        self.announce(
                            'An enemy blocks your path. Question 1 of ' +
                            result.encounter.questions_total + '.'
                        );
                    }
                    return null;
                });
        }).catch(function (error) {
            self.announce(error.message);
        }).finally(function () {
            self.moving = false;
        });
    };

    /* ----- battle outcomes ----------------------------------------- */

    /** Fold a graded turn into the world: HP, bag, the enemy, the door. Runs as
     *  soon as the server answers, so the board and HUD never lag the feedback. */
    Engine.prototype.applyTurn = function (result) {
        this.state.hp = result.hp;
        this.state.inventory = result.inventory;
        this.state.room.door_unlocked = result.door_unlocked;

        if (result.enemy) {
            this.state.enemies = this.state.enemies.map(function (enemy) {
                return enemy.index === result.enemy.index ? result.enemy : enemy;
            });
        }

        this.inventory.render(this.state);
        this.renderer.setState(this.state);

        if (result.drops) {
            this.inventory.showDrop(result.drops);
        }
    };

    /** Then, once the player has read the feedback and pressed Continue, move
     *  the run forward. */
    Engine.prototype.handleTurnResolved = function (result) {
        this.state.battle = result.battle || null;
        this.inventory.render(this.state);

        if (result.run_over) {
            this.finish(result.run_over);
            return;
        }

        if (result.enemy_defeated) {
            var remaining = this.state.enemies.filter(function (enemy) {
                return !enemy.is_defeated;
            }).length;

            this.announce(
                remaining === 0
                    ? 'The last enemy falls. The key is whole — head for the door.'
                    : 'Enemy defeated. ' + remaining + ' still lurking.'
            );
            this.describeBoard();
        }
    };

    Engine.prototype.handleItemUsed = function (itemKey, result) {
        if (itemKey === 'health_potion') {
            this.state.hp = result.hp;
            this.state.inventory = result.inventory;
            this.inventory.render(this.state);
            this.announce('You drink a health potion and recover ' + result.healed_by + ' HP.');
            return;
        }

        // A skip resolves through the same turn pipeline as an answer.
        this.battle.handleResult(result);
    };

    /* ----- endgame ------------------------------------------------- */

    Engine.prototype.finish = function (outcome) {
        this.state.is_active = false;
        this.battle.close();
        this.announce(
            outcome.cleared
                ? 'Run complete. You earned ' + outcome.xp_awarded + ' XP.'
                : 'You have fallen. The run is over.'
        );
        global.location.href = this.urls.summary;
    };

    /* ----- announcements ------------------------------------------- */

    Engine.prototype.announce = function (message) {
        if (this.liveRegion) { this.liveRegion.textContent = message; }
    };

    Engine.prototype.describeBoard = function () {
        var description = this.renderer.describeSurroundings();
        if (this.boardDescription) { this.boardDescription.textContent = description; }
        if (this.canvas) { this.canvas.setAttribute('aria-label', description); }
    };

    Engine.prototype.showBlocked = function (message) {
        if (this.blockedEl) {
            this.blockedEl.textContent = message;
            this.blockedEl.hidden = !message;
        }
    };

    DungeonQuest.Engine = Engine;

    DungeonQuest.boot = function (bootstrapId) {
        var node = document.getElementById(bootstrapId);
        if (!node) { return; }

        var data = JSON.parse(node.textContent);
        var engine = new Engine({ state: data.state, urls: data.urls });
        DungeonQuest.engine = engine;
        engine.start();
    };
}(window));
