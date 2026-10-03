/**
 * Course Forge Minigame — Developer Sandbox Controller.
 *
 * Implements developer testing controls:
 * - Resource injection (+25 Wood, +10 Crystals).
 * - Full sandbox reset (scoped to studyquest:forge:sandbox).
 * - Simulated Course Completion (tests pause, getSummary, celebration card).
 * - Pause / Resume and Mute toggles.
 * - Real-time state inspector.
 * - Complete teardown and memory cleanup.
 */
(function (window) {
    'use strict';

    function initSandbox() {
        var panel = document.querySelector('[data-sandbox-controls]');
        if (!panel) return;

        var resetBtn = panel.querySelector('[data-sandbox-reset]');
        var addWoodBtn = panel.querySelector('[data-sandbox-add-wood]');
        var addCrystalsBtn = panel.querySelector('[data-sandbox-add-crystals]');
        var simulateReadyBtn = panel.querySelector('[data-sandbox-simulate-ready]');
        var pauseResumeBtn = panel.querySelector('[data-sandbox-pause-resume]');
        var toggleMuteBtn = panel.querySelector('[data-sandbox-toggle-mute]');
        var showSummaryBtn = panel.querySelector('[data-sandbox-show-summary]');
        var summaryOutput = panel.querySelector('[data-sandbox-summary-output]');
        var mockReadySlot = document.querySelector('[data-sandbox-ready-slot]');

        var isPaused = false;

        function getCampInstance() {
            return window.CourseForgeCampInstance || null;
        }

        // 1. Reset Sandbox
        if (resetBtn) {
            resetBtn.addEventListener('click', function () {
                var camp = getCampInstance();
                if (camp && camp.getState()) {
                    try { camp.getState().clear(); } catch (e) {}
                }
                try {
                    window.sessionStorage.removeItem('studyquest:forge:sandbox');
                } catch (e) {}

                if (window.CourseForgeCamp && typeof window.CourseForgeCamp.destroy === 'function') {
                    window.CourseForgeCamp.destroy();
                }

                if (mockReadySlot) mockReadySlot.innerHTML = '';
                if (summaryOutput) {
                    summaryOutput.textContent = '';
                    summaryOutput.hidden = true;
                }
                isPaused = false;
                if (pauseResumeBtn) pauseResumeBtn.textContent = '⏸️ Pause Camp';

                if (window.CourseForgeCamp && typeof window.CourseForgeCamp.init === 'function') {
                    window.CourseForgeCampInstance = window.CourseForgeCamp.init();
                }
            });
        }

        // 2. Add 25 Wood
        if (addWoodBtn) {
            addWoodBtn.addEventListener('click', function () {
                var camp = getCampInstance();
                if (camp && camp.getState()) {
                    camp.getState().addWood(25);
                }
            });
        }

        // 3. Add 10 Study Crystals
        if (addCrystalsBtn) {
            addCrystalsBtn.addEventListener('click', function () {
                var camp = getCampInstance();
                if (camp && camp.getState()) {
                    camp.getState().addCrystals(10);
                }
            });
        }

        // 4. Simulate Course Completion
        if (simulateReadyBtn) {
            simulateReadyBtn.addEventListener('click', function () {
                if (window.CourseForgeCamp && typeof window.CourseForgeCamp.pause === 'function') {
                    window.CourseForgeCamp.pause();
                    isPaused = true;
                    if (pauseResumeBtn) pauseResumeBtn.textContent = '▶️ Resume Camp';
                }

                var summary = window.CourseForgeCamp ? window.CourseForgeCamp.getSummary() : null;

                if (mockReadySlot) {
                    mockReadySlot.innerHTML = '';
                    var readyCard = document.createElement('div');
                    readyCard.className = 'card forge-status-card forge-mock-ready-card';

                    var summaryText = summary
                        ? 'While waiting, you gathered <strong>' + summary.wood + '</strong> Wood and <strong>' + summary.crystals + '</strong> Study Crystals, and upgraded your camp <strong>' + summary.upgradesPurchased + '</strong> times!'
                        : 'StudyQuest Camp summary generated.';

                    readyCard.innerHTML = [
                        '<div class="forge-status-head">',
                        '  <span class="forge-status-dot is-ready" aria-hidden="true"></span>',
                        '  <div>',
                        '    <strong>[Simulated] Your Course is Ready</strong>',
                        '    <p class="forge-status-message">StudyQuest finished building your course (Sandbox Simulation — No real course modified).</p>',
                        '  </div>',
                        '</div>',
                        '<div class="forge-camp-summary-card">',
                        '  <span class="summary-badge">⛺ Camp Expedition Complete</span>',
                        '  <p class="summary-stats">' + summaryText + '</p>',
                        '</div>',
                        '<div class="forge-actions">',
                        '  <button type="button" class="btn btn-primary" data-sandbox-dismiss-ready>Continue Sandbox</button>',
                        '  <a href="/" class="btn btn-ghost">Back to Dashboard</a>',
                        '</div>'
                    ].join('');

                    var dismissBtn = readyCard.querySelector('[data-sandbox-dismiss-ready]');
                    if (dismissBtn) {
                        dismissBtn.addEventListener('click', function () {
                            mockReadySlot.innerHTML = '';
                            if (window.CourseForgeCamp && typeof window.CourseForgeCamp.resume === 'function') {
                                window.CourseForgeCamp.resume();
                                isPaused = false;
                                if (pauseResumeBtn) pauseResumeBtn.textContent = '⏸️ Pause Camp';
                            }
                        });
                    }

                    mockReadySlot.appendChild(readyCard);
                    readyCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                }
            });
        }

        // 5. Pause or Resume
        if (pauseResumeBtn) {
            pauseResumeBtn.addEventListener('click', function () {
                if (!window.CourseForgeCamp) return;
                if (isPaused) {
                    window.CourseForgeCamp.resume();
                    isPaused = false;
                    pauseResumeBtn.textContent = '⏸️ Pause Camp';
                } else {
                    window.CourseForgeCamp.pause();
                    isPaused = true;
                    pauseResumeBtn.textContent = '▶️ Resume Camp';
                }
            });
        }

        // 6. Toggle Mute
        if (toggleMuteBtn) {
            toggleMuteBtn.addEventListener('click', function () {
                var campMuteBtn = document.querySelector('[data-forge-mute]');
                if (campMuteBtn) {
                    campMuteBtn.click();
                    toggleMuteBtn.textContent = campMuteBtn.textContent.includes('Unmute') ? '🔇 Unmute' : '🔊 Mute';
                }
            });
        }

        // 7. Inspect Summary
        if (showSummaryBtn && summaryOutput) {
            showSummaryBtn.addEventListener('click', function () {
                var s = window.CourseForgeCamp ? window.CourseForgeCamp.getSummary() : null;
                if (!s) {
                    summaryOutput.textContent = '// Camp summary unavailable';
                    summaryOutput.hidden = false;
                    return;
                }
                summaryOutput.textContent = JSON.stringify(s, null, 2);
                summaryOutput.hidden = false;
            });
        }

        // 8. Lifecycle cleanup
        window.addEventListener('pagehide', function () {
            if (window.CourseForgeCamp && typeof window.CourseForgeCamp.destroy === 'function') {
                window.CourseForgeCamp.destroy();
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initSandbox);
    } else {
        initSandbox();
    }
})(window);
