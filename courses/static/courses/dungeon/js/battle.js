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
        this.bodyEl = this.root.querySelector('[data-battle-body]');
        this.promptEl = this.root.querySelector('[data-question-prompt]');
        this.fieldsEl = this.root.querySelector('[data-answer-fields]');
        this.legendEl = this.root.querySelector('[data-answer-legend]');
        this.formEl = this.root.querySelector('[data-battle-form]');
        this.submitEl = this.root.querySelector('[data-submit-answer]');
        this.continueEl = this.root.querySelector('[data-continue]');
        this.feedbackEl = this.root.querySelector('[data-feedback]');

        this.combatResultEl = document.querySelector('[data-combat-result]');
        this.combatResultBadgeEl = document.querySelector('[data-combat-result-badge]');
        this.combatResultLabelEl = document.querySelector('[data-combat-result-label]');
        this.combatResultAnswerEl = document.querySelector('[data-combat-result-answer]');
        this.combatResultExplanationEl = document.querySelector('[data-combat-result-explanation]');

        this.comboMeterEl = this.root.querySelector('[data-combo-meter]');
        this.comboPips = this.root.querySelectorAll('[data-combo-pip]');
        this.comboTextEl = this.root.querySelector('[data-combo-text]');

        this.idleEl = this.root.parentElement
            ? this.root.parentElement.querySelector('[data-battle-idle]')
            : document.querySelector('[data-battle-idle]');

        this.battle = null;
        this.busy = false;

        this.formEl.addEventListener('submit', this.submit.bind(this));
        this.continueEl.addEventListener('click', this.continueAfterFeedback.bind(this));
        this.root.addEventListener('keydown', this.handleKeydown.bind(this));
    }

    Battle.prototype.isOpen = function () {
        return !this.root.hidden;
    };

    Battle.prototype.renderCombatResult = function (options) {
        if (!this.combatResultEl) return;
        options = options || {};
        var outcome = options.outcome || 'roaming';
        var badge = options.badge || 'Combat Log';
        var label = options.label || '';
        var answer = options.answer || '';
        var explanation = options.explanation || '';

        this.combatResultEl.dataset.outcome = outcome;

        if (this.combatResultBadgeEl) {
            this.combatResultBadgeEl.textContent = badge;
        }
        if (this.combatResultLabelEl) {
            this.combatResultLabelEl.textContent = label;
        }
        if (this.combatResultAnswerEl) {
            if (answer) {
                this.combatResultAnswerEl.textContent = answer;
                this.combatResultAnswerEl.hidden = false;
            } else {
                this.combatResultAnswerEl.textContent = '';
                this.combatResultAnswerEl.hidden = true;
            }
        }
        if (this.combatResultExplanationEl) {
            this.combatResultExplanationEl.textContent = explanation;
        }
    };

    Battle.prototype.open = function (battle) {
        this.battle = battle;
        this.root.hidden = false;
        if (this.idleEl) { this.idleEl.hidden = true; }
        this.render();

        this.renderCombatResult({
            outcome: 'turn',
            badge: 'Your Turn',
            label: 'Question ' + battle.question_number + ' of ' + battle.questions_total,
            answer: '',
            explanation: 'Choose an answer on the right, then attack.'
        });

        // Bring the battle sheet into view on mobile (< 768px). On desktop / tablet,
        // it is already docked side-by-side or stacked without page scrolling.
        if (global.innerWidth < 768) {
            this.root.scrollIntoView({
                block: 'start',
                behavior: this.reducedMotion ? 'auto' : 'smooth'
            });
        }

        var heading = this.root.querySelector('[data-battle-title]');
        if (heading) { heading.focus({ preventScroll: true }); }
    };

    Battle.prototype.close = function () {
        this.battle = null;
        this.root.hidden = true;
        if (this.feedbackEl) { this.feedbackEl.hidden = true; }
        if (this.idleEl) { this.idleEl.hidden = false; }
    };

    Battle.prototype.render = function () {
        var battle = this.battle;
        if (!battle) { return; }

        var enemy = battle.enemy;
        this.titleEl.textContent = 'Enemy ' + (enemy.index + 1);
        this.counterEl.textContent = 'Question ' + battle.question_number + ' of ' + battle.questions_total;
        this.hpTextEl.textContent = enemy.hp + ' / ' + enemy.max_hp + ' HP';
        this.barEl.style.width = (enemy.max_hp ? (enemy.hp / enemy.max_hp) * 100 : 0) + '%';

        this.renderCombo(battle.combo);

        if (this.feedbackEl) { this.feedbackEl.hidden = true; }
        this.submitEl.hidden = false;
        this.continueEl.hidden = true;

        this.renderQuestion(battle.question);
    };

    Battle.prototype.renderCombo = function (combo, combatEvent) {
        if (!this.comboMeterEl) { return; }
        combo = combo || { count: 0, current: 0, threshold: 3, remaining: 3, is_power_strike: false };
        var count = typeof combo.count !== 'undefined' ? combo.count : (combo.current || 0);
        var threshold = combo.threshold || 3;
        var isPowerStrike = (combatEvent && combatEvent.code === 'power_strike') || !!combo.is_power_strike;

        this.comboMeterEl.classList.toggle('has-combo', count > 0 || isPowerStrike);
        this.comboMeterEl.classList.toggle('is-ready', count >= threshold - 1 || isPowerStrike);

        if (this.comboPips && this.comboPips.length) {
            var activePips = isPowerStrike ? threshold : count;
            this.comboPips.forEach(function (pip, index) {
                var pipIndex = index + 1;
                pip.classList.toggle('active', pipIndex <= activePips);
            });
        }

        if (this.comboTextEl) {
            if (isPowerStrike) {
                this.comboTextEl.textContent = 'POWER STRIKE (2 DMG)';
            } else if (count === 1) {
                this.comboTextEl.textContent = 'Combo: 1/3 (2 to Strike)';
            } else if (count === 2) {
                this.comboTextEl.textContent = 'Combo: 2/3 (1 to Strike!)';
            } else {
                this.comboTextEl.textContent = 'Combo: 0/3';
            }
        }

        if (isPowerStrike && !this.reducedMotion) {
            this.comboMeterEl.classList.remove('combo-strike');
            void this.comboMeterEl.offsetWidth;
            this.comboMeterEl.classList.add('combo-strike');
        }
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
        if (event && event.preventDefault) event.preventDefault();
        if (this.busy || !this.battle || !this.battle.question) { return; }

        if (!this.feedbackEl.hidden) {
            this.continueAfterFeedback();
            return;
        }

        // Prevent premature submit if user pressed Enter inside an enumeration field that isn't the last one
        var activeEl = document.activeElement;
        if (this.battle.question.type === 'enumeration' && activeEl && activeEl.classList && activeEl.classList.contains('dungeon-enum-answer')) {
            var inputs = Array.prototype.slice.call(
                this.fieldsEl.querySelectorAll('.dungeon-enum-answer:not(:disabled)')
            );
            var currentIndex = inputs.indexOf(activeEl);
            if (currentIndex !== -1 && currentIndex < inputs.length - 1) {
                var nextInput = inputs[currentIndex + 1];
                if (nextInput) {
                    nextInput.focus();
                    if (typeof nextInput.select === 'function') nextInput.select();
                }
                return;
            }
        }

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

        if (result.combo) {
            this.renderCombo(result.combo, result.combat_event);
        }

        if (result.damage_to_player > 0 && !this.reducedMotion) {
            var panel = this.root;
            panel.classList.remove('dungeon-hit');
            void panel.offsetWidth;
            panel.classList.add('dungeon-hit');
        }

        if (result.outcome === 'partial' && !this.reducedMotion) {
            var panel = this.root;
            panel.classList.remove('dungeon-glance');
            void panel.offsetWidth;
            panel.classList.add('dungeon-glance');
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
        var combatEvent = result.combat_event || {};
        var isPowerStrike = (combatEvent.code === 'power_strike') || (result.combo && result.combo.is_power_strike);

        var badge = 'Hit';
        var label = '';
        var answer = '';
        var explanation = '';

        if (isPowerStrike) {
            badge = 'Power Strike!';
            label = 'POWER STRIKE · 2 DAMAGE';
        } else if (result.outcome === 'correct') {
            badge = 'Direct Hit';
            label = 'DIRECT HIT · 1 DAMAGE';
        } else if (result.outcome === 'incorrect') {
            badge = 'Damage Taken';
            label = 'YOU TAKE A HIT · ' + (result.damage_to_player > 0 ? result.damage_to_player + ' HP LOST' : '1 HP LOST');
        } else if (result.outcome === 'partial') {
            badge = 'Glancing Blow';
            label = 'GLANCING BLOW · PARTIAL CREDIT';
        } else if (result.outcome === 'skipped') {
            badge = 'Skipped';
            label = 'SLIPPED PAST THE ENEMY';
        } else {
            badge = 'Combat';
            label = 'TURN GRADED';
        }

        if (result.outcome === 'incorrect' || result.outcome === 'partial') {
            if (feedback.correct_choice_text) {
                answer = 'Correct answer: ' + feedback.correct_choice_text;
            } else if (feedback.canonical_answer) {
                answer = 'Correct answer: ' + feedback.canonical_answer;
            }
            if (feedback.missing_items && feedback.missing_items.length) {
                var missed = 'Missed: ' + feedback.missing_items.join(', ');
                answer = answer ? (answer + ' | ' + missed) : missed;
            }
        }

        if (feedback.explanation) {
            explanation = feedback.explanation;
        } else if (result.outcome === 'correct') {
            explanation = isPowerStrike
                ? 'Combo threshold reached! Massive damage dealt.'
                : 'Well done! The enemy takes damage.';
        } else if (result.outcome === 'partial') {
            explanation = 'Partial credit awarded — nobody takes damage.';
        } else if (result.outcome === 'skipped') {
            explanation = 'You slipped past the question.';
        } else {
            explanation = 'Review the correct answer before proceeding.';
        }

        this.renderCombatResult({
            outcome: isPowerStrike ? 'power_strike' : result.outcome,
            badge: badge,
            label: label,
            answer: answer,
            explanation: explanation
        });

        if (this.feedbackEl) {
            this.feedbackEl.dataset.outcome = result.outcome;
            this.feedbackEl.innerHTML = '';
            var headlineEl = document.createElement('p');
            headlineEl.className = 'dungeon-feedback-headline';
            headlineEl.textContent = HEADLINES[result.outcome] || 'Result';
            this.feedbackEl.appendChild(headlineEl);
            if (explanation) {
                var expEl = document.createElement('p');
                expEl.textContent = explanation;
                this.feedbackEl.appendChild(expEl);
            }
            this.feedbackEl.hidden = false;
        }

        var announceText = badge + ': ' + label + '. ' + (answer ? answer + '. ' : '') + explanation;
        this.announce(announceText);

        if (global.innerWidth < 768 && this.combatResultEl) {
            this.combatResultEl.scrollIntoView({
                behavior: this.reducedMotion ? 'auto' : 'smooth',
                block: 'nearest'
            });
        }
    };

    Battle.prototype.continueAfterFeedback = function () {
        var result = this.pendingResult;
        this.pendingResult = null;
        if (this.feedbackEl) { this.feedbackEl.hidden = true; }

        if (!result) { return; }

        if (result.battle) {
            this.battle = result.battle;
            this.render();
            this.renderCombatResult({
                outcome: 'turn',
                badge: 'Your Turn',
                label: 'Question ' + this.battle.question_number + ' of ' + this.battle.questions_total,
                answer: '',
                explanation: 'Choose an answer on the right, then attack.'
            });
        } else {
            this.close();
        }

        if (this.onResolved) { this.onResolved(result); }
    };

    Battle.prototype.handleKeydown = function (event) {
        if (event.defaultPrevented) return;
        if (event.isComposing) return;
        if (event.repeat) return;
        if (event.ctrlKey || event.metaKey || event.altKey) return;

        var activeEl = document.activeElement;
        var isEnter = event.key === 'Enter';
        var isSpace = event.key === ' ' || event.code === 'Space';

        if (!isEnter && !isSpace) return;

        var isButtonLike = activeEl && activeEl.matches && activeEl.matches('button, a[href], [role="button"]');
        if (isButtonLike) return;

        var isInput = activeEl && activeEl.matches && activeEl.matches('input, select, textarea, [contenteditable="true"]');
        var isTextarea = activeEl && activeEl.matches && activeEl.matches('textarea');

        var isGraded = this.continueEl ? !this.continueEl.hidden : false;
        var question = this.battle ? this.battle.question : null;

        if (!isGraded) {
            if (!question) return;

            var isChoiceQuestion = (
                question.type === 'multiple_choice' ||
                question.type === 'true_false'
            );

            if (isSpace) {
                if (isChoiceQuestion && !isInput && !this.submitEl.disabled && !this.busy) {
                    event.preventDefault();
                    event.stopPropagation();
                    this.submit(event);
                }
                return;
            }

            if (isEnter && question.type === 'enumeration') {
                if (activeEl && activeEl.classList && activeEl.classList.contains('dungeon-enum-answer')) {
                    event.preventDefault();
                    event.stopPropagation();

                    var inputs = Array.prototype.slice.call(
                        this.fieldsEl.querySelectorAll('.dungeon-enum-answer:not(:disabled)')
                    );
                    var currentIndex = inputs.indexOf(activeEl);
                    var nextInput = inputs[currentIndex + 1];

                    if (nextInput) {
                        nextInput.focus();
                        if (typeof nextInput.select === 'function') nextInput.select();
                        return;
                    }

                    if (currentIndex === inputs.length - 1 && !this.submitEl.disabled && !this.busy) {
                        activeEl.blur();
                        this.submit(event);
                    }
                    return;
                }
                return;
            }

            if (isEnter && isTextarea) {
                return;
            }

            if (isEnter && !this.submitEl.disabled && !this.busy) {
                event.preventDefault();
                event.stopPropagation();
                if (isInput && typeof activeEl.blur === 'function') {
                    activeEl.blur();
                }
                this.submit(event);
            }
            return;
        }

        if ((isEnter || isSpace) && !isInput) {
            event.preventDefault();
            event.stopPropagation();
            this.continueAfterFeedback();
        }
    };

    DungeonQuest.Battle = Battle;
}(window));
