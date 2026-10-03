/**
 * Course Forge Minigame — Entities & World Simulation.
 *
 * Implements:
 * - Player: scholar avatar with walk cycle, directional facing, chopping/striking.
 * - Trees: harvestable forest timber with shake physics, stump respawn, and wood drops.
 * - Grove Slimes: gentle forest creatures that yield study crystals, with knockback.
 * - Drops: floating collectables with magnetic attraction vacuum.
 * - Collision & physics: separation against trunks, campfire, and clearing boundaries.
 */
(function (window) {
    'use strict';

    var WORLD_WIDTH = 1920;
    var WORLD_HEIGHT = 1200;

    var ARENA = {
        minX: 55,
        maxX: 1865,
        minY: 70,
        maxY: 1130
    };

    var PLOT_HEARTH = { x: 960, y: 600, radius: 24 };
    var PLOT_SAWMILL = { x: 850, y: 535, radius: 26 };
    var PLOT_ARCHIVE = { x: 1070, y: 535, radius: 26 };
    var PLOT_FORGE = { x: 960, y: 695, radius: 22 };

    var OBSTACLES = [PLOT_HEARTH, PLOT_SAWMILL, PLOT_ARCHIVE, PLOT_FORGE];

    function dist(x1, y1, x2, y2) {
        var dx = x2 - x1;
        var dy = y2 - y1;
        return Math.hypot(dx, dy);
    }

    function createPlayer(x, y) {
        return {
            x: x,
            y: y,
            vx: 0,
            vy: 0,
            radius: 12,
            facing: 'right',
            isMoving: false,
            walkTime: 0,
            swingTimer: 0,
            swingDuration: 0.22,
            swingType: 'chop', // 'chop' or 'strike'
            swingAngle: 0
        };
    }

    function createTree(x, y, variant) {
        var v = variant || 'oak';
        var maxHp = 3;
        if (v === 'pine') maxHp = 4;
        if (v === 'ancient_oak') maxHp = 5;
        if (v === 'crystal_pine') maxHp = 4;
        return {
            id: 'tree_' + Math.floor(x) + '_' + Math.floor(y),
            tag: 'tree',
            x: x,
            y: y,
            variant: v,
            radius: 18,
            maxHp: maxHp,
            hp: maxHp,
            shakeTimer: 0,
            isFelled: false,
            respawnTimer: 0,
            respawnDuration: 10
        };
    }

    function createEnemy(x, y, id, variant) {
        return {
            id: id || ('slime_' + Math.random().toString(36).substr(2, 6)),
            tag: 'enemy',
            variant: variant || 'forest',
            startX: x,
            startY: y,
            x: x,
            y: y,
            vx: 0,
            vy: 0,
            radius: 14,
            maxHp: 3,
            hp: 3,
            flashTimer: 0,
            knockbackX: 0,
            knockbackY: 0,
            isDefeated: false,
            respawnTimer: 0,
            respawnDuration: 9,
            wanderAngle: Math.random() * Math.PI * 2,
            wanderTimer: 1 + Math.random() * 2,
            hopTime: Math.random() * 2
        };
    }

    function createDrop(x, y, type) {
        var angle = Math.random() * Math.PI * 2;
        var speed = 30 + Math.random() * 40;
        return {
            id: 'drop_' + Math.random().toString(36).substr(2, 8),
            tag: 'drop',
            type: type, // 'wood' or 'crystal'
            x: x,
            y: y,
            radius: 12,
            vx: Math.cos(angle) * speed,
            vy: Math.sin(angle) * speed - 15,
            z: 0,
            vz: 50 + Math.random() * 30,
            groundY: y,
            age: 0,
            collected: false
        };
    }

    function createEntityManager(state, soundSystem, camera) {
        var player = createPlayer(960, 640);
        var spatial = window.CourseForgeSpatial ? window.CourseForgeSpatial.create(128) : null;

        var treeDefs = [
            // 1. Central Camp clearing borders (around 960, 600)
            { x: 780,  y: 520, variant: 'pine' },
            { x: 1140, y: 520, variant: 'oak' },
            { x: 750,  y: 680, variant: 'oak' },
            { x: 1170, y: 680, variant: 'pine' },
            { x: 860,  y: 740, variant: 'oak' },
            { x: 1060, y: 740, variant: 'pine' },
            { x: 960,  y: 450, variant: 'pine' },
            { x: 880,  y: 440, variant: 'oak' },
            { x: 1040, y: 440, variant: 'pine' },

            // 2. Western Timber Grove (rich lumber zone, x: 140-560, y: 400-760)
            { x: 420,  y: 480, variant: 'ancient_oak' },
            { x: 340,  y: 540, variant: 'pine' },
            { x: 480,  y: 620, variant: 'ancient_oak' },
            { x: 360,  y: 690, variant: 'pine' },
            { x: 260,  y: 510, variant: 'oak' },
            { x: 220,  y: 630, variant: 'pine' },
            { x: 520,  y: 420, variant: 'oak' },
            { x: 180,  y: 440, variant: 'ancient_oak' },
            { x: 160,  y: 560, variant: 'pine' },
            { x: 280,  y: 730, variant: 'ancient_oak' },
            { x: 440,  y: 750, variant: 'oak' },

            // 3. Eastern Crystal Hollow (mystic cyan/teal grove, x: 1340-1780, y: 420-740)
            { x: 1400, y: 490, variant: 'crystal_pine' },
            { x: 1520, y: 540, variant: 'crystal_pine' },
            { x: 1440, y: 640, variant: 'crystal_pine' },
            { x: 1580, y: 670, variant: 'crystal_pine' },
            { x: 1680, y: 520, variant: 'crystal_pine' },
            { x: 1640, y: 620, variant: 'crystal_pine' },
            { x: 1740, y: 460, variant: 'crystal_pine' },
            { x: 1760, y: 610, variant: 'crystal_pine' },
            { x: 1340, y: 600, variant: 'crystal_pine' },
            { x: 1500, y: 730, variant: 'crystal_pine' },

            // 4. Northern Ancient Woodlands & Shrine (x: 640-1280, y: 150-380)
            { x: 820,  y: 310, variant: 'pine' },
            { x: 960,  y: 290, variant: 'ancient_oak' },
            { x: 1100, y: 320, variant: 'pine' },
            { x: 680,  y: 360, variant: 'oak' },
            { x: 1240, y: 360, variant: 'pine' },
            { x: 740,  y: 220, variant: 'ancient_oak' },
            { x: 880,  y: 190, variant: 'pine' },
            { x: 1040, y: 190, variant: 'pine' },
            { x: 1180, y: 230, variant: 'ancient_oak' },

            // 5. Southern Meadows (x: 450-1450, y: 820-1080)
            { x: 620,  y: 860, variant: 'oak' },
            { x: 760,  y: 920, variant: 'oak' },
            { x: 960,  y: 960, variant: 'oak' },
            { x: 1160, y: 920, variant: 'oak' },
            { x: 1300, y: 860, variant: 'oak' },
            { x: 850,  y: 1040, variant: 'oak' },
            { x: 1070, y: 1040, variant: 'pine' }
        ];

        var trees = treeDefs.map(function (def) {
            var t = createTree(def.x, def.y, def.variant);
            if (spatial) spatial.insert(t);
            return t;
        });

        var enemies = [
            // Outskirts of Central Camp
            createEnemy(1120, 560, 'slime_c1', 'forest'),
            createEnemy(800,  620, 'slime_c2', 'forest'),
            // Western Timber Grove
            createEnemy(380,  580, 'slime_w1', 'forest'),
            createEnemy(240,  660, 'slime_w2', 'forest'),
            // Eastern Crystal Hollow
            createEnemy(1480, 560, 'slime_e1', 'crystal'),
            createEnemy(1620, 610, 'slime_e2', 'crystal'),
            // Northern Ancient Woodlands
            createEnemy(860,  240, 'slime_n1', 'ancient'),
            createEnemy(1060, 240, 'slime_n2', 'ancient'),
            // Southern Meadows
            createEnemy(700,  920, 'slime_s1', 'forest'),
            createEnemy(1220, 920, 'slime_s2', 'forest')
        ];
        enemies.forEach(function (e) {
            if (spatial) spatial.insert(e);
        });

        var drops = [];
        var particles = [];

        function addDrop(drop) {
            drops.push(drop);
            if (spatial) spatial.insert(drop);
        }

        function addParticle(p) {
            particles.push(p);
        }

        function spawnFloatingText(text, x, y, color) {
            addParticle({
                type: 'text',
                text: text,
                x: x,
                y: y,
                vx: (Math.random() - 0.5) * 15,
                vy: -35 - Math.random() * 15,
                life: 0.9,
                maxLife: 0.9,
                color: color || '#fcd34d'
            });
        }

        function spawnImpactChips(x, y, count, color) {
            for (var i = 0; i < count; i++) {
                var angle = Math.random() * Math.PI * 2;
                var speed = 40 + Math.random() * 60;
                addParticle({
                    type: 'chip',
                    x: x,
                    y: y,
                    vx: Math.cos(angle) * speed,
                    vy: Math.sin(angle) * speed - 20,
                    life: 0.4 + Math.random() * 0.3,
                    maxLife: 0.6,
                    size: 3 + Math.random() * 3,
                    color: color || '#b45309'
                });
            }
        }

        function spawnSparkles(x, y, count, color) {
            for (var i = 0; i < count; i++) {
                var angle = Math.random() * Math.PI * 2;
                var speed = 30 + Math.random() * 50;
                addParticle({
                    type: 'sparkle',
                    x: x,
                    y: y,
                    vx: Math.cos(angle) * speed,
                    vy: Math.sin(angle) * speed,
                    life: 0.5 + Math.random() * 0.4,
                    maxLife: 0.7,
                    size: 2 + Math.random() * 3,
                    color: color || '#38bdf8'
                });
            }
        }

        function resolveCircleCollision(entity, obstacle, padding) {
            var pad = padding || 0;
            var minDist = entity.radius + obstacle.radius + pad;
            var d = dist(entity.x, entity.y, obstacle.x, obstacle.y);
            if (d < minDist && d > 0.001) {
                var overlap = minDist - d;
                var nx = (entity.x - obstacle.x) / d;
                var ny = (entity.y - obstacle.y) / d;
                entity.x += nx * overlap;
                entity.y += ny * overlap;
            }
        }

        function handleAction() {
            var stateData = state.getData();
            var axeLevel = stateData.levels.axe || 1;
            var strikeLevel = stateData.levels.strike || 1;
            var plotsData = stateData.plots || {};

            // Check if facing or close to an enemy
            var targetEnemy = null;
            var minEnemyDist = 52;
            var candidateEnemies = spatial ? spatial.queryRadius(player.x, player.y, 55, 'enemy') : enemies;
            for (var i = 0; i < candidateEnemies.length; i++) {
                var e = candidateEnemies[i];
                if (e.isDefeated) continue;
                var d = dist(player.x, player.y, e.x, e.y);
                if (d < minEnemyDist) {
                    minEnemyDist = d;
                    targetEnemy = e;
                }
            }

            // Check if near a harvestable tree
            var targetTree = null;
            var minTreeDist = 48;
            var candidateTrees = spatial ? spatial.queryRadius(player.x, player.y, 55, 'tree') : trees;
            for (var j = 0; j < candidateTrees.length; j++) {
                var t = candidateTrees[j];
                if (t.isFelled) continue;
                var td = dist(player.x, player.y, t.x, t.y);
                if (td < minTreeDist) {
                    minTreeDist = td;
                    targetTree = t;
                }
            }

            // Attack enemy prioritizes if enemy is close
            if (targetEnemy && minEnemyDist <= 46) {
                player.swingType = 'strike';
                player.swingTimer = player.swingDuration;
                player.facing = (targetEnemy.x < player.x) ? 'left' : 'right';

                var damage = strikeLevel + (plotsData.forge || 0);
                targetEnemy.hp -= damage;
                targetEnemy.flashTimer = 0.22;

                // Knockback
                var angle = Math.atan2(targetEnemy.y - player.y, targetEnemy.x - player.x);
                targetEnemy.knockbackX = Math.cos(angle) * 140;
                targetEnemy.knockbackY = Math.sin(angle) * 140;

                spawnSparkles(targetEnemy.x, targetEnemy.y - 10, 8, '#c084fc');
                spawnFloatingText('-' + damage, targetEnemy.x, targetEnemy.y - 18, '#ec4899');
                if (soundSystem) soundSystem.playStrike();
                if (camera) camera.shake(2.0, 0.12);

                if (targetEnemy.hp <= 0) {
                    targetEnemy.isDefeated = true;
                    targetEnemy.respawnTimer = targetEnemy.respawnDuration;
                    state.recordEnemy();
                    if (soundSystem) soundSystem.playDefeat();
                    if (camera) camera.shake(5.0, 0.25);

                    // Drops based on slime variant + archive study plot bonus
                    var crystalCount = 1 + (plotsData.archive || 0);
                    if (targetEnemy.variant === 'crystal') {
                        crystalCount = 2 + (plotsData.archive || 0) + Math.floor(Math.random() * (1 + Math.min(strikeLevel, 3)));
                    } else if (targetEnemy.variant === 'ancient') {
                        crystalCount = 1 + (plotsData.archive || 0) + Math.floor(Math.random() * 2);
                        addDrop(createDrop(targetEnemy.x, targetEnemy.y, 'wood'));
                    } else {
                        crystalCount = 1 + (plotsData.archive || 0) + Math.floor(Math.random() * (1 + Math.min(strikeLevel, 2)));
                    }
                    for (var c = 0; c < crystalCount; c++) {
                        addDrop(createDrop(targetEnemy.x, targetEnemy.y, 'crystal'));
                    }
                    spawnFloatingText('Defeated!', targetEnemy.x, targetEnemy.y - 28, '#a855f7');
                    spawnSparkles(targetEnemy.x, targetEnemy.y, 16, '#a855f7');
                }
                return;
            }

            // Chop tree if available
            if (targetTree) {
                player.swingType = 'chop';
                player.swingTimer = player.swingDuration;
                player.facing = (targetTree.x < player.x) ? 'left' : 'right';

                var chopDmg = axeLevel + (plotsData.sawmill || 0);
                targetTree.hp -= chopDmg;
                targetTree.shakeTimer = 0.25;

                spawnImpactChips(targetTree.x, targetTree.y - 20, 6, '#d97706');
                spawnFloatingText('-' + chopDmg, targetTree.x, targetTree.y - 35, '#fbbf24');
                if (soundSystem) soundSystem.playChop();
                if (camera) camera.shake(1.5, 0.1);

                // Bonus drop on hit
                if (targetTree.variant === 'crystal_pine') {
                    spawnSparkles(targetTree.x, targetTree.y - 20, 6, '#38bdf8');
                    if (Math.random() < 0.35) {
                        addDrop(createDrop(targetTree.x, targetTree.y, 'crystal'));
                    }
                } else if (Math.random() < 0.35) {
                    addDrop(createDrop(targetTree.x, targetTree.y, 'wood'));
                }

                if (targetTree.hp <= 0) {
                    targetTree.isFelled = true;
                    targetTree.respawnTimer = targetTree.respawnDuration;
                    state.recordTree();
                    if (soundSystem) soundSystem.playTreeFall();
                    if (camera) camera.shake(3.5, 0.22);

                    // Drop bundles according to tree variant + sawmill bonus
                    var sawmillBonus = plotsData.sawmill || 0;
                    if (targetTree.variant === 'crystal_pine') {
                        addDrop(createDrop(targetTree.x, targetTree.y, 'wood'));
                        addDrop(createDrop(targetTree.x, targetTree.y, 'wood'));
                        for (var sb = 0; sb < sawmillBonus; sb++) {
                            addDrop(createDrop(targetTree.x, targetTree.y, 'wood'));
                        }
                        addDrop(createDrop(targetTree.x, targetTree.y, 'crystal'));
                        if (Math.random() < 0.5) {
                            addDrop(createDrop(targetTree.x, targetTree.y, 'crystal'));
                        }
                    } else if (targetTree.variant === 'ancient_oak') {
                        var woodCount = 3 + sawmillBonus + Math.floor(Math.random() * (2 + axeLevel));
                        for (var w = 0; w < woodCount; w++) {
                            addDrop(createDrop(targetTree.x, targetTree.y, 'wood'));
                        }
                    } else {
                        var woodCount = 2 + sawmillBonus + Math.floor(Math.random() * (2 + axeLevel));
                        for (var w = 0; w < woodCount; w++) {
                            addDrop(createDrop(targetTree.x, targetTree.y, 'wood'));
                        }
                    }
                    spawnFloatingText('Timber!', targetTree.x, targetTree.y - 45, '#f59e0b');
                    spawnImpactChips(targetTree.x, targetTree.y - 15, 14, '#92400e');
                }
                return;
            }

            // Air swing if nothing targeted
            player.swingType = 'chop';
            player.swingTimer = player.swingDuration;
            if (soundSystem) soundSystem.playSwing();
        }

        function update(dt, moveVec, actionRequested) {
            var stateData = state.getData();
            var speedLevel = stateData.levels.speed || 1;
            var backpackLevel = stateData.levels.backpack || 1;
            var plotsData = stateData.plots || {};

            var hearthBonus = plotsData.hearth ? (plotsData.hearth - 1) * 8 : 0;
            var archiveBonus = (plotsData.archive || 0) * 12;
            var forgeSpeedBonus = (plotsData.forge || 0) * 10;

            var baseSpeed = 135 + (speedLevel - 1) * 24 + forgeSpeedBonus;
            var pickupRadius = 46 + (backpackLevel - 1) * 20 + hearthBonus + archiveBonus;

            // Handle edge-triggered action
            if (actionRequested && player.swingTimer <= 0.05) {
                handleAction();
            }

            // Player Swing timer
            if (player.swingTimer > 0) {
                player.swingTimer -= dt;
            }

            // Movement update
            var targetVx = moveVec.x * baseSpeed;
            var targetVy = moveVec.y * baseSpeed;

            // Smooth velocity interpolation
            var accel = Math.min(1, dt * 16);
            player.vx += (targetVx - player.vx) * accel;
            player.vy += (targetVy - player.vy) * accel;

            player.x += player.vx * dt;
            player.y += player.vy * dt;

            // Determine facing
            if (moveVec.x > 0.05) player.facing = 'right';
            else if (moveVec.x < -0.05) player.facing = 'left';

            // Walk animation cycle
            var speedMag = Math.hypot(player.vx, player.vy);
            player.isMoving = speedMag > 5;
            if (player.isMoving) {
                player.walkTime += dt * (speedMag / 20);
            } else {
                player.walkTime = 0;
            }

            // Arena boundary clamps
            player.x = Math.max(ARENA.minX, Math.min(ARENA.maxX, player.x));
            player.y = Math.max(ARENA.minY, Math.min(ARENA.maxY, player.y));

            // Obstacle collisions
            for (var o = 0; o < OBSTACLES.length; o++) {
                resolveCircleCollision(player, OBSTACLES[o], 4);
            }

            // Standing tree trunk collisions (only nearby trees)
            var nearbyTrees = spatial ? spatial.queryRadius(player.x, player.y, 42, 'tree') : trees;
            for (var t = 0; t < nearbyTrees.length; t++) {
                var tree = nearbyTrees[t];
                if (!tree.isFelled) {
                    resolveCircleCollision(player, tree, 2);
                }
            }

            // Tree timers (shake & respawn)
            for (var tr = 0; tr < trees.length; tr++) {
                var treeObj = trees[tr];
                if (treeObj.shakeTimer > 0) {
                    treeObj.shakeTimer -= dt;
                }
                if (treeObj.isFelled) {
                    treeObj.respawnTimer -= dt;
                    if (treeObj.respawnTimer <= 0) {
                        treeObj.isFelled = false;
                        treeObj.hp = treeObj.maxHp;
                        if (spatial) spatial.update(treeObj);
                        spawnImpactChips(treeObj.x, treeObj.y - 20, 8, '#22c55e');
                        spawnFloatingText('Sprouted!', treeObj.x, treeObj.y - 30, '#4ade80');
                    }
                }
            }

            // Enemies update
            for (var eIdx = 0; eIdx < enemies.length; eIdx++) {
                var enemy = enemies[eIdx];

                if (enemy.flashTimer > 0) enemy.flashTimer -= dt;

                // Respawn
                if (enemy.isDefeated) {
                    enemy.respawnTimer -= dt;
                    if (enemy.respawnTimer <= 0) {
                        enemy.isDefeated = false;
                        enemy.hp = enemy.maxHp;
                        enemy.x = enemy.startX;
                        enemy.y = enemy.startY;
                        if (spatial) spatial.update(enemy);
                        var sColor = enemy.variant === 'crystal' ? '#38bdf8' : (enemy.variant === 'ancient' ? '#c084fc' : '#34d399');
                        var sName = enemy.variant === 'crystal' ? 'Crystal Slime!' : (enemy.variant === 'ancient' ? 'Ancient Slime!' : 'Forest Slime!');
                        spawnSparkles(enemy.x, enemy.y, 10, sColor);
                        spawnFloatingText(sName, enemy.x, enemy.y - 20, sColor);
                    }
                    continue;
                }

                // Knockback dampening
                enemy.x += enemy.knockbackX * dt;
                enemy.y += enemy.knockbackY * dt;
                enemy.knockbackX *= Math.pow(0.05, dt);
                enemy.knockbackY *= Math.pow(0.05, dt);

                var dToPlayer = dist(enemy.x, enemy.y, player.x, player.y);

                // AI behavior
                if (dToPlayer < 130 && dToPlayer > 18) {
                    // Chase player gently
                    var chaseSpeed = 50;
                    var angleToP = Math.atan2(player.y - enemy.y, player.x - enemy.x);
                    enemy.vx = Math.cos(angleToP) * chaseSpeed;
                    enemy.vy = Math.sin(angleToP) * chaseSpeed;
                } else {
                    // Gentle wander near origin
                    enemy.wanderTimer -= dt;
                    if (enemy.wanderTimer <= 0) {
                        enemy.wanderTimer = 1.5 + Math.random() * 2.5;
                        var dFromOrigin = dist(enemy.x, enemy.y, enemy.startX, enemy.startY);
                        if (dFromOrigin > 90) {
                            enemy.wanderAngle = Math.atan2(enemy.startY - enemy.y, enemy.startX - enemy.x);
                        } else {
                            enemy.wanderAngle = Math.random() * Math.PI * 2;
                        }
                    }
                    var wanderSpeed = 22;
                    enemy.vx = Math.cos(enemy.wanderAngle) * wanderSpeed;
                    enemy.vy = Math.sin(enemy.wanderAngle) * wanderSpeed;
                }

                enemy.x += enemy.vx * dt;
                enemy.y += enemy.vy * dt;
                enemy.hopTime += dt * 4;

                // Keep inside arena
                enemy.x = Math.max(ARENA.minX, Math.min(ARENA.maxX, enemy.x));
                enemy.y = Math.max(ARENA.minY, Math.min(ARENA.maxY, enemy.y));

                if (spatial) {
                    spatial.update(enemy);
                }

                // Player bump
                if (dToPlayer < (player.radius + enemy.radius)) {
                    var bumpAngle = Math.atan2(player.y - enemy.y, player.x - enemy.x);
                    player.x += Math.cos(bumpAngle) * 4;
                    player.y += Math.sin(bumpAngle) * 4;
                    enemy.x -= Math.cos(bumpAngle) * 4;
                    enemy.y -= Math.sin(bumpAngle) * 4;
                }
            }

            // Drops update & magnetic vacuum
            for (var d = drops.length - 1; d >= 0; d--) {
                var item = drops[d];
                item.age += dt;

                // Physics arc bounce
                if (item.z > 0 || item.vz !== 0) {
                    item.z += item.vz * dt;
                    item.vz -= 220 * dt; // gravity
                    item.x += item.vx * dt;
                    item.y += item.vy * dt;
                    item.vx *= Math.pow(0.2, dt);
                    item.vy *= Math.pow(0.2, dt);

                    if (item.z <= 0) {
                        item.z = 0;
                        item.vz = -item.vz * 0.4;
                        if (Math.abs(item.vz) < 15) item.vz = 0;
                    }
                }

                // Vacuum pull to player
                var dItemToP = dist(item.x, item.y, player.x, player.y - 10);
                if (dItemToP < pickupRadius) {
                    var pullSpeed = Math.min(320, 90 + (1 - dItemToP / pickupRadius) * 230);
                    var pAngle = Math.atan2((player.y - 10) - item.y, player.x - item.x);
                    item.x += Math.cos(pAngle) * pullSpeed * dt;
                    item.y += Math.sin(pAngle) * pullSpeed * dt;
                }

                // Pickup collection
                if (dItemToP < 18) {
                    item.collected = true;
                    if (item.type === 'wood') {
                        state.addWood(1);
                        spawnFloatingText('+1 Wood', item.x, item.y - 12, '#f59e0b');
                        spawnImpactChips(item.x, item.y, 4, '#b45309');
                        if (soundSystem) soundSystem.playWoodPickup();
                    } else if (item.type === 'crystal') {
                        state.addCrystals(1);
                        spawnFloatingText('+1 Crystal', item.x, item.y - 12, '#38bdf8');
                        spawnSparkles(item.x, item.y, 6, '#38bdf8');
                        if (soundSystem) soundSystem.playCrystalPickup();
                    }
                    if (spatial) spatial.remove(item);
                    drops.splice(d, 1);
                }
            }

            // Particles update
            for (var pIdx = particles.length - 1; pIdx >= 0; pIdx--) {
                var p = particles[pIdx];
                p.life -= dt;
                if (p.life <= 0) {
                    particles.splice(pIdx, 1);
                    continue;
                }
                p.x += p.vx * dt;
                p.y += p.vy * dt;
                if (p.type === 'chip') {
                    p.vy += 120 * dt; // gravity
                }
            }
        }

        return {
            getPlayer: function () { return player; },
            getTrees: function () { return trees; },
            getEnemies: function () { return enemies; },
            getDrops: function () { return drops; },
            getParticles: function () { return particles; },
            getObstacles: function () { return OBSTACLES; },
            getArena: function () { return ARENA; },
            getSpatial: function () { return spatial; },
            update: update,
            spawnFloatingText: spawnFloatingText,
            spawnSparkles: spawnSparkles
        };
    }

    window.CourseForgeEntities = {
        createManager: createEntityManager
    };
})(window);
