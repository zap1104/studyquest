/* Dungeon Quest — HUD and inventory.
 *
 * Draws hearts, key pieces and item buttons from the run state. Every icon is
 * resolved through the sprite manifest (via the renderer), so this file names
 * logical keys only — never a file path.
 *
 * Using an item is a server call; this file renders the result, it never
 * decides what an item does.
 */
(function (global) {
    'use strict';

    var DungeonQuest = global.DungeonQuest = global.DungeonQuest || {};

    function Inventory(options) {
        this.root = options.root;
        this.renderer = options.renderer;
        this.api = options.api;
        this.announce = options.announce;
        this.onUsed = options.onUsed;

        this.heartsEl = this.root.querySelector('[data-hearts]');
        this.hpTextEl = this.root.querySelector('[data-hp-text]');
        this.keysEl = this.root.querySelector('[data-keys]');
        this.keyNoteEl = this.root.querySelector('[data-key-note]');
        this.itemsEl = this.root.querySelector('[data-items]');
        this.toastEl = options.toast;

        this.busy = false;
        this.itemsEl.addEventListener('click', this.handleClick.bind(this));
    }

    Inventory.prototype.render = function (state) {
        this.state = state;
        this.renderHearts(state.hp);
        this.renderKeys(state.inventory);
        this.renderItems(state.inventory, state.hp);
    };

    Inventory.prototype.renderHearts = function (hp) {
        this.heartsEl.innerHTML = '';
        for (var index = 0; index < hp.max; index += 1) {
            var filled = index < hp.current;
            var img = document.createElement('img');
            img.src = this.renderer.urlFor('ui', filled ? 'heart_full' : 'heart_empty');
            img.alt = '';
            this.heartsEl.appendChild(img);
        }
        this.hpTextEl.textContent = hp.current + ' / ' + hp.max + ' HP';
    };

    Inventory.prototype.renderKeys = function (inventory) {
        this.keysEl.innerHTML = '';
        var required = inventory.key_pieces_required;

        if (inventory.has_final_key) {
            var key = document.createElement('img');
            key.src = this.renderer.urlFor('items', 'final_key');
            key.alt = 'The assembled final key';
            key.dataset.collected = 'true';
            this.keysEl.appendChild(key);
            this.keyNoteEl.textContent = 'The key is whole. The exit is open.';
            this.keyNoteEl.dataset.unlocked = 'true';
            return;
        }

        for (var index = 0; index < required; index += 1) {
            var piece = document.createElement('img');
            var collected = index < inventory.key_pieces;
            piece.src = this.renderer.urlFor('items', 'key_piece');
            piece.alt = collected ? 'Key piece collected' : 'Key piece still missing';
            piece.dataset.collected = collected ? 'true' : 'false';
            this.keysEl.appendChild(piece);
        }

        this.keyNoteEl.dataset.unlocked = 'false';
        this.keyNoteEl.textContent =
            inventory.key_pieces + ' of ' + required + ' key pieces — the exit stays sealed.';
    };

    Inventory.prototype.renderItems = function (inventory, hp) {
        var definitions = [
            {
                key: 'health_potion',
                label: 'Health potion',
                count: inventory.health_potions,
                usable: inventory.health_potions > 0 && hp.current < hp.max,
                hint: hp.current >= hp.max ? 'Already at full health' : null
            },
            {
                key: 'skip_potion',
                label: 'Skip potion',
                count: inventory.skip_potions,
                usable: inventory.skip_potions > 0 && !!this.state.battle,
                hint: this.state.battle ? null : 'Only usable in a battle'
            }
        ];

        this.itemsEl.innerHTML = '';

        definitions.forEach(function (definition) {
            var button = document.createElement('button');
            button.type = 'button';
            button.className = 'dungeon-item-button';
            button.dataset.item = definition.key;
            button.disabled = !definition.usable;

            var icon = document.createElement('img');
            icon.src = this.renderer.urlFor('items', definition.key);
            icon.alt = '';

            var label = document.createElement('span');
            label.textContent = definition.label;

            var count = document.createElement('span');
            count.className = 'dungeon-item-count';
            count.textContent = '×' + definition.count;

            button.appendChild(icon);
            button.appendChild(label);
            button.appendChild(count);

            var description = definition.label + ', ' + definition.count + ' held';
            if (definition.hint) { description += '. ' + definition.hint; }
            button.setAttribute('aria-label', description);
            button.title = definition.hint || definition.label;

            this.itemsEl.appendChild(button);
        }, this);
    };

    Inventory.prototype.handleClick = function (event) {
        var button = event.target.closest('[data-item]');
        if (!button || button.disabled || this.busy) { return; }

        var self = this;
        this.busy = true;

        this.api.useItem(button.dataset.item)
            .then(function (response) {
                if (self.onUsed) { self.onUsed(button.dataset.item, response.result); }
            })
            .catch(function (error) {
                self.announce(error.message || 'That item could not be used.');
            })
            .finally(function () { self.busy = false; });
    };

    /** Flash a short "you found…" note with the item's own sprite. */
    Inventory.prototype.showDrop = function (drops) {
        if (!this.toastEl || !drops) { return; }

        var parts = [];
        if (drops.key_pieces) {
            parts.push(drops.key_pieces === 1 ? 'a key piece' : drops.key_pieces + ' key pieces');
        }
        if (drops.item && drops.item !== 'nothing') {
            parts.push('a ' + drops.item.replace('_', ' '));
        }
        if (drops.item_wasted_at_cap) {
            parts.push('(your bag was full)');
        }
        if (!parts.length) { return; }

        var message = 'Found ' + parts.join(' and ') + '.';
        var iconKey = drops.item && drops.item !== 'nothing' ? drops.item : 'key_piece';

        this.toastEl.innerHTML = '';
        var icon = document.createElement('img');
        icon.src = this.renderer.urlFor('items', iconKey);
        icon.alt = '';
        var text = document.createElement('span');
        text.textContent = message;
        this.toastEl.appendChild(icon);
        this.toastEl.appendChild(text);
        this.toastEl.hidden = false;

        this.announce(message);

        global.clearTimeout(this.toastTimer);
        this.toastTimer = global.setTimeout(function () {
            this.toastEl.hidden = true;
        }.bind(this), 3200);
    };

    DungeonQuest.Inventory = Inventory;
}(window));
