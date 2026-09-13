document.addEventListener('DOMContentLoaded', () => {
    const rawData = document.getElementById('quiz-data');
    if (!rawData) return;
    const quiz = JSON.parse(rawData.textContent);

    const wrap = document.getElementById('quiz-question-wrap');
    const progressFill = document.getElementById('quiz-progress-fill');
    const progressLabel = document.getElementById('quiz-progress-label');
    const actionBtn = document.getElementById('action-btn');

    // Permanent Response Dock & Feedback Elements
    const responseDock = document.getElementById('quiz-response-dock');
    const checkState = document.getElementById('quiz-dock-check-state');
    const sheet = document.getElementById('quiz-feedback-sheet');
    const sheetContinue = document.getElementById('sheet-continue-btn');
    const sheetAnswerLabel = document.getElementById('sheet-answer-label');
    const sheetTitleText = document.getElementById('sheet-title-text');
    const sheetExplText = document.getElementById('sheet-expl-text');
    const sheetExtraWrap = document.getElementById('sheet-extra-wrap');
    const sheetStatusPill = document.getElementById('sheet-status-pill');
    const sheetPointsPill = document.getElementById('sheet-points-pill');

    // Result & Review Modals
    const resultSheet = document.getElementById('result-sheet');
    const reviewModal = document.getElementById('review-drawer-modal');
    const reviewContainer = document.getElementById('review-items-container');

    // Required Elements Guard
    if (!wrap || !actionBtn || !sheet) {
        console.error('Quiz initialization aborted: missing essential DOM elements.');
        return;
    }

    function setText(id, value) {
        const el = document.getElementById(id);
        if (el) el.textContent = value ?? '';
    }

    const letters = ['A', 'B', 'C', 'D'];
    const qpShapes = ['▲', '◆', '●', '■'];

    let currentIndex = 0;
    let selectedAnswers = {};
    let gradedHistory = [];
    let isCurrentGraded = false;
    let serverReviewData = null;

    function showCheckState() {
        if (responseDock) {
            responseDock.classList.remove('is-correct', 'is-partial', 'is-incorrect');
            responseDock.hidden = false;
        }
        if (checkState) {
            checkState.hidden = false;
        }
        if (sheet) {
            sheet.hidden = true;
        }
    }

    function populateFeedback(data, q) {
        const earned = Number(data.points_awarded ?? data.earned_points ?? (data.is_correct ? 1 : 0));
        const maxPts = Number(data.maximum_points ?? q.max_points ?? 1);
        const status = data.status || (earned >= maxPts && maxPts > 0 ? 'correct' : (earned > 0 ? 'partial' : 'incorrect'));
        const isLast = currentIndex === quiz.questions.length - 1;

        if (responseDock) {
            responseDock.classList.remove('is-correct', 'is-partial', 'is-incorrect');
            responseDock.classList.add(`is-${status}`);
        }

        if (sheetStatusPill) {
            sheetStatusPill.textContent = status === 'correct' ? 'CORRECT' : (status === 'partial' ? 'PARTIALLY CORRECT' : 'NEEDS REVIEW');
        }
        if (sheetPointsPill) {
            sheetPointsPill.textContent = `${earned} / ${maxPts} point${maxPts === 1 ? '' : 's'}`;
        }

        let label = 'CORRECT ANSWER';
        let heading = '';
        let extraHtml = '';

        if (q.type === 'multiple_choice' || q.type === 'true_false') {
            label = 'CORRECT ANSWER';
            heading = data.correct_choice_text || '';
            if (!heading && q.choices) {
                const matched = q.choices.find(c => c.id === data.correct_choice_id);
                if (matched) heading = matched.text;
            }
        } else if (q.type === 'identification') {
            label = 'ACCEPTED ANSWER';
            heading = data.canonical_answer || (data.accepted_answers && data.accepted_answers[0]) || '';
            if (data.accepted_answers && data.accepted_answers.length > 1) {
                const canonicalNorm = heading.trim().toLowerCase();
                const variants = data.accepted_answers.filter(a => a.trim().toLowerCase() !== canonicalNorm);
                if (variants.length) {
                    extraHtml = `<div>Also accepted: <strong>${variants.join(', ')}</strong></div>`;
                }
            }
        } else if (q.type === 'enumeration') {
            const missing = data.missing_items || [];
            const expected = data.expected_items || [];

            if (status === 'correct') {
                label = expected.length === 1 ? 'EXPECTED ANSWER' : 'EXPECTED ANSWERS';
                heading = expected.join(', ');
            } else if (status === 'partial') {
                label = missing.length === 1 ? 'MISSING ANSWER' : 'MISSING ANSWERS';
                heading = missing.join(', ');
                if (expected.length) {
                    extraHtml = `<div>Expected full list: <strong>${expected.join(', ')}</strong></div>`;
                }
            } else {
                label = expected.length === 1 ? 'EXPECTED ANSWER' : 'EXPECTED ANSWERS';
                heading = expected.join(', ');
            }
        }

        if (sheetAnswerLabel) sheetAnswerLabel.textContent = label;
        if (sheetTitleText) sheetTitleText.textContent = heading;
        if (sheetExplText) sheetExplText.textContent = data.explanation || '';
        if (sheetExtraWrap) sheetExtraWrap.innerHTML = extraHtml;
        if (sheetContinue) sheetContinue.textContent = isLast ? 'Complete Quiz' : 'Next Question';
    }

    function showFeedbackState(data, q) {
        populateFeedback(data, q);
        if (checkState) checkState.hidden = true;
        if (sheet) {
            sheet.hidden = false;
            sheet.scrollTop = 0;
        }
    }

    // Layout configuration: URL param > persistent preference > session preference > 'quick_play'
    let currentLayout = new URLSearchParams(window.location.search).get('layout')
        || localStorage.getItem('studyquest_quiz_style_preference')
        || sessionStorage.getItem('studyquest_quiz_style_session')
        || 'quick_play';
    if (currentLayout !== 'quick_play' && currentLayout !== 'standard') {
        currentLayout = 'quick_play';
    }
    document.body.dataset.quizLayout = currentLayout;

    let isQuizCompleted = false;

    // In-Quiz Style Dropdown Menu
    const dropdownWrap = document.getElementById('quiz-style-dropdown-wrap');
    const dropdownBtn = document.getElementById('quiz-style-dropdown-btn');
    const dropdownMenu = document.getElementById('quiz-style-dropdown-menu');
    const styleLabel = document.getElementById('quiz-style-label');
    const switchBtn = document.getElementById('style-switch-btn');
    const switchIcon = document.getElementById('switch-btn-icon');
    const switchTitle = document.getElementById('switch-btn-title');
    const switchSubtitle = document.getElementById('switch-btn-subtitle');
    const forgetBtn = document.getElementById('style-forget-btn');

    function isStyleLocked() {
        return isQuizCompleted || currentIndex > 0 || isCurrentGraded || gradedHistory.length > 0;
    }

    function showQuizToast(message) {
        const toast = document.getElementById('quiz-toast');
        if (!toast) return;
        toast.textContent = message;
        toast.hidden = false;
        setTimeout(() => {
            toast.hidden = true;
        }, 2800);
    }

    function updateStyleDropdownUI() {
        if (isQuizCompleted) {
            if (dropdownWrap) {
                dropdownWrap.style.display = 'none';
                dropdownWrap.hidden = true;
            }
            return;
        }
        if (styleLabel) {
            styleLabel.textContent = currentLayout === 'quick_play' ? '⚡ Quick Play' : '📄 Standard';
        }
        if (switchTitle) {
            const nextMode = currentLayout === 'quick_play' ? 'Standard' : 'Quick Play';
            const nextIcon = currentLayout === 'quick_play' ? '📄' : '⚡';
            const nextSub = currentLayout === 'quick_play' ? 'Compact, focused layout' : 'Full answer board';
            if (switchIcon) switchIcon.textContent = nextIcon;
            switchTitle.textContent = `Switch to ${nextMode}`;
            if (switchSubtitle) {
                switchSubtitle.textContent = isStyleLocked() ? 'Locked for this attempt' : nextSub;
            }
        }
        if (switchBtn) {
            if (isStyleLocked()) {
                switchBtn.disabled = true;
                switchBtn.title = 'Quiz style cannot be changed after Question 1 has been answered.';
            } else {
                switchBtn.disabled = false;
                switchBtn.title = '';
            }
        }
    }
    updateStyleDropdownUI();

    if (dropdownBtn && dropdownMenu) {
        dropdownBtn.addEventListener('click', (e) => {
            if (isQuizCompleted) return;
            e.stopPropagation();
            const isHidden = dropdownMenu.hidden;
            dropdownMenu.hidden = !isHidden;
            dropdownBtn.setAttribute('aria-expanded', String(isHidden));
            dropdownWrap?.classList.toggle('open', isHidden);
            updateStyleDropdownUI();
        });

        document.addEventListener('click', (e) => {
            if (dropdownWrap && !dropdownWrap.contains(e.target)) {
                dropdownMenu.hidden = true;
                dropdownBtn.setAttribute('aria-expanded', 'false');
                dropdownWrap.classList.remove('open');
            }
        });
    }

    if (switchBtn) {
        switchBtn.addEventListener('click', () => {
            if (isQuizCompleted || isStyleLocked()) {
                showQuizToast('Layout is locked after answering Question 1.');
                return;
            }
            currentLayout = currentLayout === 'quick_play' ? 'standard' : 'quick_play';
            document.body.dataset.quizLayout = currentLayout;
            if (localStorage.getItem('studyquest_quiz_style_preference')) {
                localStorage.setItem('studyquest_quiz_style_preference', currentLayout);
            }
            sessionStorage.setItem('studyquest_quiz_style_session', currentLayout);
            updateStyleDropdownUI();
            if (dropdownMenu) {
                dropdownMenu.hidden = true;
                dropdownBtn?.setAttribute('aria-expanded', 'false');
                dropdownWrap?.classList.remove('open');
            }
            if (!isQuizCompleted && quiz && quiz.questions && quiz.questions[currentIndex]) {
                renderQuestion();
            }
        });
    }

    if (forgetBtn) {
        forgetBtn.addEventListener('click', () => {
            localStorage.removeItem('studyquest_quiz_style_preference');
            sessionStorage.removeItem('studyquest_quiz_style_session');
            localStorage.removeItem('studyquest_quiz_style');
            if (dropdownMenu) {
                dropdownMenu.hidden = true;
                dropdownBtn?.setAttribute('aria-expanded', 'false');
                dropdownWrap?.classList.remove('open');
            }
            showQuizToast("Saved style preference cleared. You'll choose style on your next quiz.");
        });
    }

    function getCookie(name) {
        const parts = (`; ${document.cookie}`).split(`; ${name}=`);
        return parts.length === 2 ? parts.pop().split(';').shift() : '';
    }

    function escapeHtml(value) {
        return String(value ?? '')
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#039;');
    }

    function escapeList(items) {
        return (items || []).map(escapeHtml).join(' • ');
    }

    function setResultPresentation(resultData) {
        if (!resultData) return;

        const mascotImg = document.getElementById('result-mascot-img');
        const guidanceTitle = document.getElementById('result-guidance-title');
        const guidanceMessage = document.getElementById('result-guidance-message');

        const percentage = Number(resultData.percentage || 0);
        const passed = percentage >= 75 || Boolean(resultData.passed);

        let sprite;
        let titleText;
        let messageHtml;

        if (percentage === 100) {
            sprite = '/static/courses/images/mascot_result_excellent.png';
            titleText = 'That was excellent!';
            messageHtml = "You <b>aced</b> every single question! Perfect score! You've completely mastered this chapter.";
        } else if (percentage >= 85) {
            sprite = '/static/courses/images/mascot_result_excellent.png';
            titleText = 'That was amazing!';
            messageHtml = 'You barely made any errors! Come <b>review</b> the couple you missed and we are golden.';
        } else if (passed) {
            sprite = '/static/courses/images/mascot_result_passed.png';
            titleText = 'You did well!';
            messageHtml = "You <b>passed<b> the quiz! Let's take a quick look at your answers to lock in what you've learned.";
        } else {
            sprite = '/static/courses/images/mascot_result_review.png';
            titleText = "You'll get it next time!";
            messageHtml = 'Come <b>review</b> this chapter with me so we can crush the retake together!';
        }

        if (mascotImg) mascotImg.src = sprite;
        if (guidanceTitle) guidanceTitle.textContent = titleText;
        if (guidanceMessage) guidanceMessage.innerHTML = messageHtml;
    }

    function positionResultPresentation() {
        const presentation = document.getElementById('result-presentation');
        if (!presentation || !resultSheet) return;

        const resultHeight = resultSheet.getBoundingClientRect().height;
        const overlap = window.innerWidth <= 640 ? 20 : 36;
        presentation.style.bottom = `${Math.max(resultHeight - overlap, 0)}px`;
    }

    function showResultPresentation(resultData, animate = true) {
        setResultPresentation(resultData);

        // Hide style dropdown, action button, question wrapper, stage, board, response dock, and badges completely on results
        if (dropdownWrap) {
            dropdownWrap.style.display = 'none';
            dropdownWrap.hidden = true;
        }
        if (wrap) {
            wrap.innerHTML = '';
            wrap.style.display = 'none';
        }
        if (actionBtn) {
            actionBtn.classList.add('is-hidden');
            actionBtn.style.display = 'none';
        }
        if (responseDock) {
            responseDock.style.display = 'none';
            responseDock.hidden = true;
        }
        const stage = document.getElementById('quiz-question-stage');
        if (stage) stage.style.display = 'none';
        const answerBoard = document.getElementById('quiz-answer-board');
        if (answerBoard) answerBoard.style.display = 'none';
        const metaDot = document.getElementById('quiz-meta-dot');
        if (metaDot) metaDot.style.display = 'none';
        const typeBadge = document.getElementById('quiz-type-badge');
        if (typeBadge) typeBadge.style.display = 'none';

        if (animate) {
            document.body.classList.remove('show-result-mascot');
        }

        requestAnimationFrame(() => {
            positionResultPresentation();
            requestAnimationFrame(() => {
                document.body.classList.add('show-result-mascot');
            });
        });
    }

    function renderQuestion() {
        if (isQuizCompleted) return;
        isCurrentGraded = false;
        const q = quiz.questions[currentIndex];
        if (!q) return;

        const isQuickPlay = currentLayout === 'quick_play';
        const isTF = q.type === 'true_false';
        const typeLabels = {
            multiple_choice: 'Multiple Choice',
            true_false: 'True or False',
            identification: isQuickPlay ? 'Written Response' : 'Identification',
            enumeration: 'Enumeration'
        };

        // Reset Response Dock to Check State & Restore Wrap
        if (responseDock) {
            responseDock.style.display = '';
            responseDock.hidden = false;
        }
        showCheckState();
        if (wrap) wrap.style.display = '';

        // Restore action button state
        actionBtn.disabled = !selectedAnswers[q.id];
        actionBtn.textContent = 'Check Answer';

        // Update Progress Bar
        const pct = Math.round(((currentIndex + 1) / quiz.questions.length) * 100);
        if (progressFill) progressFill.style.width = `${pct}%`;
        if (progressLabel) {
            progressLabel.classList.remove('is-complete');
            progressLabel.textContent = `Question ${currentIndex + 1} of ${quiz.questions.length}`;
        }

        // Region 1 Meta: Type badge and separator dot
        const badgeEl = document.getElementById('quiz-type-badge');
        if (badgeEl) {
            badgeEl.style.display = '';
            badgeEl.textContent = typeLabels[q.type] || q.type;
        }
        const metaDot = document.getElementById('quiz-meta-dot');
        if (metaDot) metaDot.style.display = '';

        // Region 2 Center Stage: Question text
        const qTextEl = document.getElementById('quiz-question-text');
        if (qTextEl) {
            qTextEl.textContent = q.text;
        }
        const qStage = document.getElementById('quiz-question-stage');
        if (qStage) qStage.style.display = '';
        const ansBoard = document.getElementById('quiz-answer-board');
        if (ansBoard) ansBoard.style.display = '';

        // Generate Question Body
        let bodyHtml = '';
        if (q.type === 'multiple_choice' || q.type === 'true_false') {
            const currentPick = selectedAnswers[q.id]?.choice_id;
            bodyHtml = `
                <div class="quiz-choices ${isTF ? 'tf-layout' : ''}" id="choices-container">
                    ${q.choices.map((c, i) => {
                        let badge = '';
                        let extraClass = '';
                        if (isQuickPlay) {
                            if (isTF) {
                                const isTrue = c.text.trim().toLowerCase().startsWith('t');
                                badge = isTrue ? '▲' : '◆';
                                extraClass = isTrue ? 'qp-tf-true' : 'qp-tf-false';
                            } else {
                                badge = qpShapes[i % qpShapes.length] || '';
                                extraClass = `qp-opt-${i % 4}`;
                            }
                        } else {
                            if (isTF) {
                                badge = c.text.trim().toLowerCase().startsWith('t') ? 'T' : 'F';
                            } else {
                                badge = letters[i] || '';
                            }
                        }
                        const isSelected = currentPick === c.id;
                        return `
                            <button type="button" class="quiz-option ${extraClass} ${isSelected ? 'selected' : ''}" data-choice-id="${c.id}">
                                <span class="quiz-letter">${badge}</span>
                                <span>${c.text}</span>
                            </button>
                        `;
                    }).join('')}
                </div>
            `;
        } else if (q.type === 'identification') {
            const currentVal = selectedAnswers[q.id]?.text || '';
            bodyHtml = `
                <div class="quiz-typed-answer">
                    <input type="text" class="quiz-text-input" id="id-answer-input" placeholder="Type your answer..." value="${currentVal}" autocomplete="off" autocapitalize="off">
                </div>
            `;
        } else if (q.type === 'enumeration') {
            const currentItems = selectedAnswers[q.id]?.items || [];
            const count = q.expected_count || 3;
            let inputs = '';
            for (let i = 0; i < count; i++) {
                inputs += `
                    <input type="text" class="quiz-text-input enumeration-input" data-index="${i}" placeholder="Item ${i + 1}..." value="${currentItems[i] || ''}" autocomplete="off">
                `;
            }
            bodyHtml = `<div class="enumeration-inputs">${inputs}</div>`;
        }

        wrap.innerHTML = bodyHtml;
        wrap.style.display = '';

        updateStyleDropdownUI();
        bindInputEvents(q);
    }

    function bindInputEvents(q) {
        wrap.querySelectorAll('.quiz-option').forEach(btn => {
            btn.addEventListener('click', () => {
                if (isCurrentGraded) return;
                const cId = Number(btn.dataset.choiceId);
                selectedAnswers[q.id] = { choice_id: cId };
                wrap.querySelectorAll('.quiz-option').forEach(b => b.classList.remove('selected'));
                btn.classList.add('selected');
                actionBtn.disabled = false;
            });
        });

        const idInput = wrap.querySelector('#id-answer-input');
        if (idInput) {
            idInput.addEventListener('input', () => {
                const val = idInput.value.trim();
                if (val) {
                    selectedAnswers[q.id] = { text: val };
                    actionBtn.disabled = false;
                } else {
                    delete selectedAnswers[q.id];
                    actionBtn.disabled = true;
                }
            });
            idInput.addEventListener('focus', () => {
                setTimeout(() => idInput.scrollIntoView({ behavior: 'smooth', block: 'center' }), 280);
            });
        }

        const enumInputs = wrap.querySelectorAll('.enumeration-input');
        if (enumInputs.length) {
            enumInputs.forEach(input => {
                input.addEventListener('input', () => {
                    const items = Array.from(enumInputs).map(inp => inp.value.trim());
                    const hasAny = items.some(Boolean);
                    if (hasAny) {
                        selectedAnswers[q.id] = { items };
                        actionBtn.disabled = false;
                    } else {
                        delete selectedAnswers[q.id];
                        actionBtn.disabled = true;
                    }
                });
                input.addEventListener('focus', () => {
                    setTimeout(() => input.scrollIntoView({ behavior: 'smooth', block: 'center' }), 280);
                });
            });
        }
    }

    async function handleCheckAnswer() {
        if (isCurrentGraded) return;
        const q = quiz.questions[currentIndex];
        const isLast = currentIndex === quiz.questions.length - 1;
        const answerPayload = selectedAnswers[q.id];

        actionBtn.disabled = true;
        actionBtn.textContent = 'Verifying...';

        try {
            const res = await fetch(quiz.checkUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCookie('csrftoken'),
                    'X-Requested-With': 'XMLHttpRequest'
                },
                body: JSON.stringify({ question_id: q.id, answer: answerPayload })
            });

            if (!res.ok) throw new Error('Grading check failed');
            const data = await res.json();
            isCurrentGraded = true;
            gradedHistory.push({ question: q, userPick: answerPayload, result: data });
            updateStyleDropdownUI();

            if (q.type === 'multiple_choice' || q.type === 'true_false') {
                wrap.querySelectorAll('.quiz-option').forEach(btn => {
                    btn.classList.add('locked');
                    const cId = Number(btn.dataset.choiceId);
                    if (cId === data.correct_choice_id) btn.classList.add('is-correct');
                    if (cId === answerPayload.choice_id && !data.is_correct) btn.classList.add('is-incorrect');
                    if (cId !== data.correct_choice_id && cId !== answerPayload.choice_id) btn.classList.add('is-dimmed');
                });
            } else if (q.type === 'identification') {
                const idInput = wrap.querySelector('#id-answer-input');
                if (idInput) {
                    idInput.disabled = true;
                    idInput.classList.add(data.is_correct ? 'is-correct' : 'is-incorrect');
                }
            } else if (q.type === 'enumeration') {
                wrap.querySelectorAll('.enumeration-input').forEach(inp => inp.disabled = true);
            }

            showFeedbackState(data, q);

        } catch (err) {
            console.error('Answer check error:', err);
            actionBtn.disabled = false;
            actionBtn.textContent = 'Check Answer';
        }
    }

    actionBtn.addEventListener('click', handleCheckAnswer);

    sheetContinue?.addEventListener('click', () => {
        if (currentIndex < quiz.questions.length - 1) {
            currentIndex++;
            renderQuestion();
        } else {
            finalizeQuiz();
        }
    });

    async function finalizeQuiz() {
        isQuizCompleted = true;
        if (responseDock) {
            responseDock.style.display = 'none';
            responseDock.hidden = true;
        }
        if (sheet) {
            sheet.hidden = true;
        }
        if (checkState) {
            checkState.hidden = true;
        }

        // Completely hide the style switcher dropdown
        if (dropdownWrap) {
            dropdownWrap.style.display = 'none';
            dropdownWrap.hidden = true;
        }
        if (dropdownMenu) {
            dropdownMenu.hidden = true;
        }

        // Hide action button
        if (actionBtn) {
            actionBtn.classList.add('is-hidden');
            actionBtn.style.display = 'none';
            actionBtn.disabled = true;
        }

        // Hide question stage and meta indicators immediately during evaluation
        const stage = document.getElementById('quiz-question-stage');
        if (stage) stage.style.display = 'none';
        const metaDot = document.getElementById('quiz-meta-dot');
        if (metaDot) metaDot.style.display = 'none';
        const typeBadge = document.getElementById('quiz-type-badge');
        if (typeBadge) typeBadge.style.display = 'none';

        if (progressFill) progressFill.style.width = '100%';

        // Upgrade progress counter to glowing completion banner
        if (progressLabel) {
            progressLabel.textContent = 'QUIZ 100% COMPLETE';
            progressLabel.classList.add('is-complete');
        }

        // Educational, up-to-date assessment copy
        wrap.innerHTML = `
            <div style="text-align: center; padding: 70px 20px;">
                <div style="font-size: 2.2rem; margin-bottom: 12px;">📝</div>
                <h2 style="margin: 0; font-size: 1.35rem; color: var(--ink);">Evaluating Assessment...</h2>
                <p style="margin: 6px 0 0; color: var(--muted); font-size: 0.9rem;">Compiling your answer review.</p>
            </div>
        `;

        try {
            const res = await fetch(quiz.submitUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCookie('csrftoken'),
                    'X-Requested-With': 'XMLHttpRequest'
                },
                body: JSON.stringify({ answers: selectedAnswers })
            });

            if (!res.ok) throw new Error('Evaluation submission failed');
            const data = await res.json();
            serverReviewData = data;

            setText('sheet-score', `${data.score} / ${data.maximum_score}`);
            setText('sheet-percentage', `${data.percentage}%`);
            setText('sheet-xp', `+${data.xp_earned} XP`);

            const passBadge = document.getElementById('result-pass-badge');
            if (passBadge) {
                passBadge.textContent = data.passed ? 'Assessment Passed' : 'Review Suggested';
                passBadge.className = `result-badge ${data.passed ? 'passed' : 'retry'}`;
            }

            // Clear loading text and hide wrap and answer board before presenting the final result.
            wrap.innerHTML = '';
            wrap.style.display = 'none';
            const answerBoard = document.getElementById('quiz-answer-board');
            if (answerBoard) answerBoard.style.display = 'none';

            if (resultSheet) {
                resultSheet.classList.add('open');
                showResultPresentation(data, true);
            }
        } catch (err) {
            console.error('Finalize error:', err);
            wrap.style.display = '';
            wrap.innerHTML = `
                <div style="text-align: center; padding: 50px 20px;">
                    <p style="color: var(--danger);">Failed to save assessment results.</p>
                    <button type="button" class="btn btn-secondary" onclick="location.reload()">Retry</button>
                </div>
            `;
        }
    }

    function buildReviewModal() {
        if (!reviewContainer || !serverReviewData || !serverReviewData.review_items) return;

        reviewContainer.innerHTML = serverReviewData.review_items.map((item, idx) => {
            const statusClass = ['correct', 'partial', 'incorrect'].includes(item.result_state) 
                ? item.result_state 
                : 'incorrect';
            
            const statusLabel = statusClass === 'correct' 
                ? 'Correct' 
                : (statusClass === 'partial' ? 'Partially Correct' : 'Needs Review');

            let detailHtml = '';

            if (item.question_type === 'multiple_choice' || item.question_type === 'true_false') {
                detailHtml = `
                    <div class="review-data-row">
                        <span>Submitted answer:</span>
                        <strong class="${item.is_correct ? 'text-accent' : 'text-danger'}">${escapeHtml(item.submitted_text || 'No answer provided')}</strong>
                    </div>
                    ${!item.is_correct ? `
                        <div class="review-data-row">
                            <span>Correct answer:</span>
                            <strong class="text-accent">${escapeHtml(item.correct_text)}</strong>
                        </div>
                    ` : ''}
                `;
            } else if (item.question_type === 'identification') {
                detailHtml = `
                    <div class="review-data-row">
                        <span>Submitted response:</span>
                        <strong class="${item.is_correct ? 'text-accent' : 'text-danger'}">${escapeHtml(item.submitted_text || 'No answer provided')}</strong>
                    </div>
                    ${!item.is_correct ? `
                        <div class="review-data-row">
                            <span>Accepted term:</span>
                            <strong class="text-accent">${escapeHtml(item.canonical_text)}</strong>
                        </div>
                        ${item.accepted_variants && item.accepted_variants.length ? `
                            <div class="review-data-row">
                                <span>Accepted variants:</span>
                                <span>${escapeList(item.accepted_variants)}</span>
                            </div>
                        ` : ''}
                    ` : ''}
                `;
            } else if (item.question_type === 'enumeration') {
                detailHtml = `
                    <div class="review-data-block">
                        <div class="review-data-row">
                            <span>Submitted items:</span>
                            <span>${escapeList(item.submitted_items)}</span>
                        </div>
                        ${item.matched_items && item.matched_items.length ? `
                            <div class="review-data-row text-accent">
                                <span>Matched items:</span>
                                <strong>${escapeList(item.matched_items)}</strong>
                            </div>
                        ` : ''}
                        ${item.missing_items && item.missing_items.length ? `
                            <div class="review-data-row text-danger">
                                <span>Missing items:</span>
                                <strong>${escapeList(item.missing_items)}</strong>
                            </div>
                        ` : ''}
                        <div class="review-data-row">
                            <span>Expected full list:</span>
                            <span>${escapeList(item.canonical_items)}</span>
                        </div>
                        ${item.order_matters ? '<small class="review-hint">Order of enumeration was graded strictly.</small>' : ''}
                    </div>
                `;
            }

            return `
                <article class="review-item-card ${statusClass}">
                    <div class="review-item-header">
                        <div class="review-item-status-group">
                            <span class="feedback-status-pill">${statusLabel}</span>
                            <span class="quiz-tag">${escapeHtml(item.question_type.replace('_', ' '))}</span>
                        </div>
                        <span class="feedback-points-pill">${item.earned_points} / ${item.maximum_points} Pt${item.maximum_points === 1 ? '' : 's'}</span>
                    </div>

                    <h4 class="review-item-qtext">Q${idx + 1}. ${escapeHtml(item.prompt)}</h4>

                    <div class="review-answer-block">
                        ${detailHtml}
                    </div>

                    ${item.explanation ? `
                        <div class="review-expl-box">
                            <strong class="review-expl-label">Explanation</strong>
                            <p class="review-expl-text">${escapeHtml(item.explanation)}</p>
                        </div>
                    ` : ''}
                </article>
            `;
        }).join('');
    }

    document.getElementById('open-review-btn')?.addEventListener('click', () => {
        buildReviewModal();
        if (reviewModal) {
            reviewModal.hidden = false;
            reviewModal.classList.add('is-active');
            document.body.classList.add('modal-open');
        }
    });

    // ================= REVIEW MODAL DISMISS & ESCAPE =================
    const urlParams = new URLSearchParams(window.location.search);
    const isReviewMode = urlParams.get('view') === 'review';
    const chapterReviewUrl = document.getElementById('quiz-exit-link')?.getAttribute('href') || '';
    const backToScoreBtn = document.getElementById('back-to-results-btn');

    function exitReviewDrawer() {
        if (isReviewMode && chapterReviewUrl) {
            window.location.href = chapterReviewUrl;
        } else if (reviewModal) {
            reviewModal.hidden = true;
            reviewModal.classList.remove('is-active');
            document.body.classList.remove('modal-open');
        }
    }

    document.getElementById('close-review-btn')?.addEventListener('click', exitReviewDrawer);
    backToScoreBtn?.addEventListener('click', exitReviewDrawer);

    reviewModal?.addEventListener('click', (e) => {
        if (e.target === reviewModal) exitReviewDrawer();
    });

    // ================= KEYBOARD SHORTCUTS =================
    document.addEventListener('keydown', (event) => {
        if (event.repeat) return;

        // Disengage if result summary or review modal is active
        if (resultSheet?.classList.contains('open')) return;
        if (reviewModal && !reviewModal.hidden) return;

        const question = quiz.questions[currentIndex];
        if (!question) return;

        const isSpace = event.key === ' ' || event.code === 'Space';
        const isEnter = event.key === 'Enter';

        if (!isSpace && !isEnter) return;

        const activeEl = document.activeElement;
        const isTyping = Boolean(activeEl?.matches('input, textarea, select, [contenteditable="true"]'));
        const isChoiceQuestion = question.type === 'multiple_choice' || question.type === 'true_false';

        // 1. Before Grading (Submitting Answers)
        if (!isCurrentGraded) {
            // Space submits only Multiple Choice and True/False
            if (isSpace) {
                if (isChoiceQuestion && !isTyping && !actionBtn.disabled) {
                    event.preventDefault();
                    event.stopPropagation();
                    if (activeEl && typeof activeEl.blur === 'function') activeEl.blur();
                    handleCheckAnswer();
                }
                return;
            }

            // Enter submits when ready (advances fields like Tab for Enumeration)
            if (isEnter) {
                if (question.type === 'enumeration' && isTyping) {
                    if (activeEl?.classList.contains('enumeration-input')) {
                        event.preventDefault();
                        event.stopPropagation();

                        const enumInputs = Array.from(wrap.querySelectorAll('.enumeration-input'));
                        const inputIdx = enumInputs.indexOf(activeEl);

                        // If not on the last input, advance focus to the next field
                        if (inputIdx !== -1 && inputIdx < enumInputs.length - 1) {
                            enumInputs[inputIdx + 1].focus();
                            return;
                        }

                        // On the last input: submit if an answer is provided, otherwise blur
                        if (inputIdx === enumInputs.length - 1) {
                            if (!actionBtn.disabled) {
                                activeEl.blur();
                                handleCheckAnswer();
                            }
                            return;
                        }
                    }
                    return;
                }

                if (!actionBtn.disabled) {
                    event.preventDefault();
                    event.stopPropagation();
                    if (isTyping && activeEl) activeEl.blur();
                    handleCheckAnswer();
                }
            }
            return;
        }

        // 2. After Grading (Drawer open: Next Question)
        if (isSpace || isEnter) {
            if (isTyping) return;
            event.preventDefault();
            event.stopPropagation();
            if (activeEl && typeof activeEl.blur === 'function') activeEl.blur();
            sheetContinue?.click();
        }
    });

    window.addEventListener('resize', () => {
        if (resultSheet?.classList.contains('open') || document.body.classList.contains('show-result-mascot')) {
            positionResultPresentation();
        }
    });

    // ================= VIEW ROUTING (?view=review vs New Quiz) =================
    let preloadedReview = null;
    const reviewDataEl = document.getElementById('latest-review-data');
    if (reviewDataEl && reviewDataEl.textContent.trim()) {
        try {
            preloadedReview = JSON.parse(reviewDataEl.textContent);
            if (typeof preloadedReview === 'string') {
                preloadedReview = JSON.parse(preloadedReview);
            }
        } catch (e) {
            console.error('Failed to parse preloaded review data:', e);
        }
    }

    if (isReviewMode) {
        isQuizCompleted = true;
        if (dropdownWrap) {
            dropdownWrap.style.display = 'none';
            dropdownWrap.hidden = true;
        }
        // Hide quiz controls immediately
        actionBtn.classList.add('is-hidden');
        actionBtn.style.display = 'none';
        if (progressFill && progressFill.parentElement) {
            progressFill.parentElement.style.display = 'none';
        }
        setText('quiz-progress-label', '');

        if (preloadedReview && Array.isArray(preloadedReview.review_items) && preloadedReview.review_items.length > 0) {
            serverReviewData = preloadedReview;

            if (backToScoreBtn) {
                backToScoreBtn.style.display = 'none';
            }

            // Prepare saved-result presentation data without displaying it over the review modal.
            setResultPresentation(preloadedReview);
            document.body.classList.remove('show-result-mascot');

            buildReviewModal();
            if (reviewModal) {
                reviewModal.hidden = false;
                reviewModal.classList.add('is-active');
                document.body.classList.add('modal-open');
            }
        } else {
            wrap.innerHTML = `
                <div style="text-align: center; padding: 60px 20px;">
                    <div style="font-size: 2.5rem; margin-bottom: 12px;">📋</div>
                    <h2 style="margin: 0 0 8px; font-size: 1.35rem; color: var(--ink);">Review Unavailable</h2>
                    <p style="margin: 0 0 24px; color: var(--muted); font-size: 0.95rem;">
                        Detailed answer breakdown is not available for this attempt.
                    </p>
                    <a href="${chapterReviewUrl || '../review/'}" class="btn btn-secondary" style="min-height: 44px; display: inline-flex; align-items: center;">
                        &larr; Back to Chapter Review
                    </a>
                </div>
            `;
        }
    } else if (quiz.questions && quiz.questions.length > 0) {
        document.body.classList.remove('show-result-mascot');
        renderQuestion();
    }
});