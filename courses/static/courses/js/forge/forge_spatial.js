/**
 * Course Forge Minigame — 2D Spatial Hash Grid.
 *
 * Provides O(1) spatial partitioning for collision queries, interaction radii,
 * and frustum culling across the 1920x1200 woodland territory.
 */
(function (window) {
    'use strict';

    var DEFAULT_CELL_SIZE = 128;

    function createSpatialGrid(cellSize) {
        var size = cellSize || DEFAULT_CELL_SIZE;
        var grid = {}; // 'col:row' -> array of items
        var itemCells = {}; // itemId -> array of cell keys

        function cellKey(col, row) {
            return col + ':' + row;
        }

        function getCellRange(minX, minY, maxX, maxY) {
            var minCol = Math.floor(minX / size);
            var maxCol = Math.floor(maxX / size);
            var minRow = Math.floor(minY / size);
            var maxRow = Math.floor(maxY / size);
            return {
                minCol: minCol,
                maxCol: maxCol,
                minRow: minRow,
                maxRow: maxRow
            };
        }

        function insert(item) {
            if (!item || item.x === undefined || item.y === undefined) return;
            var id = item.id;
            if (!id) {
                id = item.id = 'sp_' + Math.random().toString(36).substr(2, 9);
            }

            var r = item.radius || 12;
            var range = getCellRange(item.x - r, item.y - r, item.x + r, item.y + r);
            var keys = [];

            for (var c = range.minCol; c <= range.maxCol; c++) {
                for (var row = range.minRow; row <= range.maxRow; row++) {
                    var k = cellKey(c, row);
                    if (!grid[k]) {
                        grid[k] = [];
                    }
                    grid[k].push(item);
                    keys.push(k);
                }
            }

            itemCells[id] = keys;
        }

        function remove(item) {
            if (!item || !item.id) return;
            var id = item.id;
            var keys = itemCells[id];
            if (!keys) return;

            for (var i = 0; i < keys.length; i++) {
                var k = keys[i];
                var cell = grid[k];
                if (cell) {
                    for (var j = cell.length - 1; j >= 0; j--) {
                        if (cell[j].id === id) {
                            cell.splice(j, 1);
                            break;
                        }
                    }
                    if (cell.length === 0) {
                        delete grid[k];
                    }
                }
            }

            delete itemCells[id];
        }

        function update(item) {
            remove(item);
            insert(item);
        }

        function clear() {
            grid = {};
            itemCells = {};
        }

        /**
         * Returns all items within a circular radius of (cx, cy).
         * Optional filterTag filters by item.tag.
         */
        function queryRadius(cx, cy, radius, filterTag) {
            var r = radius || 0;
            var range = getCellRange(cx - r, cy - r, cx + r, cy + r);
            var seen = {};
            var results = [];
            var rSq = r * r;

            for (var c = range.minCol; c <= range.maxCol; c++) {
                for (var row = range.minRow; row <= range.maxRow; row++) {
                    var k = cellKey(c, row);
                    var cell = grid[k];
                    if (!cell) continue;

                    for (var i = 0; i < cell.length; i++) {
                        var item = cell[i];
                        if (seen[item.id]) continue;
                        seen[item.id] = true;

                        if (filterTag && item.tag !== filterTag) continue;

                        var itemR = item.radius || 0;
                        var totalR = r + itemR;
                        var dx = item.x - cx;
                        var dy = item.y - cy;
                        var distSq = dx * dx + dy * dy;

                        if (distSq <= totalR * totalR) {
                            results.push(item);
                        }
                    }
                }
            }

            return results;
        }

        /**
         * Returns all items intersecting an axis-aligned bounding box (e.g. camera view).
         */
        function queryRect(minX, minY, maxX, maxY, filterTag) {
            var range = getCellRange(minX, minY, maxX, maxY);
            var seen = {};
            var results = [];

            for (var c = range.minCol; c <= range.maxCol; c++) {
                for (var row = range.minRow; row <= range.maxRow; row++) {
                    var k = cellKey(c, row);
                    var cell = grid[k];
                    if (!cell) continue;

                    for (var i = 0; i < cell.length; i++) {
                        var item = cell[i];
                        if (seen[item.id]) continue;
                        seen[item.id] = true;

                        if (filterTag && item.tag !== filterTag) continue;

                        var itemR = item.radius || 0;
                        if (
                            item.x + itemR >= minX &&
                            item.x - itemR <= maxX &&
                            item.y + itemR >= minY &&
                            item.y - itemR <= maxY
                        ) {
                            results.push(item);
                        }
                    }
                }
            }

            return results;
        }

        return {
            insert: insert,
            remove: remove,
            update: update,
            clear: clear,
            queryRadius: queryRadius,
            queryRect: queryRect,
            getCellSize: function () { return size; }
        };
    }

    window.CourseForgeSpatial = {
        create: createSpatialGrid,
        DEFAULT_CELL_SIZE: DEFAULT_CELL_SIZE
    };
})(window);
