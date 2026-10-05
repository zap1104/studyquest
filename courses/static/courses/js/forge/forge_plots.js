/**
 * Course Forge Minigame — In-World Construction Plots & Progression Engine.
 *
 * Implements:
 * - 4 interactive physical construction plots anchored in the world:
 *   1. Camp Hearth (Center: x: 960, y: 600)
 *   2. Woodcutter's Sawmill (West: x: 850, y: 535)
 *   3. Scribe's Study Archive (East: x: 1070, y: 535)
 *   4. Warrior's Forge (South: x: 960, y: 695)
 * - 4 distinct visual tiers per plot (Unbuilt Foundation Stake, Lv 1, Lv 2, Lv 3).
 * - Player proximity detection & contextual in-world floating interaction prompts.
 * - Interactive building construction with resource deduction, procedural audio,
 *   shake feedback, floating text, and dust/sparkle particle effects.
 */
(function (window) {
    'use strict';

    var INTERACT_RADIUS = 58;

    function dist(x1, y1, x2, y2) {
        var dx = x2 - x1;
        var dy = y2 - y1;
        return Math.hypot(dx, dy);
    }

    function drawShadow(c, x, y, rx, ry) {
        c.fillStyle = 'rgba(0, 0, 0, 0.32)';
        c.beginPath();
        c.ellipse(x, y, rx, ry, 0, 0, Math.PI * 2);
        c.fill();
    }

    function createPlotManager(state, soundSystem, entityManager, camera) {
        var activePromptPlot = null;
        var animTimer = 0;

        function getPlots() {
            return state.getAllPlots();
        }

        function getPlot(id) {
            return state.getPlotInfo(id);
        }

        function getNearestPlot(px, py) {
            var plots = getPlots();
            var nearest = null;
            var minDist = Infinity;

            for (var i = 0; i < plots.length; i++) {
                var p = plots[i];
                var d = dist(px, py, p.x, p.y);
                if (d < minDist) {
                    minDist = d;
                    nearest = p;
                }
            }

            return {
                plot: nearest,
                distance: minDist,
                inRange: minDist <= INTERACT_RADIUS
            };
        }

        function update(dt, px, py) {
            animTimer += dt;
            var nearestInfo = getNearestPlot(px, py);
            if (nearestInfo.inRange) {
                activePromptPlot = nearestInfo.plot;
            } else {
                activePromptPlot = null;
            }
        }

        function interact(plotId) {
            var targetId = plotId || (activePromptPlot ? activePromptPlot.id : null);
            if (!targetId) return false;

            var plot = getPlot(targetId);
            if (!plot) return false;

            if (plot.isMax) {
                if (entityManager) {
                    entityManager.spawnFloatingText('Max Level Reached!', plot.x, plot.y - 30, '#94a3b8');
                }
                return false;
            }

            if (!plot.canAfford) {
                if (entityManager && plot.cost) {
                    var needMsg = 'Need ' + plot.cost.wood + ' 🪵';
                    if (plot.cost.crystals > 0) needMsg += ', ' + plot.cost.crystals + ' 💎';
                    entityManager.spawnFloatingText(needMsg, plot.x, plot.y - 35, '#ef4444');
                }
                if (soundSystem) soundSystem.playStrike();
                return false;
            }

            var success = state.buyPlotUpgrade(targetId);
            if (success) {
                var updated = getPlot(targetId);
                if (soundSystem) soundSystem.playUpgrade();
                if (camera) camera.shake(2.8, 0.2);

                if (entityManager) {
                    var actionName = updated.currentLevel === 1 ? 'Built ' : 'Upgraded ';
                    entityManager.spawnFloatingText(actionName + updated.name + ' (Lv ' + updated.currentLevel + ')!', plot.x, plot.y - 40, '#38bdf8');
                    entityManager.spawnSparkles(plot.x, plot.y - 10, 16, '#f59e0b');
                    entityManager.spawnSparkles(plot.x, plot.y - 20, 12, '#38bdf8');
                }
                return true;
            }

            return false;
        }

        // =========================================================================
        // RENDERING: 4 PLOTS × 4 TIERS
        // =========================================================================

        function drawFoundationStake(c, x, y, icon, label) {
            drawShadow(c, x, y + 6, 26, 12);

            // Dotted boundary line
            c.save();
            c.strokeStyle = 'rgba(255, 255, 255, 0.35)';
            c.lineWidth = 1.5;
            c.setLineDash([4, 4]);
            c.beginPath();
            c.arc(x, y, 26, 0, Math.PI * 2);
            c.stroke();
            c.restore();

            // 4 Corner boundary pegs
            var pegs = [
                { x: x - 20, y: y - 14 },
                { x: x + 20, y: y - 14 },
                { x: x - 20, y: y + 14 },
                { x: x + 20, y: y + 14 }
            ];
            c.fillStyle = '#92400e';
            pegs.forEach(function (peg) {
                c.fillRect(peg.x - 2, peg.y - 8, 4, 8);
                c.fillStyle = '#b45309';
                c.fillRect(peg.x - 3, peg.y - 10, 6, 3);
            });

            // Signpost
            c.fillStyle = '#451a03';
            c.fillRect(x - 2, y - 18, 4, 18); // post
            // Signboard
            c.fillStyle = '#78350f';
            c.fillRect(x - 14, y - 28, 28, 14);
            c.strokeStyle = '#92400e';
            c.lineWidth = 1;
            c.strokeRect(x - 14, y - 28, 28, 14);

            // Icon / Symbol on sign
            c.font = '10px sans-serif';
            c.textAlign = 'center';
            c.textBaseline = 'middle';
            c.fillStyle = '#fef08a';
            c.fillText(icon || '🔨', x, y - 21);
        }

        // --- 1. HEARTH RENDERING ---
        function drawHearth(c, plot, t) {
            var x = plot.x;
            var y = plot.y;
            var lvl = plot.currentLevel;

            if (lvl === 1) {
                // Tier 1: Pioneer Campfire
                drawShadow(c, x, y + 8, 28, 12);

                // Cobblestone circle
                c.fillStyle = '#475569';
                for (var i = 0; i < 8; i++) {
                    var a = (i / 8) * Math.PI * 2;
                    c.beginPath();
                    c.ellipse(x + Math.cos(a) * 16, y + Math.sin(a) * 9, 5, 3.5, a, 0, Math.PI * 2);
                    c.fill();
                }

                // Ash bed
                c.fillStyle = '#1e293b';
                c.beginPath();
                c.ellipse(x, y, 12, 6, 0, 0, Math.PI * 2);
                c.fill();

                // Crossed firewood logs
                c.save();
                c.translate(x, y);
                c.fillStyle = '#3e2717';
                c.rotate(-0.4);
                c.fillRect(-12, -2, 24, 4);
                c.rotate(0.8);
                c.fillRect(-12, -2, 24, 4);
                c.restore();

                // Flame
                drawFlames(c, x, y, 16, t);
            } else if (lvl === 2) {
                // Tier 2: Cooking Hearth with Tripod & Kettle
                drawShadow(c, x, y + 10, 34, 14);

                // Double stone circle
                c.fillStyle = '#334155';
                for (var j = 0; j < 10; j++) {
                    var ang = (j / 10) * Math.PI * 2;
                    c.beginPath();
                    c.ellipse(x + Math.cos(ang) * 19, y + Math.sin(ang) * 10, 6, 4, ang, 0, Math.PI * 2);
                    c.fill();
                }

                // Ash bed
                c.fillStyle = '#0f172a';
                c.beginPath();
                c.ellipse(x, y, 14, 7, 0, 0, Math.PI * 2);
                c.fill();

                // Sitting log beside hearth
                c.fillStyle = '#451a03';
                c.fillRect(x - 30, y - 4, 10, 16);
                c.fillStyle = '#78350f';
                c.beginPath();
                c.ellipse(x - 25, y - 4, 5, 2.5, 0, 0, Math.PI * 2);
                c.fill();

                // Iron Tripod
                c.strokeStyle = '#1e293b';
                c.lineWidth = 2;
                c.beginPath();
                c.moveTo(x - 12, y + 6);
                c.lineTo(x, y - 26);
                c.lineTo(x + 12, y + 6);
                c.stroke();

                // Kettle hanging
                c.strokeStyle = '#475569';
                c.lineWidth = 1.5;
                c.beginPath();
                c.moveTo(x, y - 26);
                c.lineTo(x, y - 14);
                c.stroke();

                c.fillStyle = '#0f172a';
                c.beginPath();
                c.arc(x, y - 10, 5, 0, Math.PI * 2);
                c.fill();

                // Steam wisps
                var steamOffset = Math.sin(t * 5) * 2;
                c.fillStyle = 'rgba(255, 255, 255, 0.4)';
                c.beginPath();
                c.arc(x + steamOffset, y - 18, 1.8, 0, Math.PI * 2);
                c.arc(x - steamOffset, y - 23, 2.2, 0, Math.PI * 2);
                c.fill();

                // Warm fire
                drawFlames(c, x, y, 19, t);
            } else if (lvl >= 3) {
                // Tier 3: Grand Citadel Bonfire & Carved Lanterns
                drawShadow(c, x, y + 12, 42, 16);

                // Raised stone masonry base
                c.fillStyle = '#1e293b';
                c.beginPath();
                c.ellipse(x, y + 2, 26, 13, 0, 0, Math.PI * 2);
                c.fill();
                c.fillStyle = '#334155';
                c.beginPath();
                c.ellipse(x, y - 2, 24, 11, 0, 0, Math.PI * 2);
                c.fill();

                // Twin carved wooden lantern posts
                [-28, 28].forEach(function (offX) {
                    c.fillStyle = '#451a03';
                    c.fillRect(x + offX - 2, y - 26, 4, 28);
                    // Lantern
                    c.fillStyle = '#f59e0b';
                    c.beginPath();
                    c.arc(x + offX, y - 26, 4, 0, Math.PI * 2);
                    c.fill();
                    c.fillStyle = '#fef08a';
                    c.beginPath();
                    c.arc(x + offX, y - 26, 2, 0, Math.PI * 2);
                    c.fill();
                });

                // Roaring fire
                drawFlames(c, x, y - 2, 26, t);

                // Golden ember particles floating upward
                c.fillStyle = '#fbbf24';
                for (var ep = 0; ep < 4; ep++) {
                    var epx = x + Math.sin(t * 3 + ep * 1.5) * 12;
                    var epy = (y - 16) - ((t * 22 + ep * 9) % 24);
                    c.beginPath();
                    c.arc(epx, epy, 1.2, 0, Math.PI * 2);
                    c.fill();
                }
            }
        }

        function drawFlames(c, x, y, baseHeight, t) {
            var flameFlicker = Math.sin(t * 12) * 2;
            var flameHeight = baseHeight + Math.cos(t * 16) * 3;

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

        // --- 2. SAWMILL RENDERING ---
        function drawSawmill(c, plot, t) {
            var x = plot.x;
            var y = plot.y;
            var lvl = plot.currentLevel;

            if (lvl === 0) {
                drawFoundationStake(c, x, y, '🪓', 'Sawmill');
                return;
            }

            drawShadow(c, x, y + 8, 32, 14);

            if (lvl === 1) {
                // Tier 1: Timber Lean-To Shelter
                // Rear posts
                c.fillStyle = '#451a03';
                c.fillRect(x - 16, y - 22, 3, 22);
                c.fillRect(x + 14, y - 22, 3, 22);

                // Slanted timber roof
                c.fillStyle = '#78350f';
                c.beginPath();
                c.moveTo(x - 20, y - 24);
                c.lineTo(x + 18, y - 24);
                c.lineTo(x + 16, y - 10);
                c.lineTo(x - 22, y - 10);
                c.closePath();
                c.fill();
                c.strokeStyle = '#92400e';
                c.lineWidth = 1.5;
                c.stroke();

                // Chopping stump with stuck hatchet
                c.fillStyle = '#5c3924';
                c.fillRect(x - 4, y - 4, 12, 10);
                c.fillStyle = '#b45309';
                c.beginPath();
                c.ellipse(x + 2, y - 4, 6, 3, 0, 0, Math.PI * 2);
                c.fill();

                // Hatchet handle & blade
                c.fillStyle = '#d97706';
                c.save();
                c.translate(x + 2, y - 4);
                c.rotate(-0.4);
                c.fillRect(0, -10, 2, 10);
                c.fillStyle = '#94a3b8';
                c.fillRect(-4, -10, 5, 3);
                c.restore();

                // Stacked logs
                c.fillStyle = '#451a03';
                c.fillRect(x - 18, y - 2, 10, 5);
                c.fillRect(x - 16, y - 6, 8, 4);
            } else if (lvl === 2) {
                // Tier 2: Timber Workshop with A-Frame Roof & Sawhorse
                // Workshop Shed
                c.fillStyle = '#3e2717';
                c.fillRect(x - 22, y - 18, 28, 20);

                // A-Frame Roof
                c.fillStyle = '#92400e';
                c.beginPath();
                c.moveTo(x - 26, y - 16);
                c.lineTo(x - 8, y - 32);
                c.lineTo(x + 10, y - 16);
                c.closePath();
                c.fill();
                c.strokeStyle = '#b45309';
                c.lineWidth = 1.5;
                c.stroke();

                // Sawhorse with beam & crosscut saw
                c.fillStyle = '#78350f';
                c.fillRect(x + 12, y - 8, 16, 4); // beam
                // Legs
                c.strokeStyle = '#451a03';
                c.lineWidth = 1.5;
                c.beginPath();
                c.moveTo(x + 14, y - 6);
                c.lineTo(x + 11, y + 6);
                c.moveTo(x + 26, y - 6);
                c.lineTo(x + 29, y + 6);
                c.stroke();

                // Saw blade
                c.strokeStyle = '#cbd5e1';
                c.lineWidth = 2;
                c.beginPath();
                c.moveTo(x + 16, y - 14);
                c.lineTo(x + 22, y - 4);
                c.stroke();

                // Hanging lantern
                c.fillStyle = '#fef08a';
                c.beginPath();
                c.arc(x - 8, y - 12, 2.5, 0, Math.PI * 2);
                c.fill();
            } else if (lvl >= 3) {
                // Tier 3: Masterwork Lumber Mill with Crank Wheel
                // Sturdy building body
                c.fillStyle = '#29180c';
                c.fillRect(x - 26, y - 22, 34, 24);

                // Shingled Dutch Barn Roof
                c.fillStyle = '#92400e';
                c.beginPath();
                c.moveTo(x - 30, y - 20);
                c.lineTo(x - 10, y - 36);
                c.lineTo(x + 12, y - 20);
                c.closePath();
                c.fill();
                c.strokeStyle = '#f59e0b';
                c.lineWidth = 1.5;
                c.stroke();

                // Rotating Water/Crank Wheel on right
                var wheelX = x + 20;
                var wheelY = y - 4;
                var wheelR = 12;
                c.save();
                c.translate(wheelX, wheelY);
                c.rotate(t * 1.5);
                c.strokeStyle = '#78350f';
                c.lineWidth = 2;
                c.beginPath();
                c.arc(0, 0, wheelR, 0, Math.PI * 2);
                c.stroke();
                // Spokes
                for (var sp = 0; sp < 4; sp++) {
                    c.rotate(Math.PI / 4);
                    c.beginPath();
                    c.moveTo(-wheelR, 0);
                    c.lineTo(wheelR, 0);
                    c.stroke();
                }
                c.restore();

                // Cured timber stacks on left
                c.fillStyle = '#d97706';
                c.fillRect(x - 32, y - 6, 8, 8);
                c.fillStyle = '#b45309';
                c.fillRect(x - 30, y - 12, 6, 6);

                // Rooftop weathercock / axe flag
                c.fillStyle = '#fbbf24';
                c.fillRect(x - 11, y - 41, 2, 6);
                c.beginPath();
                c.moveTo(x - 9, y - 41);
                c.lineTo(x - 3, y - 38);
                c.lineTo(x - 9, y - 35);
                c.closePath();
                c.fill();
            }
        }

        // --- 3. SCRIBE'S STUDY ARCHIVE RENDERING ---
        function drawArchive(c, plot, t) {
            var x = plot.x;
            var y = plot.y;
            var lvl = plot.currentLevel;

            if (lvl === 0) {
                drawFoundationStake(c, x, y, '📜', 'Archive');
                return;
            }

            drawShadow(c, x, y + 8, 34, 14);

            if (lvl === 1) {
                // Tier 1: Scholar's Blue Expedition Tent & Field Desk
                // Navy blue canvas tent
                c.fillStyle = '#1e3a5f';
                c.beginPath();
                c.moveTo(x - 8, y - 28);
                c.lineTo(x + 22, y + 8);
                c.lineTo(x - 26, y + 8);
                c.closePath();
                c.fill();

                // Tent flap shadow
                c.fillStyle = '#0f172a';
                c.beginPath();
                c.moveTo(x - 8, y - 28);
                c.lineTo(x - 4, y + 8);
                c.lineTo(x - 16, y + 8);
                c.closePath();
                c.fill();

                // Ridgepole
                c.strokeStyle = '#d97706';
                c.lineWidth = 2;
                c.beginPath();
                c.moveTo(x - 8, y - 30);
                c.lineTo(x - 8, y + 8);
                c.stroke();

                // Wooden desk outside tent
                c.fillStyle = '#78350f';
                c.fillRect(x + 10, y - 4, 14, 8);
                // Open book on desk
                c.fillStyle = '#f8fafc';
                c.fillRect(x + 13, y - 6, 8, 3);
            } else if (lvl === 2) {
                // Tier 2: Study Pavilion & Scroll Racks
                // Canopy columns
                c.fillStyle = '#451a03';
                c.fillRect(x - 20, y - 20, 3, 24);
                c.fillRect(x + 17, y - 20, 3, 24);

                // Pavilion Navy Awning
                c.fillStyle = '#1d4ed8';
                c.beginPath();
                c.moveTo(x - 24, y - 18);
                c.lineTo(x - 2, y - 32);
                c.lineTo(x + 21, y - 18);
                c.closePath();
                c.fill();
                c.strokeStyle = '#60a5fa';
                c.lineWidth = 1.5;
                c.stroke();

                // Scholar's Desk with Grimoires & Scrolls
                c.fillStyle = '#5c3924';
                c.fillRect(x - 14, y - 6, 28, 10);

                // Stacked Books
                c.fillStyle = '#dc2626';
                c.fillRect(x - 10, y - 10, 8, 4);
                c.fillStyle = '#16a34a';
                c.fillRect(x - 9, y - 13, 6, 3);

                // Glowing Crystal Inkwell
                c.fillStyle = '#38bdf8';
                c.beginPath();
                c.arc(x + 6, y - 9, 3, 0, Math.PI * 2);
                c.fill();

                // Hanging warm lantern
                c.fillStyle = '#fef08a';
                c.beginPath();
                c.arc(x - 2, y - 15, 2.5, 0, Math.PI * 2);
                c.fill();
            } else if (lvl >= 3) {
                // Tier 3: Grand Celestial Scriptoria with Observatory Telescope
                // Stately Pavilion Base
                c.fillStyle = '#172554';
                c.fillRect(x - 24, y - 20, 48, 24);

                // Pagoda Roof
                c.fillStyle = '#1e3a8a';
                c.beginPath();
                c.moveTo(x - 28, y - 18);
                c.lineTo(x, y - 38);
                c.lineTo(x + 28, y - 18);
                c.closePath();
                c.fill();
                c.strokeStyle = '#38bdf8';
                c.lineWidth = 2;
                c.stroke();

                // Golden Finial Peak
                c.fillStyle = '#fbbf24';
                c.beginPath();
                c.arc(x, y - 38, 4, 0, Math.PI * 2);
                c.fill();

                // Brass Celestial Telescope on rooftop pointing right
                c.strokeStyle = '#f59e0b';
                c.lineWidth = 3;
                c.beginPath();
                c.moveTo(x + 6, y - 28);
                c.lineTo(x + 22, y - 40);
                c.stroke();
                // Telescope lens glow
                c.fillStyle = '#38bdf8';
                c.beginPath();
                c.arc(x + 22, y - 40, 2.5, 0, Math.PI * 2);
                c.fill();

                // Bookshelves inside
                c.fillStyle = '#78350f';
                c.fillRect(x - 18, y - 14, 12, 16);
                c.fillStyle = '#fbbf24';
                c.fillRect(x - 16, y - 11, 8, 3);
                c.fillStyle = '#38bdf8';
                c.fillRect(x - 16, y - 6, 8, 3);

                // Floating Mystic Study Motes
                var moteX = x + Math.cos(t * 2) * 14;
                var moteY = (y - 20) + Math.sin(t * 2) * 6;
                c.fillStyle = '#60a5fa';
                c.beginPath();
                c.arc(moteX, moteY, 2, 0, Math.PI * 2);
                c.fill();
            }
        }

        // --- 4. WARRIOR'S FORGE RENDERING ---
        function drawForge(c, plot, t) {
            var x = plot.x;
            var y = plot.y;
            var lvl = plot.currentLevel;

            if (lvl === 0) {
                drawFoundationStake(c, x, y, '🔨', 'Forge');
                return;
            }

            drawShadow(c, x, y + 8, 28, 12);

            if (lvl === 1) {
                // Tier 1: Anvil on Stump & Tool Rack
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

                // Water bucket
                c.fillStyle = '#334155';
                c.fillRect(x + 14, y - 2, 8, 8);
            } else if (lvl === 2) {
                // Tier 2: Smithy Furnace with Coals & Weapon Rack
                // Brick hearth furnace on left
                c.fillStyle = '#7f1d1d';
                c.fillRect(x - 22, y - 18, 16, 20);

                // Furnace opening with glowing embers
                c.fillStyle = '#1c1917';
                c.fillRect(x - 18, y - 12, 10, 10);
                var emberGlow = '#f97316';
                c.fillStyle = emberGlow;
                c.beginPath();
                c.arc(x - 13, y - 7, 3 + Math.sin(t * 8) * 1, 0, Math.PI * 2);
                c.fill();

                // Chimney pipe
                c.fillStyle = '#334155';
                c.fillRect(x - 17, y - 28, 6, 10);

                // Anvil on right
                c.fillStyle = '#64748b';
                c.fillRect(x + 2, y - 10, 16, 6);
                c.fillStyle = '#475569';
                c.fillRect(x + 4, y - 4, 12, 8);

                // Weapons rack behind with shield
                c.fillStyle = '#b45309';
                c.beginPath();
                c.arc(x + 18, y - 10, 6, 0, Math.PI * 2);
                c.fill();
                c.fillStyle = '#3b82f6';
                c.beginPath();
                c.arc(x + 18, y - 10, 4, 0, Math.PI * 2);
                c.fill();
            } else if (lvl >= 3) {
                // Tier 3: Masterwork Runeforge with Basalt Masonry & Arcane Anvil
                // Volcanic Basalt Forge Body
                c.fillStyle = '#18181b';
                c.fillRect(x - 24, y - 22, 20, 24);

                // Magma crucible
                c.fillStyle = '#ef4444';
                c.fillRect(x - 20, y - 14, 12, 10);
                c.fillStyle = '#f59e0b';
                c.beginPath();
                c.ellipse(x - 14, y - 9, 4, 3 + Math.sin(t * 6), 0, 0, Math.PI * 2);
                c.fill();

                // High-temperature Steel Chimney
                c.fillStyle = '#27272a';
                c.fillRect(x - 18, y - 36, 8, 14);

                // Runic Anvil
                c.fillStyle = '#3f3f46';
                c.fillRect(x + 2, y - 12, 20, 7);
                c.fillStyle = '#18181b';
                c.fillRect(x + 4, y - 5, 16, 9);
                // Glowing Cyan Rune Carving on Anvil
                c.strokeStyle = '#38bdf8';
                c.lineWidth = 1.5;
                c.beginPath();
                c.moveTo(x + 6, y - 9);
                c.lineTo(x + 16, y - 9);
                c.moveTo(x + 11, y - 12);
                c.lineTo(x + 11, y - 6);
                c.stroke();

                // Enchanted sword mounted on rack
                c.strokeStyle = '#38bdf8';
                c.lineWidth = 2;
                c.beginPath();
                c.moveTo(x + 24, y - 24);
                c.lineTo(x + 24, y - 2);
                c.stroke();
                // Golden crossguard
                c.strokeStyle = '#fbbf24';
                c.lineWidth = 2;
                c.beginPath();
                c.moveTo(x + 21, y - 8);
                c.lineTo(x + 27, y - 8);
                c.stroke();

                // Fiery sparks rising from chimney
                c.fillStyle = '#f97316';
                var sparkY = (y - 36) - ((t * 26) % 18);
                c.beginPath();
                c.arc(x - 14 + Math.sin(t * 4) * 3, sparkY, 1.3, 0, Math.PI * 2);
                c.fill();
            }
        }

        function drawPlot(c, plot, animTime) {
            if (plot.id === 'hearth') {
                drawHearth(c, plot, animTime);
            } else if (plot.id === 'sawmill') {
                drawSawmill(c, plot, animTime);
            } else if (plot.id === 'archive') {
                drawArchive(c, plot, animTime);
            } else if (plot.id === 'forge') {
                drawForge(c, plot, animTime);
            }
        }

        // =========================================================================
        // IN-WORLD CONTEXTUAL PROMPT BADGE RENDERING
        // =========================================================================
        function drawPrompt(c, camera) {
            if (!activePromptPlot) return;

            var plot = activePromptPlot;
            // Target coordinates above structure
            var targetX = plot.x;
            var targetY = plot.y - 42;
            var bob = Math.sin(animTimer * 5) * 3;
            var py = targetY + bob;

            var title = '';
            var costStr = '';
            var canAfford = plot.canAfford;

            if (plot.isMax) {
                title = '⭐ ' + plot.name + ' (Max Lv. 3)';
            } else if (plot.currentLevel === 0) {
                title = '[E] Build ' + plot.name;
                if (plot.cost) {
                    costStr = '🪵 ' + plot.cost.wood + (plot.cost.crystals > 0 ? ' 💎 ' + plot.cost.crystals : '');
                }
            } else {
                title = '[E] Upgrade (Lv ' + (plot.currentLevel + 1) + ')';
                if (plot.cost) {
                    costStr = '🪵 ' + plot.cost.wood + (plot.cost.crystals > 0 ? ' 💎 ' + plot.cost.crystals : '');
                }
            }

            var text = costStr ? (title + ' · ' + costStr) : title;

            c.save();
            c.font = 'bold 9px sans-serif';
            var tw = c.measureText(text).width;
            var padX = 8;
            var padY = 4;
            var boxW = tw + padX * 2;
            var boxH = 18;
            var bx = targetX - boxW / 2;
            var by = py - boxH / 2;

            // Background pill
            c.fillStyle = 'rgba(15, 23, 42, 0.94)';
            c.beginPath();
            c.roundRect(bx, by, boxW, boxH, 9);
            c.fill();

            // Border (gold if affordable, cyan if max, reddish if unaffordable)
            if (plot.isMax) {
                c.strokeStyle = 'rgba(56, 189, 248, 0.7)';
            } else if (canAfford) {
                c.strokeStyle = 'rgba(34, 197, 94, 0.8)';
            } else {
                c.strokeStyle = 'rgba(239, 68, 68, 0.6)';
            }
            c.lineWidth = 1.5;
            c.stroke();

            // Text
            c.textAlign = 'center';
            c.textBaseline = 'middle';
            if (plot.isMax) {
                c.fillStyle = '#38bdf8';
            } else if (canAfford) {
                c.fillStyle = '#4ade80';
            } else {
                c.fillStyle = '#fca5a5';
            }
            c.fillText(text, targetX, py);

            c.restore();
        }

        return {
            getPlots: getPlots,
            getPlot: getPlot,
            getNearestPlot: getNearestPlot,
            getActivePromptPlot: function () { return activePromptPlot; },
            update: update,
            interact: interact,
            drawPlot: drawPlot,
            drawPrompt: drawPrompt
        };
    }

    window.CourseForgePlots = {
        create: createPlotManager,
        INTERACT_RADIUS: INTERACT_RADIUS
    };
})(window);
