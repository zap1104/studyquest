/**
 * Course Forge Minigame — Unified Input Engine.
 *
 * Implements:
 * - Normalized keyboard movement (WASD + Arrow Keys).
 * - Pointer-captured virtual joystick with dead-zone clamping for mobile.
 * - Edge-triggered action queue (Attack/Chop, Upgrade, Pause, Mute).
 * - Complete teardown and listener cleanup.
 */
(function (window) {
    'use strict';

    function createInput() {
        var listeners = [];
        var activeKeys = new Set();
        var queuedActions = new Set();
        var enabled = true;

        var joyState = {
            active: false,
            pointerId: null,
            startX: 0,
            startY: 0,
            currX: 0,
            currY: 0,
            dx: 0,
            dy: 0,
            dist: 0
        };

        var MAX_JOY_RADIUS = 42;
        var DEAD_ZONE = 0.16;

        function addEventListenerSafe(target, type, handler, options) {
            target.addEventListener(type, handler, options);
            listeners.push(function () {
                target.removeEventListener(type, handler, options);
            });
        }

        // --- Keyboard Handlers ---
        function onKeyDown(e) {
            // Ignore keystrokes when typing in inputs/textareas
            var tag = (e.target && e.target.tagName) || '';
            if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;

            // Input is disabled once the Course is ready. Swallow movement keys
            // entirely so they are not tracked or queued behind the overlay.
            if (!enabled) return;

            var key = e.key.toLowerCase();
            if (['w', 'a', 's', 'd', 'arrowup', 'arrowleft', 'arrowdown', 'arrowright'].includes(key)) {
                activeKeys.add(key);
                e.preventDefault();
            } else if (key === ' ' || key === 'enter') {
                queuedActions.add('action');
                e.preventDefault();
            } else if (key === 'e' || key === 'u') {
                queuedActions.add('upgrade');
                e.preventDefault();
            } else if (key === 'p' || key === 'escape') {
                queuedActions.add('pause');
                e.preventDefault();
            } else if (key === 'm') {
                queuedActions.add('mute');
                e.preventDefault();
            }
        }

        function onKeyUp(e) {
            var key = e.key.toLowerCase();
            activeKeys.delete(key);
        }

        function onBlur() {
            activeKeys.clear();
            resetJoystick();
        }

        addEventListenerSafe(window, 'keydown', onKeyDown);
        addEventListenerSafe(window, 'keyup', onKeyUp);
        addEventListenerSafe(window, 'blur', onBlur);

        // --- Virtual Joystick Handlers ---
        function bindJoystickElements(baseEl, knobEl) {
            if (!baseEl || !knobEl) return;

            function onPointerDown(e) {
                if (joyState.active) return;
                joyState.active = true;
                joyState.pointerId = e.pointerId;

                var rect = baseEl.getBoundingClientRect();
                joyState.startX = rect.left + rect.width / 2;
                joyState.startY = rect.top + rect.height / 2;
                joyState.currX = e.clientX;
                joyState.currY = e.clientY;

                baseEl.setPointerCapture(e.pointerId);
                updateJoystickVisual(knobEl);
                e.preventDefault();
            }

            function onPointerMove(e) {
                if (!joyState.active || e.pointerId !== joyState.pointerId) return;
                joyState.currX = e.clientX;
                joyState.currY = e.clientY;
                updateJoystickVisual(knobEl);
                e.preventDefault();
            }

            function onPointerUp(e) {
                if (e.pointerId === joyState.pointerId) {
                    resetJoystick(knobEl);
                }
            }

            addEventListenerSafe(baseEl, 'pointerdown', onPointerDown);
            addEventListenerSafe(baseEl, 'pointermove', onPointerMove);
            addEventListenerSafe(baseEl, 'pointerup', onPointerUp);
            addEventListenerSafe(baseEl, 'pointercancel', onPointerUp);
            addEventListenerSafe(baseEl, 'lostpointercapture', onPointerUp);
        }

        function updateJoystickVisual(knobEl) {
            var rawDx = joyState.currX - joyState.startX;
            var rawDy = joyState.currY - joyState.startY;
            var rawDist = Math.hypot(rawDx, rawDy);

            if (rawDist > MAX_JOY_RADIUS) {
                joyState.dx = (rawDx / rawDist) * MAX_JOY_RADIUS;
                joyState.dy = (rawDy / rawDist) * MAX_JOY_RADIUS;
                joyState.dist = 1;
            } else {
                joyState.dx = rawDx;
                joyState.dy = rawDy;
                joyState.dist = rawDist / MAX_JOY_RADIUS;
            }

            if (knobEl) {
                knobEl.style.transform = 'translate(' + joyState.dx + 'px, ' + joyState.dy + 'px)';
            }
        }

        function resetJoystick(knobEl) {
            joyState.active = false;
            joyState.pointerId = null;
            joyState.dx = 0;
            joyState.dy = 0;
            joyState.dist = 0;
            if (knobEl) {
                knobEl.style.transform = 'translate(0px, 0px)';
            }
        }

        function bindActionButton(btnEl, actionName) {
            if (!btnEl) return;
            var name = actionName || 'action';

            function onPointerDown(e) {
                queuedActions.add(name);
                btnEl.classList.add('is-pressed');
                e.preventDefault();
            }

            function onPointerUp() {
                btnEl.classList.remove('is-pressed');
            }

            addEventListenerSafe(btnEl, 'pointerdown', onPointerDown);
            addEventListenerSafe(btnEl, 'pointerup', onPointerUp);
            addEventListenerSafe(btnEl, 'pointercancel', onPointerUp);
        }

        // --- Public Query API ---
        function getMoveVector() {
            if (!enabled) return { x: 0, y: 0 };

            var x = 0;
            var y = 0;

            // 1. Keyboard vector
            if (activeKeys.has('a') || activeKeys.has('arrowleft')) x -= 1;
            if (activeKeys.has('d') || activeKeys.has('arrowright')) x += 1;
            if (activeKeys.has('w') || activeKeys.has('arrowup')) y -= 1;
            if (activeKeys.has('s') || activeKeys.has('arrowdown')) y += 1;

            if (x !== 0 || y !== 0) {
                var len = Math.hypot(x, y);
                return { x: x / len, y: y / len };
            }

            // 2. Touch joystick vector with dead-zone
            if (joyState.active && joyState.dist >= DEAD_ZONE) {
                var jx = joyState.dx / MAX_JOY_RADIUS;
                var jy = joyState.dy / MAX_JOY_RADIUS;
                var mag = Math.hypot(jx, jy);
                if (mag > 1) {
                    jx /= mag;
                    jy /= mag;
                }
                return { x: jx, y: jy };
            }

            return { x: 0, y: 0 };
        }

        function consume(actionName) {
            if (!enabled) {
                // Drain rather than leak: a queued action must never survive a
                // disabled period and fire on a later resume.
                queuedActions.clear();
                return false;
            }
            if (queuedActions.has(actionName)) {
                queuedActions.delete(actionName);
                return true;
            }
            return false;
        }

        function trigger(actionName) {
            if (!enabled) return;
            queuedActions.add(actionName);
        }

        /**
         * Enable or disable all game input.
         * Called with false the moment the Course becomes ready, so the
         * learner cannot keep moving or buying upgrades behind the Course
         * Ready overlay. Releasing keys, the joystick and the action queue
         * here means nothing stale can fire if the game is ever resumed.
         */
        function setEnabled(next) {
            enabled = !!next;
            if (!enabled) {
                activeKeys.clear();
                queuedActions.clear();
                joyState.active = false;
                joyState.pointerId = null;
                joyState.dx = 0;
                joyState.dy = 0;
                joyState.dist = 0;
            }
        }

        function isEnabled() {
            return enabled;
        }

        function destroy() {
            listeners.forEach(function (off) { off(); });
            listeners.length = 0;
            enabled = false;
            activeKeys.clear();
            queuedActions.clear();
            joyState.active = false;
        }

        return {
            getMoveVector: getMoveVector,
            consume: consume,
            trigger: trigger,
            setEnabled: setEnabled,
            isEnabled: isEnabled,
            bindJoystickElements: bindJoystickElements,
            bindActionButton: bindActionButton,
            destroy: destroy
        };
    }

    window.CourseForgeInput = {
        create: createInput
    };
})(window);
