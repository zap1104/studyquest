/**
 * Course Forge Minigame — Canvas 2D Renderer.
 *
 * Implements:
 * - 640x360 logical viewport with DPR scaling (capped at 2 for performance).
 * - Offscreen cached background (cozy StudyQuest woodland camp, paths, campfire).
 * - Y-sorted back-to-front entity rendering for natural depth.
 * - Procedural characters, trees, slimes, drops, campfire flames, and particle FX.
 */
(function (window) {
    'use strict';

    var LOGICAL_WIDTH = 640;
    var LOGICAL_HEIGHT = 360;
    var WORLD_WIDTH = 1920;
    var WORLD_HEIGHT = 1200;

    function createRenderer(canvas) {
        var ctx = canvas.getContext('2d');
        var dpr = Math.min(window.devicePixelRatio || 1, 2);
        var bgCanvas = null;
        var animTime = 0;

        function resize() {
            var rect = canvas.getBoundingClientRect();
            var width = rect.width || LOGICAL_WIDTH;
            var height = rect.height || LOGICAL_HEIGHT;

            canvas.width = Math.round(width * dpr);
            canvas.height = Math.round(height * dpr);

            // Rebuild background cache if needed
            if (!bgCanvas) {
                bgCanvas = buildBackground();
            }
        }

        function buildBackground() {
            var bg = document.createElement('canvas');
            bg.width = WORLD_WIDTH;
            bg.height = WORLD_HEIGHT;
            var bctx = bg.getContext('2d');

            // 1. Deep forest base fill across world
            bctx.fillStyle = '#0c1712';
            bctx.fillRect(0, 0, WORLD_WIDTH, WORLD_HEIGHT);

            // 2. Varied grass patches and regional clearings
            var patches = [
                // Central Camp Clearing (around 960, 600)
                { x: 960,  y: 600, r: 240, c: '#1c3629' },
                { x: 880,  y: 540, r: 180, c: '#183125' },
                { x: 1040, y: 550, r: 170, c: '#173024' },
                { x: 960,  y: 680, r: 190, c: '#193326' },

                // Western Timber Grove (around 420, 560)
                { x: 420,  y: 560, r: 260, c: '#162b20' },
                { x: 300,  y: 520, r: 200, c: '#13251c' },
                { x: 480,  y: 650, r: 190, c: '#15291f' },

                // Eastern Crystal Grove (around 1480, 560)
                { x: 1480, y: 560, r: 270, c: '#15272a' },
                { x: 1600, y: 520, r: 210, c: '#132427' },
                { x: 1420, y: 660, r: 200, c: '#152a28' },

                // Northern Woodlands (around 960, 260)
                { x: 960,  y: 260, r: 220, c: '#13261d' },
                { x: 820,  y: 280, r: 180, c: '#11221a' },
                { x: 1100, y: 280, r: 180, c: '#11221a' },

                // Southern Meadows (around 960, 950)
                { x: 960,  y: 950, r: 280, c: '#1f3d24' },
                { x: 780,  y: 920, r: 220, c: '#1a351f' },
                { x: 1140, y: 920, r: 220, c: '#1a351f' }
            ];
            patches.forEach(function (p) {
                var grad = bctx.createRadialGradient(p.x, p.y, 20, p.x, p.y, p.r);
                grad.addColorStop(0, p.c);
                grad.addColorStop(1, 'transparent');
                bctx.fillStyle = grad;
                bctx.beginPath();
                bctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
                bctx.fill();
            });

            // 3. Dirt / cobblestone paths connecting regions
            bctx.fillStyle = '#241f1a';
            // Central camp clearing dirt circle
            bctx.beginPath();
            bctx.arc(960, 600, 75, 0, Math.PI * 2);
            bctx.fill();

            // Camp-to-West (Timber Grove) path
            bctx.beginPath();
            bctx.ellipse(690, 580, 220, 36, -0.05, 0, Math.PI * 2);
            bctx.fill();

            // Camp-to-East (Crystal Grove) path
            bctx.beginPath();
            bctx.ellipse(1220, 585, 210, 34, 0.05, 0, Math.PI * 2);
            bctx.fill();

            // Camp-to-North (Northern Woodlands) path
            bctx.beginPath();
            bctx.ellipse(960, 420, 34, 150, 0, 0, Math.PI * 2);
            bctx.fill();

            // Camp-to-South (Southern Meadows) path
            bctx.beginPath();
            bctx.ellipse(960, 770, 32, 120, 0, 0, Math.PI * 2);
            bctx.fill();

            // Cobblestones on paths
            var stones = [
                // Camp center cobblestones
                { x: 920, y: 585, rx: 9, ry: 5 },
                { x: 990, y: 580, rx: 10, ry: 6 },
                { x: 940, y: 635, rx: 8, ry: 5 },
                { x: 980, y: 640, rx: 9, ry: 5 },
                // West path stones
                { x: 800, y: 580, rx: 8, ry: 5 },
                { x: 720, y: 575, rx: 9, ry: 5 },
                { x: 620, y: 585, rx: 8, ry: 4 },
                { x: 520, y: 575, rx: 10, ry: 6 },
                { x: 440, y: 565, rx: 8, ry: 5 },
                // East path stones
                { x: 1100, y: 585, rx: 8, ry: 5 },
                { x: 1190, y: 580, rx: 10, ry: 5 },
                { x: 1290, y: 590, rx: 9, ry: 5 },
                { x: 1390, y: 575, rx: 8, ry: 5 },
                // North path stones
                { x: 960, y: 480, rx: 6, ry: 9 },
                { x: 955, y: 410, rx: 7, ry: 8 },
                { x: 965, y: 340, rx: 6, ry: 9 },
                // South path stones
                { x: 960, y: 720, rx: 6, ry: 9 },
                { x: 965, y: 790, rx: 7, ry: 8 },
                { x: 955, y: 860, rx: 6, ry: 9 }
            ];
            bctx.fillStyle = '#3a342c';
            stones.forEach(function (s) {
                bctx.beginPath();
                bctx.ellipse(s.x, s.y, s.rx, s.ry, 0.2, 0, Math.PI * 2);
                bctx.fill();
            });

            // 3b. Northern Ancient Shrine ruins (at 960, 220)
            bctx.fillStyle = '#1c2421';
            bctx.beginPath();
            bctx.arc(960, 220, 56, 0, Math.PI * 2);
            bctx.fill();
            bctx.strokeStyle = '#334155';
            bctx.lineWidth = 3;
            bctx.beginPath();
            bctx.arc(960, 220, 52, 0, Math.PI * 2);
            bctx.stroke();

            // Mystic concentric ring and runes
            bctx.strokeStyle = 'rgba(129, 140, 248, 0.4)';
            bctx.lineWidth = 1.5;
            bctx.beginPath();
            bctx.arc(960, 220, 32, 0, Math.PI * 2);
            bctx.stroke();

            // Broken ancient stone pillars
            // West pillar
            bctx.fillStyle = '#475569';
            bctx.fillRect(920, 195, 14, 28);
            bctx.fillStyle = '#64748b';
            bctx.fillRect(920, 192, 14, 4);
            // East pillar
            bctx.fillStyle = '#475569';
            bctx.fillRect(986, 195, 14, 28);
            bctx.fillStyle = '#64748b';
            bctx.fillRect(986, 192, 14, 4);
            // North fallen pillar chunk
            bctx.fillStyle = '#334155';
            bctx.fillRect(950, 175, 20, 10);

            // 3c. Eastern Crystal Outcrops (at 1540, 580)
            var crystalOutcrops = [
                { x: 1540, y: 580, r: 8, color: '#06b6d4' },
                { x: 1550, y: 574, r: 12, color: '#38bdf8' },
                { x: 1560, y: 583, r: 9, color: '#67e8f9' },
                { x: 1650, y: 500, r: 10, color: '#38bdf8' },
                { x: 1662, y: 494, r: 7, color: '#06b6d4' }
            ];
            crystalOutcrops.forEach(function (cr) {
                bctx.fillStyle = cr.color;
                bctx.beginPath();
                bctx.moveTo(cr.x, cr.y - cr.r * 1.5);
                bctx.lineTo(cr.x + cr.r * 0.6, cr.y + cr.r * 0.5);
                bctx.lineTo(cr.x - cr.r * 0.6, cr.y + cr.r * 0.5);
                bctx.closePath();
                bctx.fill();
            });

            // 3d. Southern Meadow Wildflowers
            var flowers = [
                { x: 740, y: 910, c: '#fbcfe8' },
                { x: 820, y: 940, c: '#fde047' },
                { x: 910, y: 980, c: '#93c5fd' },
                { x: 990, y: 950, c: '#fde047' },
                { x: 1080, y: 990, c: '#fbcfe8' },
                { x: 1160, y: 930, c: '#93c5fd' },
                { x: 860, y: 1020, c: '#fde047' },
                { x: 1040, y: 1030, c: '#fbcfe8' }
            ];
            flowers.forEach(function (fl) {
                bctx.fillStyle = fl.c;
                bctx.beginPath();
                bctx.arc(fl.x, fl.y, 2.5, 0, Math.PI * 2);
                bctx.fill();
            });

            // 4. Border perimeter: dense ancient pines framing the entire world
            bctx.fillStyle = '#08120d';
            // Top border treeline
            for (var bx = 15; bx < WORLD_WIDTH; bx += 40) {
                drawTreeSilhouette(bctx, bx, 25, 30);
            }
            // Bottom border treeline
            for (var bbx = 15; bbx < WORLD_WIDTH; bbx += 42) {
                drawTreeSilhouette(bctx, bbx, WORLD_HEIGHT - 20, 32);
            }
            // Left border treeline
            for (var ly = 30; ly < WORLD_HEIGHT; ly += 38) {
                drawTreeSilhouette(bctx, 20, ly, 28);
            }
            // Right border treeline
            for (var ry = 30; ry < WORLD_HEIGHT; ry += 38) {
                drawTreeSilhouette(bctx, WORLD_WIDTH - 20, ry, 28);
            }

            // 5. Camp log benches around campfire
            // Top bench
            bctx.fillStyle = '#3d2415';
            bctx.beginPath();
            bctx.roundRect(930, 558, 60, 10, 4);
            bctx.fill();
            bctx.fillStyle = '#2b170c';
            bctx.fillRect(936, 568, 8, 4);
            bctx.fillRect(976, 568, 8, 4);

            // Bottom bench
            bctx.fillStyle = '#3d2415';
            bctx.beginPath();
            bctx.roundRect(930, 636, 60, 10, 4);
            bctx.fill();
            bctx.fillStyle = '#2b170c';
            bctx.fillRect(936, 646, 8, 4);
            bctx.fillRect(976, 646, 8, 4);

            return bg;
        }

        function drawTreeSilhouette(ctx, x, y, r) {
            ctx.beginPath();
            ctx.arc(x, y, r, 0, Math.PI * 2);
            ctx.fill();
        }

        function drawShadow(c, x, y, rx, ry) {
            c.save();
            c.fillStyle = 'rgba(0, 0, 0, 0.35)';
            c.beginPath();
            c.ellipse(x, y, rx, ry, 0, 0, Math.PI * 2);
            c.fill();
            c.restore();
        }

        function drawCampfire(c, x, y, t) {
            drawShadow(c, x, y + 2, 26, 12);

            // Warm campfire ground glow
            var glow = c.createRadialGradient(x, y, 6, x, y, 55);
            glow.addColorStop(0, 'rgba(251, 146, 60, 0.28)');
            glow.addColorStop(0.6, 'rgba(234, 88, 12, 0.12)');
            glow.addColorStop(1, 'rgba(0, 0, 0, 0)');
            c.fillStyle = glow;
            c.beginPath();
            c.arc(x, y, 55, 0, Math.PI * 2);
            c.fill();

            // Campfire stone ring
            var stoneAngles = [0, 0.8, 1.6, 2.3, 3.1, 3.9, 4.7, 5.5];
            c.fillStyle = '#47433c';
            stoneAngles.forEach(function (ang) {
                var sx = x + Math.cos(ang) * 19;
                var sy = y + Math.sin(ang) * 9;
                c.beginPath();
                c.ellipse(sx, sy, 5, 3.5, 0, 0, Math.PI * 2);
                c.fill();
            });

            // Charred firewood logs
            c.fillStyle = '#24140b';
            c.save();
            c.translate(x, y);
            c.rotate(-0.4);
            c.fillRect(-12, -2, 24, 4);
            c.rotate(0.8);
            c.fillRect(-12, -2, 24, 4);
            c.restore();

            // Animated layered flame
            var flameFlicker = Math.sin(t * 12) * 2;
            var flameHeight = 16 + Math.cos(t * 16) * 3;

            // Outer red flame
            c.fillStyle = '#ef4444';
            c.beginPath();
            c.moveTo(x - 9, y);
            c.quadraticCurveTo(x - 6, y - flameHeight * 0.7, x + flameFlicker, y - flameHeight);
            c.quadraticCurveTo(x + 6, y - flameHeight * 0.7, x + 9, y);
            c.closePath();
            c.fill();

            // Mid orange flame
            c.fillStyle = '#f97316';
            c.beginPath();
            c.moveTo(x - 6, y);
            c.quadraticCurveTo(x - 4, y - flameHeight * 0.6, x - flameFlicker * 0.5, y - flameHeight * 0.85);
            c.quadraticCurveTo(x + 4, y - flameHeight * 0.6, x + 6, y);
            c.closePath();
            c.fill();

            // Inner yellow core
            c.fillStyle = '#fef08a';
            c.beginPath();
            c.moveTo(x - 3, y);
            c.quadraticCurveTo(x - 2, y - flameHeight * 0.4, x, y - flameHeight * 0.65);
            c.quadraticCurveTo(x + 2, y - flameHeight * 0.4, x + 3, y);
            c.closePath();
            c.fill();
        }

        function drawTent(c, x, y) {
            drawShadow(c, x, y + 10, 36, 14);

            // Canvas tent body (cozy scholar expedition tent)
            c.fillStyle = '#1e3a5f';
            c.beginPath();
            c.moveTo(x, y - 28);
            c.lineTo(x + 32, y + 10);
            c.lineTo(x - 32, y + 10);
            c.closePath();
            c.fill();

            // Tent entrance flap shadow
            c.fillStyle = '#0f1d30';
            c.beginPath();
            c.moveTo(x, y - 28);
            c.lineTo(x + 6, y + 10);
            c.lineTo(x - 14, y + 10);
            c.closePath();
            c.fill();

            // Wooden ridgepole & pegs
            c.strokeStyle = '#d97706';
            c.lineWidth = 2.5;
            c.beginPath();
            c.moveTo(x, y - 31);
            c.lineTo(x, y + 10);
            c.stroke();

            // Hanging lantern
            c.fillStyle = '#fef08a';
            c.beginPath();
            c.arc(x + 14, y - 6, 3, 0, Math.PI * 2);
            c.fill();
        }

        function drawAnvil(c, x, y) {
            drawShadow(c, x, y + 6, 20, 8);

            // Wooden stump base
            c.fillStyle = '#452b1b';
            c.beginPath();
            c.ellipse(x, y + 4, 14, 7, 0, 0, Math.PI * 2);
            c.fill();
            c.fillRect(x - 14, y - 4, 28, 8);
            c.fillStyle = '#5c3924';
            c.beginPath();
            c.ellipse(x, y - 4, 14, 6, 0, 0, Math.PI * 2);
            c.fill();

            // Iron anvil
            c.fillStyle = '#475569';
            c.fillRect(x - 10, y - 11, 20, 7);
            c.fillStyle = '#64748b';
            c.fillRect(x - 14, y - 13, 28, 4);
            // Horn
            c.beginPath();
            c.moveTo(x - 14, y - 13);
            c.lineTo(x - 19, y - 11);
            c.lineTo(x - 14, y - 9);
            c.closePath();
            c.fill();
        }

        function drawTree(c, tree) {
            drawShadow(c, tree.x, tree.y + 4, 18, 8);

            if (tree.isFelled) {
                // Wooden stump
                c.fillStyle = '#452b1b';
                c.fillRect(tree.x - 10, tree.y - 10, 20, 14);
                // Cut top with growth rings
                c.fillStyle = '#b45309';
                c.beginPath();
                c.ellipse(tree.x, tree.y - 10, 10, 5, 0, 0, Math.PI * 2);
                c.fill();
                c.strokeStyle = '#78350f';
                c.lineWidth = 1;
                c.beginPath();
                c.ellipse(tree.x, tree.y - 10, 5, 2.5, 0, 0, Math.PI * 2);
                c.stroke();

                // Tiny green respawn sprout
                var sproutProg = 1 - (tree.respawnTimer / tree.respawnDuration);
                if (sproutProg > 0.4) {
                    c.fillStyle = '#22c55e';
                    c.beginPath();
                    c.arc(tree.x, tree.y - 14, 2.5, 0, Math.PI * 2);
                    c.fill();
                }
                return;
            }

            var shakeX = 0;
            if (tree.shakeTimer > 0) {
                shakeX = Math.sin(tree.shakeTimer * 38) * 3;
            }

            var tx = tree.x + shakeX;
            var ty = tree.y;

            // Trunk
            c.fillStyle = '#3d2415';
            c.fillRect(tx - 6, ty - 22, 12, 24);

            // Foliage
            if (tree.variant === 'pine' || tree.variant === 'crystal_pine') {
                var isCrys = tree.variant === 'crystal_pine';
                // Tier 3 (bottom)
                c.fillStyle = isCrys ? '#0e3a47' : '#143822';
                c.beginPath();
                c.moveTo(tx, ty - 56);
                c.lineTo(tx + 26, ty - 20);
                c.lineTo(tx - 26, ty - 20);
                c.closePath();
                c.fill();
                // Tier 2 (middle)
                c.fillStyle = isCrys ? '#155e75' : '#1e5232';
                c.beginPath();
                c.moveTo(tx, ty - 70);
                c.lineTo(tx + 21, ty - 38);
                c.lineTo(tx - 21, ty - 38);
                c.closePath();
                c.fill();
                // Tier 1 (top)
                c.fillStyle = isCrys ? '#0891b2' : '#286e43';
                c.beginPath();
                c.moveTo(tx, ty - 84);
                c.lineTo(tx + 15, ty - 54);
                c.lineTo(tx - 15, ty - 54);
                c.closePath();
                c.fill();

                if (isCrys) {
                    // Sparkling crystal tip
                    c.fillStyle = '#67e8f9';
                    c.beginPath();
                    c.arc(tx, ty - 86, 3.5, 0, Math.PI * 2);
                    c.fill();
                }
            } else if (tree.variant === 'ancient_oak') {
                // Grand Ancient Oak Crown
                c.fillStyle = '#1c1917';
                c.beginPath();
                c.arc(tx, ty - 46, 34, 0, Math.PI * 2);
                c.fill();
                c.fillStyle = '#451a03';
                c.beginPath();
                c.arc(tx - 10, ty - 50, 24, 0, Math.PI * 2);
                c.arc(tx + 10, ty - 48, 22, 0, Math.PI * 2);
                c.fill();
                c.fillStyle = '#b45309';
                c.beginPath();
                c.arc(tx - 4, ty - 56, 18, 0, Math.PI * 2);
                c.fill();
                c.fillStyle = '#d97706';
                c.beginPath();
                c.arc(tx + 4, ty - 58, 12, 0, Math.PI * 2);
                c.fill();
            } else {
                // Lush rounded Oak Crown
                c.fillStyle = '#143822';
                c.beginPath();
                c.arc(tx, ty - 42, 28, 0, Math.PI * 2);
                c.fill();
                // Highlight layers
                c.fillStyle = '#1e5232';
                c.beginPath();
                c.arc(tx - 8, ty - 46, 20, 0, Math.PI * 2);
                c.arc(tx + 8, ty - 44, 18, 0, Math.PI * 2);
                c.fill();
                c.fillStyle = '#2d7a4b';
                c.beginPath();
                c.arc(tx - 4, ty - 52, 16, 0, Math.PI * 2);
                c.fill();
            }

            // Health Bar if damaged
            if (tree.hp < tree.maxHp) {
                var barW = 28;
                var barH = 4;
                var bx = tx - barW / 2;
                var by = ty - (tree.variant === 'pine' || tree.variant === 'crystal_pine' ? 92 : 78);
                c.fillStyle = 'rgba(0,0,0,0.6)';
                c.fillRect(bx - 1, by - 1, barW + 2, barH + 2);
                c.fillStyle = '#22c55e';
                c.fillRect(bx, by, barW * (tree.hp / tree.maxHp), barH);
            }
        }

        function drawEnemy(c, enemy, t) {
            if (enemy.isDefeated) return;

            var hopScale = Math.sin(enemy.hopTime) * 0.12;
            var ey = enemy.y;
            drawShadow(c, enemy.x, ey + 3, 14 * (1 - hopScale), 7 * (1 - hopScale));

            c.save();
            c.translate(enemy.x, ey - 6);
            c.scale(1 + hopScale, 1 - hopScale);

            // Hit flash
            var isFlashing = enemy.flashTimer > 0;

            // Slime body color based on variant
            var bodyColor = '#10b981';
            var hornColor = '#34d399';
            if (enemy.variant === 'crystal') {
                bodyColor = '#06b6d4';
                hornColor = '#67e8f9';
            } else if (enemy.variant === 'ancient') {
                bodyColor = '#8b5cf6';
                hornColor = '#c084fc';
            }

            // Slime body
            c.fillStyle = isFlashing ? '#ffffff' : bodyColor;
            c.beginPath();
            c.ellipse(0, 0, 13, 10, 0, 0, Math.PI * 2);
            c.fill();

            // Head antenna / crystal sprout
            c.fillStyle = isFlashing ? '#ffffff' : hornColor;
            c.beginPath();
            c.arc(0, -9, 3.5, 0, Math.PI * 2);
            c.fill();

            // Eyes
            if (!isFlashing) {
                c.fillStyle = '#064e3b';
                c.beginPath();
                c.arc(-4, -1, 2, 0, Math.PI * 2);
                c.arc(4, -1, 2, 0, Math.PI * 2);
                c.fill();
                // Shine dots
                c.fillStyle = '#ffffff';
                c.beginPath();
                c.arc(-4.5, -2, 0.8, 0, Math.PI * 2);
                c.arc(3.5, -2, 0.8, 0, Math.PI * 2);
                c.fill();
            }
            c.restore();

            // Health bar if damaged
            if (enemy.hp < enemy.maxHp) {
                var barW = 22;
                var barH = 3;
                var bx = enemy.x - barW / 2;
                var by = enemy.y - 22;
                c.fillStyle = 'rgba(0,0,0,0.6)';
                c.fillRect(bx - 1, by - 1, barW + 2, barH + 2);
                c.fillStyle = '#ec4899';
                c.fillRect(bx, by, barW * (enemy.hp / enemy.maxHp), barH);
            }
        }

        function drawPlayer(c, player, t) {
            var px = player.x;
            var py = player.y;

            drawShadow(c, px, py + 2, 13, 6);

            var bob = player.isMoving ? Math.abs(Math.sin(player.walkTime * 2.2)) * 3 : 0;

            c.save();
            c.translate(px, py - 12 - bob);

            if (player.facing === 'left') {
                c.scale(-1, 1);
            }

            // Scholar Robe Body
            c.fillStyle = '#1d4ed8'; // Royal blue
            c.beginPath();
            c.moveTo(0, -14);
            c.lineTo(9, 8);
            c.lineTo(-9, 8);
            c.closePath();
            c.fill();

            // Golden trim
            c.strokeStyle = '#fbbf24';
            c.lineWidth = 1.5;
            c.beginPath();
            c.moveTo(0, -12);
            c.lineTo(0, 8);
            c.stroke();

            // Head & Face
            c.fillStyle = '#fed7aa'; // Skin tone
            c.beginPath();
            c.arc(0, -18, 6.5, 0, Math.PI * 2);
            c.fill();

            // Eyes
            c.fillStyle = '#1e293b';
            c.fillRect(2, -19, 1.8, 2.2);

            // Scholar Hat / Beret
            c.fillStyle = '#1e40af';
            c.beginPath();
            c.ellipse(0, -23, 9, 3.5, -0.15, 0, Math.PI * 2);
            c.fill();
            // Golden tassel
            c.fillStyle = '#fbbf24';
            c.beginPath();
            c.arc(-6, -21, 2, 0, Math.PI * 2);
            c.fill();

            // Tool / Swing Arc
            if (player.swingTimer > 0) {
                var swingProg = 1 - (player.swingTimer / player.swingDuration);
                var swingAngle = -1.2 + swingProg * 2.4;

                c.save();
                c.translate(6, -8);
                c.rotate(swingAngle);

                // Tool handle
                c.fillStyle = '#78350f';
                c.fillRect(-2, -18, 4, 18);

                // Axe / Dagger head
                if (player.swingType === 'chop') {
                    c.fillStyle = '#94a3b8';
                    c.beginPath();
                    c.moveTo(2, -18);
                    c.lineTo(12, -22);
                    c.lineTo(10, -10);
                    c.closePath();
                    c.fill();
                } else {
                    c.fillStyle = '#c084fc';
                    c.fillRect(-3, -24, 6, 8);
                }
                c.restore();

                // Swoosh slash arc
                c.strokeStyle = 'rgba(254, 240, 138, ' + (1 - swingProg) * 0.8 + ')';
                c.lineWidth = 3;
                c.beginPath();
                c.arc(8, -8, 22, -1.2, -1.2 + swingProg * 2.2);
                c.stroke();
            } else {
                // Idle tool on belt
                c.fillStyle = '#78350f';
                c.fillRect(-7, -4, 3, 10);
                c.fillStyle = '#94a3b8';
                c.fillRect(-9, 4, 6, 3);
            }

            c.restore();
        }

        function drawDrop(c, drop, t) {
            var groundY = drop.groundY || drop.y;
            var shadowScale = Math.max(0.4, 1 - (drop.z / 60));
            drawShadow(c, drop.x, groundY + 1, 8 * shadowScale, 4 * shadowScale);

            var dy = drop.y - drop.z - Math.sin(t * 5 + drop.x) * 2;

            if (drop.type === 'wood') {
                // Log bundle
                c.fillStyle = '#b45309';
                c.save();
                c.translate(drop.x, dy);
                c.rotate(0.2);
                c.beginPath();
                c.roundRect(-7, -3, 14, 6, 2);
                c.fill();
                // End grain
                c.fillStyle = '#d97706';
                c.beginPath();
                c.ellipse(6, 0, 2, 3, 0, 0, Math.PI * 2);
                c.fill();
                c.restore();
            } else if (drop.type === 'crystal') {
                // Sparkling study crystal
                c.save();
                c.translate(drop.x, dy);

                // Halo glow
                var cg = c.createRadialGradient(0, 0, 1, 0, 0, 10);
                cg.addColorStop(0, 'rgba(56, 189, 248, 0.6)');
                cg.addColorStop(1, 'rgba(56, 189, 248, 0)');
                c.fillStyle = cg;
                c.beginPath();
                c.arc(0, 0, 10, 0, Math.PI * 2);
                c.fill();

                // Gem diamond
                c.fillStyle = '#38bdf8';
                c.beginPath();
                c.moveTo(0, -6);
                c.lineTo(5, 0);
                c.lineTo(0, 6);
                c.lineTo(-5, 0);
                c.closePath();
                c.fill();

                // Highlight
                c.fillStyle = '#bae6fd';
                c.beginPath();
                c.moveTo(0, -6);
                c.lineTo(2, 0);
                c.lineTo(0, 3);
                c.lineTo(-2, 0);
                c.closePath();
                c.fill();

                c.restore();
            }
        }

        function drawParticle(c, p) {
            var alpha = Math.max(0, p.life / p.maxLife);
            c.save();
            c.globalAlpha = alpha;

            if (p.type === 'text') {
                c.font = 'bold 12px "Segoe UI", Roboto, sans-serif';
                c.textAlign = 'center';
                c.fillStyle = '#0f172a';
                c.fillText(p.text, p.x + 1, p.y + 1);
                c.fillStyle = p.color || '#fbbf24';
                c.fillText(p.text, p.x, p.y);
            } else if (p.type === 'chip') {
                c.fillStyle = p.color || '#92400e';
                c.fillRect(p.x - p.size / 2, p.y - p.size / 2, p.size, p.size);
            } else if (p.type === 'sparkle') {
                c.fillStyle = p.color || '#38bdf8';
                c.beginPath();
                c.arc(p.x, p.y, p.size, 0, Math.PI * 2);
                c.fill();
            }
            c.restore();
        }

        function drawHud(c, stateData) {
            var wood = stateData.wood || 0;
            var crystals = stateData.crystals || 0;

            // Wood Pill Top-Left
            c.save();
            c.fillStyle = 'rgba(15, 23, 42, 0.85)';
            c.strokeStyle = 'rgba(255, 255, 255, 0.12)';
            c.lineWidth = 1;

            // Wood pill
            c.beginPath();
            c.roundRect(14, 12, 85, 28, 14);
            c.fill();
            c.stroke();
            c.font = 'bold 13px "Segoe UI", Roboto, sans-serif';
            c.fillStyle = '#f59e0b';
            c.textAlign = 'left';
            c.fillText('🪵 ' + wood, 24, 30);

            // Crystal pill
            c.beginPath();
            c.roundRect(106, 12, 85, 28, 14);
            c.fill();
            c.stroke();
            c.fillStyle = '#38bdf8';
            c.fillText('💎 ' + crystals, 116, 30);

            // Camp Level pill
            var campLvl = stateData.campLevel || 1;
            c.beginPath();
            c.roundRect(198, 12, 94, 28, 14);
            c.fill();
            c.stroke();
            c.fillStyle = '#c084fc';
            c.fillText('⛺ Lv.' + campLvl, 208, 30);

            c.restore();
        }

        function render(dt, entityManager, state, camera, plotsManager) {
            animTime += dt;

            // Scale to logical viewport
            var currentW = canvas.width;
            var currentH = canvas.height;
            var scaleX = currentW / LOGICAL_WIDTH;
            var scaleY = currentH / LOGICAL_HEIGHT;

            ctx.save();
            ctx.scale(scaleX, scaleY);

            // ==========================================
            // 1. World Pass: Camera Translated
            // ==========================================
            ctx.save();
            var camX = camera ? camera.getX() : 0;
            var camY = camera ? camera.getY() : 0;
            ctx.translate(-camX, -camY);

            // 1a. Draw pre-rendered background
            if (bgCanvas) {
                ctx.drawImage(bgCanvas, 0, 0);
            } else {
                ctx.fillStyle = '#0c1712';
                ctx.fillRect(0, 0, WORLD_WIDTH, WORLD_HEIGHT);
            }

            // 1b. Y-sorted Entity drawing
            var renderQueue = [];

            // Add Construction Plots (or legacy fallback scenery)
            if (plotsManager) {
                var plots = plotsManager.getPlots();
                for (var pi = 0; pi < plots.length; pi++) {
                    (function (plot) {
                        if (!camera || camera.isVisible(plot.x, plot.y, 75)) {
                            renderQueue.push({
                                y: plot.y,
                                draw: function (c) { plotsManager.drawPlot(c, plot, animTime); }
                            });
                        }
                    })(plots[pi]);
                }
            } else {
                // Fallback for standalone scenes
                renderQueue.push({ y: 535, draw: function (c) { drawTent(c, 865, 535); } });
                renderQueue.push({ y: 565, draw: function (c) { drawAnvil(c, 1045, 565); } });
                renderQueue.push({ y: 600, draw: function (c) { drawCampfire(c, 960, 600, animTime); } });
            }

            // Add Trees (culled if off-screen)
            var trees = entityManager.getTrees();
            for (var t = 0; t < trees.length; t++) {
                (function (tree) {
                    if (!camera || camera.isVisible(tree.x, tree.y, 60)) {
                        renderQueue.push({
                            y: tree.y,
                            draw: function (c) { drawTree(c, tree); }
                        });
                    }
                })(trees[t]);
            }

            // Add Enemies (culled if off-screen)
            var enemies = entityManager.getEnemies();
            for (var e = 0; e < enemies.length; e++) {
                (function (enemy) {
                    if (!camera || camera.isVisible(enemy.x, enemy.y, 40)) {
                        renderQueue.push({
                            y: enemy.y,
                            draw: function (c) { drawEnemy(c, enemy, animTime); }
                        });
                    }
                })(enemies[e]);
            }

            // Add Drops
            var drops = entityManager.getDrops();
            for (var d = 0; d < drops.length; d++) {
                (function (drop) {
                    if (!camera || camera.isVisible(drop.x, drop.y, 30)) {
                        renderQueue.push({
                            y: drop.groundY || drop.y,
                            draw: function (c) { drawDrop(c, drop, animTime); }
                        });
                    }
                })(drops[d]);
            }

            // Add Player
            var player = entityManager.getPlayer();
            renderQueue.push({
                y: player.y,
                draw: function (c) { drawPlayer(c, player, animTime); }
            });

            // Sort ascending by Y for proper perspective overlap
            renderQueue.sort(function (a, b) {
                return a.y - b.y;
            });

            // Draw all sorted objects
            for (var i = 0; i < renderQueue.length; i++) {
                renderQueue[i].draw(ctx);
            }

            // 1c. Draw particles in world space
            var particles = entityManager.getParticles();
            for (var p = 0; p < particles.length; p++) {
                drawParticle(ctx, particles[p]);
            }

            // 1d. Draw in-world contextual prompt badge above active plot
            if (plotsManager) {
                plotsManager.drawPrompt(ctx, camera);
            }

            ctx.restore(); // End World Pass

            // ==========================================
            // 2. Screen Pass: In-canvas Viewport HUD
            // ==========================================
            drawHud(ctx, state.getData());

            ctx.restore(); // End Logical Viewport
        }

        // Initialize size
        resize();
        window.addEventListener('resize', resize);

        function destroy() {
            window.removeEventListener('resize', resize);
            bgCanvas = null;
        }

        return {
            render: render,
            resize: resize,
            destroy: destroy
        };
    }

    window.CourseForgeRenderer = {
        create: createRenderer,
        LOGICAL_WIDTH: LOGICAL_WIDTH,
        LOGICAL_HEIGHT: LOGICAL_HEIGHT
    };
})(window);
