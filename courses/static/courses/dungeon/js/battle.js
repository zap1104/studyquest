/* Dungeon Quest — battle panel.
 *
 * The whole fight happens in ordinary DOM: a heading, a prompt, real form
 * controls and a live region. Nothing here needs the canvas, so the battle is
 * fully playable by keyboard and screen reader while the board is decorative.
 *
 * The client never knows which answer is right. It posts what the player chose
 * and renders whatever the server grades it as.
 */
(function (global) {
    'use strict';

    var DungeonQuest = global.DungeonQuest = global.DungeonQuest || {};

    var HEADLINES = {
        correct: 'Direct hit!',
        incorrect: 'You take a hit!',
        partial: 'Glancing blow — nobody takes damage.',
        skipped: 'You slip past the question.'
    };

    function Battle(options) {
        this.root = options.root;
        this.api = options.api;
        this.announce = options.announce;
        this.onResolved = options.onResolved;
        this.onUpdated = options.onUpdated;
        this.reducedMotion = !!options.reducedMotion;

        this.titleEl = this.root.querySelector('[data-battle-title]');
        this.counterEl = this.root.querySelector('[data-battle-counter]');
        this.barEl = this.root.querySelector('[data-enemy-bar]');
        this.hpTextEl = this.root.querySelector('[data-enemy-hp-text]');
        this.promptEl = this.root.querySelector('[data-question-prompt]');
        this.fieldsEl = this.root.querySelector('[data-answer-fields]');
        this.legendEl = this.root.querySelector('[data-answer-legend]');
        this.formEl = this.root.querySelector('[data-battle-form]');
        this.submitEl = this.root.querySelector('[data-submit-answer]');
        this.continueEl = this.root.querySelector('[data-continue]');
        this.feedbackEl = this.root.querySelector('[data-feedback]');

        this.battle = null;
        this.busy = false;

        this.formEl.addEventListener('submit', this.submit.bind(this));
        this.continueEl.addEventListener('click', this.continueAfterFeedback.bind(this));
    }

    Battle.prototype.isOpen = function () {
        return !this.root.hidden;
    };

    Battle.prototype.open = function (battle) {
        this.battle = battle;
        this.root.hidden = false;
        this.render();

        // Bring the whole panel into view, then take focus without a second
        // scroll - focusing alone would leave the panel half off-screen.
        this.root.scrollIntoView({
            block: 'start',
            behavior: this.reducedMotion ? 'auto' : 'smooth'
        });

        var heading = this.root.querySelector('[data-battle-title]');
        if (heading) { heading.focus({ preventScroll: true }); }
    };

    Battle.prototype.close = function () {
        this.battle = null;
        this.root.hidden = true;
        this.feedbackEl.hidden = true;
    };

    Battle.prototype.render = function () {
        var battle = this.battle;
        if (!battle) { return; }

        var enemy = battle.enemy;
        this.titleEl.textContent = 'Enemy ' + (enemy.index + 1);
        this.counterEl.textContent = 'Question ' + battle.question_number + ' of ' + battle.questions_total;
        this.hpTextEl.textContent = enemy.hp + ' / ' + enemy.max_hp + ' HP';
        this.barEl.style.width = (enemy.max_hp ? (enemy.hp / enemy.max_hp) * 100 : 0) + '%';

        this.feedbackEl.hidden = true;
        this.submitEl.hidden = false;
        this.continueEl.hidden = true;

        this.renderQuestion(battle.question);
    };

    Battle.prototype.renderQuestion = function (question) {
        this.fieldsEl.querySelectorAll('[data-generated]').forEach(function (node) {
            node.remove();
        });

        if (!question) {
            this.promptEl.textContent = 'The enemy has nothing left to ask.';
            return;
        }

        this.promptEl.textContent = question.prompt;

        if (question.type === 'multiple_choice' || question.type === 'true_false') {
            this.legendEl.textContent = 'Choose one answer';
            this.renderChoices(question);
        } else if (question.type === 'enumeration') {
            this.legendEl.textContent = question.order_matters
                ? 'List every item, in order'
                : 'List every item, in any order';
            this.renderEnumeration(question);
        } else {
            this.legendEl.textContent = 'Type your answer';
            this.renderTextAnswer(question);
        }

        var first = this.fieldsEl.querySelector('input');
        if (first) { first.focus(); }
    };

    Battle.prototype.renderChoices = function (question) {
        (question.choices || []).forEach(function (choice, index) {
            var label = document.createElement('label');
            label.className = 'dungeon-choice';
            label.dataset.generated = 'true';

            var input = document.createElement('input');
            input.type = 'radio';
            input.name = 'dungeon-choice';
            input.value = choice.id;
            input.required = index === 0;

            var text = document.createElement('span');
            text.textContent = choice.text;

            label.appendChild(input);
            label.appendChild(text);
            this.fieldsEl.appendChild(label);
        }, this);
    };

    Battle.prototype.renderTextAnswer = function (question) {
        var input = document.createElement('input');
        input.type = 'text';
        input.className = 'dungeon-text-answer';
        input.dataset.generated = 'true';
        input.dataset.role = 'text-answer';
        input.autocomplete = 'off';
        input.setAttribute('aria-label', 'Your answer');
        this.fieldsEl.appendChild(input);
    };

    Battle.prototype.renderEnumeration = function (question) {
        var count = question.expected_item_count || 1;
        for (var index = 0; index < count; index += 1) {
            var input = document.createElement('input');
            input.type = 'text';
            input.className = 'dungeon-enum-answer';
            input.dataset.generated = 'true';
            input.dataset.role = 'enum-answer';
            input.autocomplete = 'off';
            input.setAttribute('aria-label', 'Item ' + (index + 1) + ' of ' + count);
            input.placeholder = 'Item ' + (index + 1);
            this.fieldsEl.appendChild(input);
        }
    };

    Battle.prototype.collectAnswer = function () {
        var question = this.battle.question;
        if (!question) { return null; }

        if (question.type === 'multiple_choice' || question.type === 'true_false') {
            var checked = this.fieldsEl.querySelector('input[name="dungeon-choice"]:checked');
            return checked ? { choice_id: parseInt(checked.value, 10) } : null;
        }

        if (question.type === 'enumeration') {
            var items = [];
            this.fieldsEl.querySelectorAll('[data-role="enum-answer"]').forEach(function (input) {
                items.push(input.value.trim());
            });
            return { items: items };
        }

        var text = this.fieldsEl.querySelector('[data-role="text-answer"]');
        return { text: text ? text.value.trim() : '' };
    };

    Battle.prototype.submit = function (event) {
        event.preventDefault();
        if (this.busy || !this.battle || !this.battle.question) { return; }

        var answer = this.collectAnswer();
        if (answer === null) {
            this.announce('Choose an answer first.');
            return;
        }

        var self = this;
        this.busy = true;
        this.submitEl.disabled = true;

        this.api.answer(this.battle.question.id, answer)
            .then(function (response) {
                self.handleResult(response.result);
            })
            .catch(function (error) {
                self.announce(error.message || 'That answer could not be submitted.');
            })
            .finally(function () {
                self.busy = false;
                self.submitEl.disabled = false;
            });
    };

    /** Render a graded turn. Also used for skip-potion results, which the
     *  server resolves through the same turn pipeline. */
    Battle.prototype.handleResult = function (result) {
        this.pendingResult = result;

        // Sync the HUD now: hearts must drop with the blow, not when the player
        // gets round to pressing Continue.
        if (this.onUpdated) { this.onUpdated(result); }

        this.showFeedback(result);

        if (result.enemy) {
            this.hpTextEl.textContent = result.enemy.hp + ' / ' + result.enemy.max_hp + ' HP';
            this.barEl.style.width =
                (result.enemy.max_hp ? (result.enemy.hp / result.enemy.max_hp) * 100 : 0) + '%';
        }

        if (result.damage_to_player > 0 && !this.reducedMotion) {
            var panel = this.root;
            panel.classList.remove('dungeon-hit');
            void panel.offsetWidth;
            panel.classList.add('dungeon-hit');
        }

        this.submitEl.hidden = true;
        this.continueEl.hidden = false;
        this.continueEl.textContent = result.enemy_defeated || result.run_over
            ? 'Continue'
            : 'Next question';
        this.continueEl.focus();
    };

    Battle.prototype.showFeedback = function (result) {
        var feedback = result.feedback || {};
        var lines = [];

        if (result.damage_to_enemy > 0) {
            lines.push('The enemy takes ' + result.damage_to_enemy + ' damage.');
        }
        if (result.damage_to_player > 0) {
            lines.push('You lose ' + result.damage_to_player + ' HP.');
        }
        if (feedback.correct_choice_text && result.outcome === 'incorrect') {
            lines.push('Correct answer: ' + feedback.correct_choice_text);
        }
        if (feedback.canonical_answer && result.outcome === 'incorrect') {
            lines.push('Correct answer: ' + feedback.canonical_answer);
        }
        if (feedback.missing_items && feedback.missing_items.length) {
            lines.push('Missed: ' + feedback.missing_items.join(', '));
        }
        if (feedback.explanation) {
            lines.push(feedback.explanation);
        }

        var headline = HEADLINES[result.outcome] || 'Result';
        this.feedbackEl.dataset.outcome = result.outcome;
        this.feedbackEl.innerHTML = '';

        var headlineEl = document.createElement('p');
        headlineEl.className = 'dungeon-feedback-headline';
        headlineEl.textContent = headline;
        this.feedbackEl.appendChild(headlineEl);

        lines.forEach(function (line) {
            var p = document.createElement('p');
            p.style.margin = '0';
            p.textContent = line;
            this.feedbackEl.appendChild(p);
        }, this);

        this.feedbackEl.hidden = false;
        this.announce(headline + ' ' + lines.join(' '));
    };

    Battle.prototype.continueAfterFeedback = function () {
        var result = this.pendingResult;
        this.pendingResult = null;
        this.feedbackEl.hidden = true;

        if (!result) { return; }

        if (result.battle) {
            this.battle = result.battle;
            this.render();
        } else {
            this.close();
        }

        if (this.onResolved) { this.onResolved(result); }
    };

    DungeonQuest.Battle = Battle;
}(window));
