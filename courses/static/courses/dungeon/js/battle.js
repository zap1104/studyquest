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

        this.messageBoxEl = document.querySelector('[data-combat-message]');
        this.messageTitleEl = document.querySelector('[data-message-title]');
        this.messageMetaEl = document.querySelector('[data-message-meta]');
        this.messageComboEl = document.querySelector('[data-message-combo]');
        this.messageComboDots = this.messageComboEl ? this.messageComboEl.querySelectorAll('.combo-dots i') : [];
        this.messageComboTextEl = document.querySelector('[data-message-combo-text]');
        this.messagePrimaryEl = document.querySelector('[data-message-primary]');
        this.messageAnswerEl = document.querySelector('[data-message-answer]');
        this.messageStatusEl = document.querySelector('[data-message-status]');
        this.messageToggleExplanationBtn = document.querySelector('[data-toggle-explanation]');
        this.messageExplanationEl = document.querySelector('[data-message-explanation]');
        this.messageBodyEl = document.querySelector('[data-dungeon-message-body]');
        this.scrollControlsEl = document.querySelector('[data-message-scroll-controls]');
        this.scrollUpBtn = document.querySelector('[data-message-scroll-up]');
        this.scrollDownBtn = document.querySelector('[data-message-scroll-down]');

        this.idleEl = this.root.parentElement
            ? this.root.parentElement.querySelector('[data-battle-idle]')
            : document.querySelector('[data-battle-idle]');

        this.battle = null;
        this.busy = false;

        this.formEl.addEventListener('submit', this.submit.bind(this));
        this.continueEl.addEventListener('click', this.continueAfterFeedback.bind(this));
        this.root.addEventListener('keydown', this.handleKeydown.bind(this));

        if (this.messageToggleExplanationBtn) {
            this.messageToggleExplanationBtn.addEventListener('click', this.toggleExplanation.bind(this));
        }
        if (this.scrollUpBtn) {
            this.scrollUpBtn.addEventListener('click', this.scrollMessageUp.bind(this));
        }
        if (this.scrollDownBtn) {
            this.scrollDownBtn.addEventListener('click', this.scrollMessageDown.bind(this));
        }
        if (this.messageBodyEl) {
            this.messageBodyEl.addEventListener('scroll', this.updateMessageOverflowState.bind(this));
            if (typeof ResizeObserver !== 'undefined') {
                this.messageResizeObserver = new ResizeObserver(this.refreshMessageOverflow.bind(this));
                this.messageResizeObserver.observe(this.messageBodyEl);
            }
        }
        global.addEventListener('resize', this.refreshMessageOverflow.bind(this));
    }

    Battle.prototype.isOpen = function () {
        return !this.root.hidden;
    };

    Battle.prototype.renderCombatMessage = function (options) {
        if (!this.messageBoxEl) return;
        options = options || {};
        var outcome = options.outcome || '';
        var state = options.state;
        if (!state) {
            if (outcome === 'turn' || outcome === 'awaiting-answer') {
                state = 'awaiting-answer';
            } else if (outcome === 'correct' || outcome === 'power_strike' || outcome === 'incorrect' || outcome === 'partial' || outcome === 'skipped') {
                state = 'graded';
            } else if (outcome === 'victory' || outcome === 'unlocked' || outcome === 'complete' || outcome === 'failed' || outcome === 'item') {
                state = 'event';
            } else {
                state = 'roaming';
            }
        }
        var title = options.title || options.badge || '';
        var meta = options.meta || '';
        var combo = options.combo || null;
        var primary = options.primary || options.label || '';
        var answer = options.answer || '';
        var explanation = options.explanation || '';
        var showCaret = typeof options.showCaret !== 'undefined' ? !!options.showCaret : (state === 'graded');

        this.messageBoxEl.dataset.state = state;
        this.messageBoxEl.dataset.outcome = outcome;

        if (this.messageTitleEl) {
            this.messageTitleEl.textContent = title;
        }

        if (combo && combo.visible) {
            if (this.messageMetaEl) { this.messageMetaEl.hidden = true; }
            if (this.messageComboEl) {
                this.messageComboEl.hidden = false;
                var count = typeof combo.count !== 'undefined' ? combo.count : 0;
                var threshold = combo.threshold || 3;
                this.messageComboEl.dataset.count = String(count);
                if (this.messageComboTextEl) {
                    this.messageComboTextEl.textContent = combo.text || ('Combo ' + count + '/' + threshold);
                }
            }
        } else if (meta) {
            if (this.messageComboEl) { this.messageComboEl.hidden = true; }
            if (this.messageMetaEl) {
                this.messageMetaEl.textContent = meta;
                this.messageMetaEl.dataset.outcome = outcome;
                this.messageMetaEl.hidden = false;
            }
        } else {
            if (this.messageComboEl) { this.messageComboEl.hidden = true; }
            if (this.messageMetaEl) { this.messageMetaEl.hidden = true; }
        }

        if (this.messagePrimaryEl) {
            this.messagePrimaryEl.textContent = primary;
        }

        if (this.messageAnswerEl) {
            if (answer) {
                this.messageAnswerEl.textContent = answer;
                this.messageAnswerEl.hidden = false;
            } else {
                this.messageAnswerEl.textContent = '';
                this.messageAnswerEl.hidden = true;
            }
        }

        if (this.messageStatusEl) {
            if (options.status) {
                this.messageStatusEl.textContent = options.status;
                this.messageStatusEl.hidden = false;
            } else {
                this.messageStatusEl.textContent = '';
                this.messageStatusEl.hidden = true;
            }
        }

        if (this.messageToggleExplanationBtn && this.messageExplanationEl) {
            if (state === 'graded' && explanation) {
                this.messageToggleExplanationBtn.hidden = false;
                this.messageToggleExplanationBtn.setAttribute('aria-expanded', 'false');
                this.messageToggleExplanationBtn.innerHTML = 'View Explanation <span aria-hidden="true">▾</span>';
                this.messageExplanationEl.hidden = true;
                this.messageExplanationEl.textContent = explanation;
            } else if (state !== 'graded' && explanation) {
                this.messageToggleExplanationBtn.hidden = true;
                this.messageExplanationEl.hidden = false;
                this.messageExplanationEl.textContent = explanation;
            } else {
                this.messageToggleExplanationBtn.hidden = true;
                this.messageExplanationEl.hidden = true;
                this.messageExplanationEl.textContent = '';
            }
        } else if (this.messageExplanationEl) {
            if (explanation) {
                this.messageExplanationEl.textContent = explanation;
                this.messageExplanationEl.hidden = false;
            } else {
                this.messageExplanationEl.textContent = '';
                this.messageExplanationEl.hidden = true;
            }
        }

        if (this.messageBodyEl) {
            this.messageBodyEl.scrollTop = 0;
        }

        this.refreshMessageOverflow();
    };

    Battle.prototype.refreshMessageOverflow = function () {
        var self = this;
        if (global.requestAnimationFrame) {
            global.requestAnimationFrame(function () {
                self.updateMessageOverflowState();
            });
        } else {
            this.updateMessageOverflowState();
        }
    };

    Battle.prototype.updateMessageOverflowState = function () {
        var body = this.messageBodyEl;
        var box = this.messageBoxEl;
        if (!body || !box) { return; }

        var hasOverflow = body.scrollHeight > (body.clientHeight + 1);
        var isAtTop = body.scrollTop <= 4;
        var isAtBottom = (body.scrollTop + body.clientHeight) >= (body.scrollHeight - 4);

        box.classList.toggle('has-overflow', hasOverflow);
        box.classList.toggle('is-at-top', isAtTop);
        box.classList.toggle('is-at-bottom', isAtBottom);

        body.tabIndex = hasOverflow ? 0 : -1;

        if (this.scrollControlsEl) {
            this.scrollControlsEl.hidden = !hasOverflow;
        }
        if (this.scrollUpBtn) {
            this.scrollUpBtn.hidden = !hasOverflow;
            this.scrollUpBtn.disabled = isAtTop;
            this.scrollUpBtn.setAttribute('aria-disabled', String(isAtTop));
        }
        if (this.scrollDownBtn) {
            this.scrollDownBtn.hidden = !hasOverflow;
            this.scrollDownBtn.disabled = isAtBottom;
            this.scrollDownBtn.setAttribute('aria-disabled', String(isAtBottom));
        }
    };

    Battle.prototype.scrollMessageUp = function () {
        if (!this.messageBodyEl) return;
        var delta = Math.max(72, this.messageBodyEl.clientHeight * 0.7);
        this.messageBodyEl.scrollBy({
            top: -delta,
            behavior: this.reducedMotion ? 'auto' : 'smooth'
        });
    };

    Battle.prototype.scrollMessageDown = function () {
        if (!this.messageBodyEl) return;
        var delta = Math.max(72, this.messageBodyEl.clientHeight * 0.7);
        this.messageBodyEl.scrollBy({
            top: delta,
            behavior: this.reducedMotion ? 'auto' : 'smooth'
        });
    };

    Battle.prototype.toggleExplanation = function () {
        if (!this.messageToggleExplanationBtn || !this.messageExplanationEl) return;
        var isExpanded = this.messageToggleExplanationBtn.getAttribute('aria-expanded') === 'true';
        var nextExpanded = !isExpanded;

        this.messageToggleExplanationBtn.setAttribute('aria-expanded', String(nextExpanded));
        this.messageExplanationEl.hidden = !nextExpanded;
        this.messageToggleExplanationBtn.innerHTML = nextExpanded
            ? 'Hide Explanation <span aria-hidden="true">▴</span>'
            : 'View Explanation <span aria-hidden="true">▾</span>';

        this.refreshMessageOverflow();
    };

    Battle.prototype.renderCombatResult = function (options) {
        return this.renderCombatMessage(options);
    };

    Battle.prototype.setWorkspaceState = function (state) {
        if (!this.columnsEl) {
            this.columnsEl = document.querySelector('[data-run-columns]');
        }
        if (this.columnsEl) {
            this.columnsEl.dataset.state = state;
        }
    };

    Battle.prototype.open = function (battle) {
        this.battle = battle;
        this.setWorkspaceState('combat');
        this.root.hidden = false;
        if (this.idleEl) { this.idleEl.hidden = true; }
        this.render();

        var count = (battle.combo && typeof battle.combo.count !== 'undefined') ? battle.combo.count : 0;
        var threshold = (battle.combo && battle.combo.threshold) || 3;
        var comboHint = 'Three consecutive correct answers trigger a Power Strike.';
        if (count === 1) {
            comboHint = 'Two more correct answers will trigger a Power Strike.';
        } else if (count === 2) {
            comboHint = 'One more correct answer will trigger a Power Strike!';
        }

        this.renderCombatMessage({
            state: 'awaiting-answer',
            outcome: 'turn',
            title: 'YOUR TURN',
            combo: {
                visible: true,
                count: count,
                threshold: threshold,
                text: 'Combo ' + count + '/' + threshold
            },
            primary: 'Choose an answer on the right, then attack.',
            status: comboHint,
            answer: '',
            explanation: '',
            showCaret: false
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
        this.setWorkspaceState('roaming');
        this.root.hidden = true;
        if (this.feedbackEl) { this.feedbackEl.hidden = true; }
        if (this.idleEl) { this.idleEl.hidden = false; }
        if (this.scrollControlsEl) { this.scrollControlsEl.hidden = true; }
        if (this.scrollUpBtn) { this.scrollUpBtn.hidden = true; }
        if (this.scrollDownBtn) { this.scrollDownBtn.hidden = true; }
    };

    Battle.prototype.render = function () {
        var battle = this.battle;
        if (!battle) { return; }

        var enemy = battle.enemy;
        this.titleEl.textContent = 'Enemy ' + (enemy.index + 1);
        this.counterEl.textContent = 'Question ' + battle.question_number + ' / ' + battle.questions_total;
        this.hpTextEl.textContent = enemy.hp + ' / ' + enemy.max_hp + ' HP';
        this.barEl.style.width = (enemy.max_hp ? (enemy.hp / enemy.max_hp) * 100 : 0) + '%';

        this.renderCombo(battle.combo);

        if (this.feedbackEl) { this.feedbackEl.hidden = true; }
        this.submitEl.hidden = false;
        this.submitEl.dataset.action = 'attack';
        this.continueEl.hidden = true;

        this.renderQuestion(battle.question);
    };

    Battle.prototype.renderCombo = function (combo, combatEvent) {
        if (!this.messageComboEl) { return; }
        combo = combo || { count: 0, current: 0, threshold: 3, remaining: 3, is_power_strike: false };
        var count = typeof combo.count !== 'undefined' ? combo.count : (combo.current || 0);
        var threshold = combo.threshold || 3;
        var isPowerStrike = (combatEvent && combatEvent.code === 'power_strike') || !!combo.is_power_strike;

        this.messageComboEl.dataset.count = String(isPowerStrike ? threshold : count);

        if (this.messageComboTextEl) {
            if (isPowerStrike) {
                this.messageComboTextEl.textContent = 'POWER STRIKE (2 DMG)';
            } else if (count === 1) {
                this.messageComboTextEl.textContent = 'Combo 1/3 (2 to Strike)';
            } else if (count === 2) {
                this.messageComboTextEl.textContent = 'Combo 2/3 (1 to Strike!)';
            } else {
                this.messageComboTextEl.textContent = 'Combo 0/3';
            }
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
            var count = question.expected_item_count || 1;
            this.legendEl.textContent = question.order_matters
                ? ('Enter all ' + count + ' items in order')
                : ('Enter all ' + count + ' items in any order');
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
        var listContainer = document.createElement('div');
        listContainer.className = 'dungeon-enumeration-list';
        listContainer.dataset.generated = 'true';

        for (var index = 0; index < count; index += 1) {
            var input = document.createElement('input');
            input.type = 'text';
            input.className = 'dungeon-enum-answer';
            input.dataset.role = 'enum-answer';
            input.autocomplete = 'off';
            input.setAttribute('aria-label', 'Item ' + (index + 1) + ' of ' + count);
            input.placeholder = 'Item ' + (index + 1);
            listContainer.appendChild(input);
        }
        this.fieldsEl.appendChild(listContainer);
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

        if ((this.continueEl && !this.continueEl.hidden) || (this.feedbackEl && !this.feedbackEl.hidden)) {
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
        this.continueEl.dataset.action = result.enemy_defeated || result.run_over
            ? 'continue'
            : 'next';
        this.continueEl.focus();
    };

    Battle.prototype.showFeedback = function (result) {
        var feedback = result.feedback || {};
        var combatEvent = result.combat_event || {};
        var isPowerStrike = (combatEvent.code === 'power_strike') || (result.combo && result.combo.is_power_strike);
        var isReviewRun = result.is_review_run || (this.battle && this.battle.is_review_run);

        var title = 'DIRECT HIT!';
        var meta = '1 DAMAGE';
        var outcome = result.outcome || 'correct';
        var primary = '';
        var answer = '';
        var status = '';
        var explanation = feedback.explanation || '';

        if (isPowerStrike) {
            outcome = 'power_strike';
            title = 'POWER STRIKE!';
            meta = '2 DAMAGE';
            primary = 'Combo threshold reached! Devastating blow landed.';
        } else if (result.outcome === 'correct') {
            title = 'DIRECT HIT!';
            meta = '1 DAMAGE';
            primary = 'Your strike hits true!';
        } else if (result.outcome === 'incorrect') {
            title = 'YOU TOOK A HIT!';
            var lostHp = (result.damage_to_player > 0 ? result.damage_to_player : 1);
            meta = lostHp + ' HP LOST';
            primary = 'The enemy counterattacks!';
        } else if (result.outcome === 'partial') {
            title = 'GLANCING BLOW';
            meta = 'PARTIAL CREDIT';
            primary = 'Glancing blow — nobody takes full damage.';
        } else if (result.outcome === 'skipped') {
            title = 'SLIPPED PAST';
            meta = '0 DAMAGE';
            primary = 'You slipped safely past the question.';
        } else {
            title = 'TURN GRADED';
            meta = '';
            primary = 'Turn resolved.';
        }

        if (result.outcome === 'incorrect' || result.outcome === 'partial') {
            if (feedback.correct_choice_text) {
                answer = 'Correct answer: ' + feedback.correct_choice_text;
            } else if (feedback.canonical_answer) {
                answer = 'Correct answer: ' + feedback.canonical_answer;
            }

            if (feedback.missing_items && feedback.missing_items.length) {
                var missingCount = feedback.missing_items.length;
                if (missingCount <= 3) {
                    var missedList = feedback.missing_items.join(' · ');
                    answer = answer ? (answer + ' | Missing: ' + missedList) : ('Missing: ' + missedList);
                } else {
                    var missedSummary = 'Missing ' + missingCount + ' answers · View expected list below';
                    answer = answer ? (answer + ' | ' + missedSummary) : missedSummary;
                    var fullMissingList = 'Expected items: ' + feedback.missing_items.join(', ');
                    explanation = explanation ? (fullMissingList + '\n\n' + explanation) : fullMissingList;
                }
            }
        } else if (result.outcome === 'correct') {
            if (feedback.correct_choice_text) {
                answer = 'Correct answer: ' + feedback.correct_choice_text;
            } else if (feedback.canonical_answer) {
                answer = 'Correct answer: ' + feedback.canonical_answer;
            }
        }

        if (isReviewRun) {
            if (result.outcome === 'correct') {
                status = 'Remediation question mastered!';
            } else {
                status = 'Remediation item not yet mastered.';
            }
        } else {
            if (result.outcome === 'correct') {
                if (isPowerStrike) {
                    status = 'Power Strike triggered!';
                } else {
                    var count = (result.combo && typeof result.combo.count !== 'undefined') ? result.combo.count : 1;
                    var threshold = (result.combo && result.combo.threshold) || 3;
                    status = 'Combo increased to ' + count + ' of ' + threshold + '.';
                }
            } else if (result.outcome === 'incorrect') {
                status = 'Review this concept after the encounter.';
            } else if (result.outcome === 'partial') {
                status = 'Your combo was preserved.';
            }
        }

        this.renderCombatMessage({
            state: 'graded',
            outcome: outcome,
            title: title,
            meta: meta,
            combo: null,
            primary: primary,
            answer: answer,
            status: status,
            explanation: explanation
        });

        if (this.feedbackEl) {
            this.feedbackEl.hidden = true;
        }

        var announceText = title + ': ' + meta + '. ' + primary + (answer ? ' ' + answer + '.' : '') + ' ' + (status ? ' ' + status + '.' : '');
        this.announce(announceText);

        if (global.innerWidth < 768 && this.messageBoxEl) {
            this.messageBoxEl.scrollIntoView({
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
            var count = (this.battle.combo && typeof this.battle.combo.count !== 'undefined') ? this.battle.combo.count : 0;
            var threshold = (this.battle.combo && this.battle.combo.threshold) || 3;
            var comboHint = 'Three consecutive correct answers trigger a Power Strike.';
            if (count === 1) {
                comboHint = 'Two more correct answers will trigger a Power Strike.';
            } else if (count === 2) {
                comboHint = 'One more correct answer will trigger a Power Strike!';
            }

            this.renderCombatMessage({
                state: 'awaiting-answer',
                outcome: 'turn',
                title: 'YOUR TURN',
                combo: {
                    visible: true,
                    count: count,
                    threshold: threshold,
                    text: 'Combo ' + count + '/' + threshold
                },
                primary: 'Choose an answer on the right, then attack.',
                status: comboHint,
                answer: '',
                explanation: '',
                showCaret: false
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
