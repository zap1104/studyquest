/**
 * Course Forge Minigame — 2D Camera Engine.
 *
 * Implements:
 * - Logical 640x360 viewport into a larger 1920x1200 woodland world.
 * - Smooth exponential lerp target tracking with soft dead-zone.
 * - Movement-directional look-ahead lead.
 * - Hard clamping against world boundaries to prevent rendering empty void.
 * - Decaying screen-shake with prefers-reduced-motion accessibility bypass.
 * - Bidirectional coordinate conversion (World <-> Viewport Screen <-> Canvas Pixel).
 */
(function (window) {
    'use strict';

    var DEFAULT_VIEWPORT_W = 640;
    var DEFAULT_VIEWPORT_H = 360;
    var DEFAULT_WORLD_W = 1920;
    var DEFAULT_WORLD_H = 1200;

    function checkReducedMotion() {
        try {
            return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        } catch (e) {
            return false;
        }
    }

    function createCamera(options) {
        var opts = options || {};
        var viewportW = opts.viewportWidth || DEFAULT_VIEWPORT_W;
        var viewportH = opts.viewportHeight || DEFAULT_VIEWPORT_H;
        var worldW = opts.worldWidth || DEFAULT_WORLD_W;
        var worldH = opts.worldHeight || DEFAULT_WORLD_H;

        // Follow & interpolation parameters
        var followSpeed = opts.followSpeed || 5.5; // Lerp smoothing factor
        var lookAheadLead = opts.lookAheadLead || 0.18; // Seconds of velocity lead
        var maxLookAhead = opts.maxLookAhead || 48; // Max lead offset in pixels
        var deadZoneRadius = opts.deadZoneRadius || 8; // Soft dead zone in pixels

        // Camera top-left position in world coordinates
        var x = (worldW - viewportW) / 2;
        var y = (worldH - viewportH) / 2;

        // Current target position (smoothed)
        var targetX = x + viewportW / 2;
        var targetY = y + viewportH / 2;

        // Screen shake state
        var shakeTimer = 0;
        var shakeDuration = 0;
        var shakeIntensity = 0;
        var shakeOffsetX = 0;
        var shakeOffsetY = 0;

        function shake(intensity, duration) {
            if (checkReducedMotion()) return; // A11y: completely skip screen shake
            shakeIntensity = Math.min(16, intensity || 4);
            shakeDuration = duration || 0.2;
            shakeTimer = shakeDuration;
        }

        function update(dt, target) {
            if (!target) return;

            // 1. Calculate desired focus point with look-ahead
            var desiredX = target.x;
            var desiredY = target.y;

            if (target.vx) {
                var leadX = target.vx * lookAheadLead;
                leadX = Math.max(-maxLookAhead, Math.min(maxLookAhead, leadX));
                desiredX += leadX;
            }
            if (target.vy) {
                var leadY = target.vy * lookAheadLead;
                leadY = Math.max(-maxLookAhead, Math.min(maxLookAhead, leadY));
                desiredY += leadY;
            }

            // 2. Soft dead-zone check from current center
            var currentCenterX = x + viewportW / 2;
            var currentCenterY = y + viewportH / 2;
            var distToDesired = Math.hypot(desiredX - currentCenterX, desiredY - currentCenterY);

            if (distToDesired > deadZoneRadius) {
                // Exponential decay lerp: frame-rate independent
                var t = 1 - Math.exp(-followSpeed * Math.min(dt, 0.1));
                targetX += (desiredX - targetX) * t;
                targetY += (desiredY - targetY) * t;
            }

            // 3. Center viewport on target position
            x = targetX - viewportW / 2;
            y = targetY - viewportH / 2;

            // 4. Clamp camera bounds within the world
            var maxCamX = Math.max(0, worldW - viewportW);
            var maxCamY = Math.max(0, worldH - viewportH);
            x = Math.max(0, Math.min(maxCamX, x));
            y = Math.max(0, Math.min(maxCamY, y));

            // Keep target position aligned with clamped bounds
            targetX = x + viewportW / 2;
            targetY = y + viewportH / 2;

            // 5. Update screen shake
            if (shakeTimer > 0) {
                shakeTimer -= dt;
                var progress = Math.max(0, shakeTimer / (shakeDuration || 0.001));
                var currentMag = shakeIntensity * progress;
                shakeOffsetX = (Math.random() * 2 - 1) * currentMag;
                shakeOffsetY = (Math.random() * 2 - 1) * currentMag;
            } else {
                shakeOffsetX = 0;
                shakeOffsetY = 0;
            }
        }

        /**
         * Sets camera position immediately without smoothing (e.g. on respawn or init).
         */
        function snapTo(worldX, worldY) {
            targetX = worldX;
            targetY = worldY;
            x = targetX - viewportW / 2;
            y = targetY - viewportH / 2;
            var maxCamX = Math.max(0, worldW - viewportW);
            var maxCamY = Math.max(0, worldH - viewportH);
            x = Math.max(0, Math.min(maxCamX, x));
            y = Math.max(0, Math.min(maxCamY, y));
            shakeTimer = 0;
            shakeOffsetX = 0;
            shakeOffsetY = 0;
        }

        // --- Coordinate Converters ---

        function worldToScreen(worldX, worldY) {
            return {
                x: worldX - (x + shakeOffsetX),
                y: worldY - (y + shakeOffsetY)
            };
        }

        function screenToWorld(screenX, screenY) {
            return {
                x: screenX + (x + shakeOffsetX),
                y: screenY + (y + shakeOffsetY)
            };
        }

        /**
         * Converts browser client/pointer coordinates on the canvas into logical world coordinates.
         */
        function clientToWorld(clientX, clientY, canvasEl) {
            if (!canvasEl) return { x: 0, y: 0 };
            var rect = canvasEl.getBoundingClientRect();
            var normX = (clientX - rect.left) / (rect.width || 1);
            var normY = (clientY - rect.top) / (rect.height || 1);
            var screenX = normX * viewportW;
            var screenY = normY * viewportH;
            return screenToWorld(screenX, screenY);
        }

        /**
         * Determines if a world bounding circle/box intersects the current visible screen.
         */
        function isVisible(worldX, worldY, radius) {
            var r = radius || 0;
            var minX = x + shakeOffsetX - r;
            var maxX = x + shakeOffsetX + viewportW + r;
            var minY = y + shakeOffsetY - r;
            var maxY = y + shakeOffsetY + viewportH + r;
            return worldX >= minX && worldX <= maxX && worldY >= minY && worldY <= maxY;
        }

        return {
            update: update,
            shake: shake,
            snapTo: snapTo,
            getX: function () { return x + shakeOffsetX; },
            getY: function () { return y + shakeOffsetY; },
            getRawX: function () { return x; },
            getRawY: function () { return y; },
            getViewportWidth: function () { return viewportW; },
            getViewportHeight: function () { return viewportH; },
            getWorldWidth: function () { return worldW; },
            getWorldHeight: function () { return worldH; },
            setWorldDimensions: function (w, h) {
                worldW = Math.max(viewportW, w);
                worldH = Math.max(viewportH, h);
            },
            worldToScreen: worldToScreen,
            screenToWorld: screenToWorld,
            clientToWorld: clientToWorld,
            isVisible: isVisible
        };
    }

    window.CourseForgeCamera = {
        create: createCamera,
        DEFAULT_VIEWPORT_W: DEFAULT_VIEWPORT_W,
        DEFAULT_VIEWPORT_H: DEFAULT_VIEWPORT_H,
        DEFAULT_WORLD_W: DEFAULT_WORLD_W,
        DEFAULT_WORLD_H: DEFAULT_WORLD_H
    };
})(window);
