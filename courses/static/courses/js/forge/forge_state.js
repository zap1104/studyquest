/**
 * Course Forge Minigame — Game State & Progression Store.
 *
 * Scoped entirely to the active course generation session.
 * Stores resources (wood, crystals), player stats, and camp upgrades.
 * Persists to sessionStorage so accidental page refreshes don't wipe progress,
 * and clears on terminal readiness.
 */
(function (window) {
    'use strict';

    var STORAGE_PREFIX = 'studyquest:forge:';

    var UPGRADE_DEFS = {
        axe: {
            id: 'axe',
            name: 'Sturdy Hatchet',
            icon: '🪓',
            description: 'Chop trees faster with increased harvesting damage',
            maxLevel: 5,
            costs: [
                { wood: 8, crystals: 0 },
                { wood: 20, crystals: 2 },
                { wood: 45, crystals: 6 },
                { wood: 80, crystals: 14 }
            ]
        },
        strike: {
            id: 'strike',
            name: 'Grove Dagger',
            icon: '🗡️',
            description: 'Fend off forest slimes with higher strike power',
            maxLevel: 5,
            costs: [
                { wood: 6, crystals: 3 },
                { wood: 18, crystals: 8 },
                { wood: 40, crystals: 18 },
                { wood: 75, crystals: 30 }
            ]
        },
        backpack: {
            id: 'backpack',
            name: 'Magnetic Satchel',
            icon: '🎒',
            description: 'Expands resource attraction vacuum radius',
            maxLevel: 5,
            costs: [
                { wood: 12, crystals: 2 },
                { wood: 30, crystals: 6 },
                { wood: 60, crystals: 14 },
                { wood: 100, crystals: 24 }
            ]
        },
        speed: {
            id: 'speed',
            name: 'Trail Boots',
            icon: '🥾',
            description: 'Increases camp walking and exploration speed',
            maxLevel: 5,
            costs: [
                { wood: 16, crystals: 4 },
                { wood: 38, crystals: 10 },
                { wood: 75, crystals: 20 },
                { wood: 125, crystals: 36 }
            ]
        }
    };

    var PLOT_DEFS = {
        hearth: {
            id: 'hearth',
            name: 'Camp Hearth',
            icon: '⛺',
            x: 960,
            y: 600,
            radius: 24,
            description: 'Heart of Camp — warms the camp and extends resource vacuum aura',
            maxLevel: 3,
            initialLevel: 1,
            costs: [
                null,
                { wood: 15, crystals: 3 },
                { wood: 40, crystals: 10 }
            ]
        },
        sawmill: {
            id: 'sawmill',
            name: "Woodcutter's Sawmill",
            icon: '🪵',
            x: 850,
            y: 535,
            radius: 28,
            description: 'Lumber mill that increases timber harvesting speed and wood yields',
            maxLevel: 3,
            initialLevel: 0,
            costs: [
                { wood: 10, crystals: 1 },
                { wood: 25, crystals: 5 },
                { wood: 55, crystals: 12 }
            ]
        },
        archive: {
            id: 'archive',
            name: "Scribe's Study Archive",
            icon: '📜',
            x: 1070,
            y: 535,
            radius: 28,
            description: 'Expedition scriptorium that attracts study crystals and boosts absorption',
            maxLevel: 3,
            initialLevel: 0,
            costs: [
                { wood: 12, crystals: 4 },
                { wood: 30, crystals: 10 },
                { wood: 65, crystals: 20 }
            ]
        },
        forge: {
            id: 'forge',
            name: "Warrior's Forge",
            icon: '🔨',
            x: 960,
            y: 695,
            radius: 24,
            description: 'Camp armory that enhances striking power against forest creatures',
            maxLevel: 3,
            initialLevel: 0,
            costs: [
                { wood: 15, crystals: 2 },
                { wood: 35, crystals: 8 },
                { wood: 70, crystals: 16 }
            ]
        }
    };

    function createState(courseId) {
        var data = {
            wood: 0,
            crystals: 0,
            treesChopped: 0,
            enemiesDefeated: 0,
            upgradesPurchased: 0,
            levels: {
                axe: 1,
                strike: 1,
                backpack: 1,
                speed: 1
            },
            plots: {
                hearth: 1,
                sawmill: 0,
                archive: 0,
                forge: 0
            }
        };

        var listeners = [];
        var storageKey = courseId ? (STORAGE_PREFIX + courseId) : null;

        function notify() {
            for (var i = 0; i < listeners.length; i++) {
                try {
                    listeners[i](data);
                } catch (e) {
                    console.error('[ForgeState] Listener error:', e);
                }
            }
            save();
        }

        function save() {
            if (!storageKey || typeof window.sessionStorage === 'undefined') return;
            try {
                window.sessionStorage.setItem(storageKey, JSON.stringify(data));
            } catch (e) {
                /* sessionStorage quota or blocked */
            }
        }

        function load() {
            if (!storageKey || typeof window.sessionStorage === 'undefined') return;
            try {
                var raw = window.sessionStorage.getItem(storageKey);
                if (!raw) return;
                var parsed = JSON.parse(raw);
                if (parsed && typeof parsed === 'object') {
                    if (typeof parsed.wood === 'number') data.wood = parsed.wood;
                    if (typeof parsed.crystals === 'number') data.crystals = parsed.crystals;
                    if (typeof parsed.treesChopped === 'number') data.treesChopped = parsed.treesChopped;
                    if (typeof parsed.enemiesDefeated === 'number') data.enemiesDefeated = parsed.enemiesDefeated;
                    if (typeof parsed.upgradesPurchased === 'number') data.upgradesPurchased = parsed.upgradesPurchased;
                    if (parsed.levels && typeof parsed.levels === 'object') {
                        ['axe', 'strike', 'backpack', 'speed'].forEach(function (key) {
                            if (typeof parsed.levels[key] === 'number') {
                                data.levels[key] = Math.max(1, Math.min(5, parsed.levels[key]));
                            }
                        });
                    }
                    if (parsed.plots && typeof parsed.plots === 'object') {
                        ['hearth', 'sawmill', 'archive', 'forge'].forEach(function (key) {
                            if (typeof parsed.plots[key] === 'number') {
                                data.plots[key] = Math.max(0, Math.min(3, parsed.plots[key]));
                            }
                        });
                    }
                }
            } catch (e) {
                /* invalid JSON, start fresh */
            }
        }

        function clear() {
            if (!storageKey || typeof window.sessionStorage === 'undefined') return;
            try {
                window.sessionStorage.removeItem(storageKey);
            } catch (e) {}
        }

        function addWood(amount) {
            data.wood += Math.max(0, amount || 1);
            notify();
        }

        function addCrystals(amount) {
            data.crystals += Math.max(0, amount || 1);
            notify();
        }

        function recordTree() {
            data.treesChopped += 1;
            notify();
        }

        function recordEnemy() {
            data.enemiesDefeated += 1;
            notify();
        }

        function getUpgradeInfo(id) {
            var def = UPGRADE_DEFS[id];
            if (!def) return null;
            var currentLevel = data.levels[id] || 1;
            var isMax = currentLevel >= def.maxLevel;
            var nextCost = isMax ? null : def.costs[currentLevel - 1];

            return {
                id: def.id,
                name: def.name,
                icon: def.icon,
                description: def.description,
                currentLevel: currentLevel,
                maxLevel: def.maxLevel,
                isMax: isMax,
                cost: nextCost,
                canAfford: !isMax && nextCost && (data.wood >= nextCost.wood) && (data.crystals >= nextCost.crystals)
            };
        }

        function getAllUpgrades() {
            return Object.keys(UPGRADE_DEFS).map(function (key) {
                return getUpgradeInfo(key);
            });
        }

        function canAfford(id) {
            var info = getUpgradeInfo(id);
            return !!(info && info.canAfford);
        }

        function buyUpgrade(id) {
            var info = getUpgradeInfo(id);
            if (!info || info.isMax || !info.canAfford) return false;

            data.wood -= info.cost.wood;
            data.crystals -= info.cost.crystals;
            data.levels[id] = (data.levels[id] || 1) + 1;
            data.upgradesPurchased += 1;
            notify();
            return true;
        }

        function getPlotInfo(id) {
            var def = PLOT_DEFS[id];
            if (!def) return null;
            var currentLevel = (data.plots && data.plots[id] !== undefined) ? data.plots[id] : def.initialLevel;
            var isMax = currentLevel >= def.maxLevel;
            var nextCost = isMax ? null : def.costs[currentLevel];

            return {
                id: def.id,
                name: def.name,
                icon: def.icon,
                description: def.description,
                x: def.x,
                y: def.y,
                radius: def.radius,
                currentLevel: currentLevel,
                maxLevel: def.maxLevel,
                isMax: isMax,
                cost: nextCost,
                canAfford: !isMax && nextCost && (data.wood >= nextCost.wood) && (data.crystals >= nextCost.crystals)
            };
        }

        function getAllPlots() {
            return Object.keys(PLOT_DEFS).map(function (key) {
                return getPlotInfo(key);
            });
        }

        function canAffordPlot(id) {
            var info = getPlotInfo(id);
            return !!(info && info.canAfford);
        }

        function buyPlotUpgrade(id) {
            var info = getPlotInfo(id);
            if (!info || info.isMax || !info.canAfford) return false;

            data.wood -= info.cost.wood;
            data.crystals -= info.cost.crystals;
            data.plots[id] = (data.plots[id] !== undefined ? data.plots[id] : 0) + 1;
            data.upgradesPurchased += 1;
            notify();
            return true;
        }

        function getCampLevel() {
            var sum = 0;
            if (data.plots) {
                Object.keys(data.plots).forEach(function (k) {
                    sum += data.plots[k] || 0;
                });
            }
            return Math.max(1, sum);
        }

        function subscribe(fn) {
            if (typeof fn === 'function') {
                listeners.push(fn);
                fn(data);
            }
            return function unsubscribe() {
                var idx = listeners.indexOf(fn);
                if (idx !== -1) listeners.splice(idx, 1);
            };
        }

        function getSummary() {
            return {
                wood: data.wood,
                crystals: data.crystals,
                treesChopped: data.treesChopped,
                enemiesDefeated: data.enemiesDefeated,
                upgradesPurchased: data.upgradesPurchased,
                campLevel: getCampLevel(),
                levels: Object.assign({}, data.levels),
                plots: Object.assign({}, data.plots)
            };
        }

        // Initialize from session cache if available
        load();

        return {
            getData: function () { return data; },
            addWood: addWood,
            addCrystals: addCrystals,
            recordTree: recordTree,
            recordEnemy: recordEnemy,
            getUpgradeInfo: getUpgradeInfo,
            getAllUpgrades: getAllUpgrades,
            canAfford: canAfford,
            buyUpgrade: buyUpgrade,
            getPlotInfo: getPlotInfo,
            getAllPlots: getAllPlots,
            canAffordPlot: canAffordPlot,
            buyPlotUpgrade: buyPlotUpgrade,
            getCampLevel: getCampLevel,
            subscribe: subscribe,
            getSummary: getSummary,
            save: save,
            clear: clear
        };
    }

    window.CourseForgeState = {
        create: createState,
        UPGRADE_DEFS: UPGRADE_DEFS,
        PLOT_DEFS: PLOT_DEFS
    };
})(window);
