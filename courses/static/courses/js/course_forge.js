/**
 * Course Forge — generation status polling.
 *
 * Owns ONE responsibility: keep the page's status text in sync with the
 * server, and hand control to the Course Forge minigame when generation ends.
 *
 * The minigame is not loaded here. It mounts into [data-camp-root] in a later
 * sprint and is strictly optional: if it never loads, this page still works.
 */
(function () {
    'use strict';

    var POLL_INITIAL_MS = 2000;
    var POLL_MAX_MS = 5000;
    var MAX_CONSECUTIVE_FAILURES = 5;

    function init() {
        var panel = document.querySelector('[data-forge-status]');
        if (!panel) return;

        var statusUrl = panel.getAttribute('data-status-url');
        var readyUrl = panel.getAttribute('data-ready-url');
        var labelEl = panel.querySelector('[data-forge-status-label]');
        var messageEl = panel.querySelector('[data-forge-message]');
        var dotEl = panel.querySelector('[data-forge-dot]');
        var refreshBtn = panel.querySelector('[data-forge-refresh]');

        var timer = null;
        var delay = POLL_INITIAL_MS;
        var failures = 0;
        var stopped = false;
        var controller = null;

        function stop() {
            stopped = true;
            if (timer) {
                clearTimeout(timer);
                timer = null;
            }
            if (controller) {
                controller.abort();
                controller = null;
            }
        }

        function setMessage(text) {
            if (messageEl && messageEl.textContent.trim() !== text.trim()) {
                messageEl.textContent = text;
            }
        }

        function updateTimeline(message, isTerminalReady) {
            var timeline = panel.querySelector('[data-forge-timeline]');
            if (!timeline) return;

            var items = timeline.querySelectorAll('.forge-timeline-item');
            if (!items.length) return;

            if (isTerminalReady) {
                for (var i = 0; i < items.length; i++) {
                    var it = items[i];
                    it.classList.remove('is-active', 'is-pending');
                    it.classList.add('is-done');
                    var m = it.querySelector('.timeline-marker');
                    if (m) {
                        m.className = 'timeline-marker';
                        m.textContent = '✓';
                    }
                }
                return;
            }

            var msg = (message || '').toLowerCase();
            var activeIdx = 1;
            if (msg.indexOf('lesson') !== -1 || msg.indexOf('chapter') !== -1 || msg.indexOf('content') !== -1) {
                activeIdx = 1;
            } else if (msg.indexOf('activit') !== -1 || msg.indexOf('quiz') !== -1 || msg.indexOf('check') !== -1) {
                activeIdx = 2;
            } else if (msg.indexOf('final') !== -1 || msg.indexOf('saving') !== -1 || msg.indexOf('journey') !== -1) {
                activeIdx = 3;
            }

            for (var j = 0; j < items.length; j++) {
                var item = items[j];
                var marker = item.querySelector('.timeline-marker');
                item.classList.remove('is-done', 'is-active', 'is-pending');
                if (j < activeIdx) {
                    item.classList.add('is-done');
                    if (marker) {
                        marker.className = 'timeline-marker';
                        marker.textContent = '✓';
                    }
                } else if (j === activeIdx) {
                    item.classList.add('is-active');
                    if (marker) {
                        marker.className = 'timeline-marker timeline-pulse';
                        marker.textContent = '●';
                    }
                } else {
                    item.classList.add('is-pending');
                    if (marker) {
                        marker.className = 'timeline-marker';
                        marker.textContent = '○';
                    }
                }
            }
        }

        /** Called when generation reaches a terminal state. */
        function onFinished(payload) {
            stop();

            // Freeze the minigame before changing anything visual. `pause()`
            // cancels the animation frame AND disables game input, so the
            // learner cannot keep moving, chopping or buying upgrades behind
            // the Course Ready overlay -- and nothing stale is left queued.
            var summary = null;
            if (window.CourseForgeCamp) {
                try {
                    if (typeof window.CourseForgeCamp.getSummary === 'function') {
                        summary = window.CourseForgeCamp.getSummary();
                    }
                } catch (e) { /* a summary must never block readiness */ }
                try {
                    if (typeof window.CourseForgeCamp.pause === 'function') {
                        window.CourseForgeCamp.pause();
                    }
                } catch (e) { /* never block readiness */ }
            }

            if (payload.status === 'active') {
                updateTimeline('', true);
                if (labelEl) labelEl.textContent = 'Your course is ready';
                if (dotEl) dotEl.classList.add('is-ready');
                setMessage('StudyQuest finished building your course.');

                var actions = panel.querySelector('[data-forge-processing-actions]');
                if (actions) actions.remove();

                // Session summary is cosmetic only. It never awards XP, credit,
                // topic evidence or any other academic state.
                if (summary && (summary.wood > 0 || summary.crystals > 0 || summary.treesChopped > 0 || summary.enemiesDefeated > 0)) {
                    var summaryNote = document.createElement('div');
                    summaryNote.className = 'forge-camp-summary-card';
                    summaryNote.innerHTML = '<span class="summary-badge">⛺ Camp Expedition Complete</span>' +
                        '<p class="summary-stats">While waiting, you gathered <strong>' + summary.wood + '</strong> Wood and <strong>' + summary.crystals + '</strong> Study Crystals, and upgraded your camp <strong>' + summary.upgradesPurchased + '</strong> times!</p>';
                    panel.appendChild(summaryNote);
                }

                var ready = document.createElement('div');
                ready.className = 'forge-actions forge-ready-actions';
                var link = document.createElement('a');
                link.className = 'btn btn-primary';
                link.href = payload.ready_url || readyUrl;
                link.textContent = 'Enter Course';
                ready.appendChild(link);
                panel.appendChild(ready);

                // Give the learner a beat to read the outcome, then offer the
                // destination. Manual entry stays available the whole time.
                setTimeout(function () {
                    if (!stopped && document.visibilityState === 'visible') {
                        window.location.href = link.href;
                    }
                }, 3000);
                return;
            }

            // Failed.
            if (labelEl) labelEl.textContent = 'Generation could not be completed';
            if (dotEl) dotEl.classList.add('is-failed');
            setMessage(payload.message || 'StudyQuest could not finish this course.');

            var actionsEl = panel.querySelector('[data-forge-processing-actions]');
            if (actionsEl) actionsEl.remove();

            var failActions = document.createElement('div');
            failActions.className = 'forge-actions';

            if (payload.retry_url) {
                var form = document.createElement('form');
                form.method = 'post';
                form.action = payload.retry_url;
                form.style.margin = '0';
                var token = document.querySelector('input[name=csrfmiddlewaretoken]');
                if (token) {
                    var hidden = document.createElement('input');
                    hidden.type = 'hidden';
                    hidden.name = 'csrfmiddlewaretoken';
                    hidden.value = token.value;
                    form.appendChild(hidden);
                }
                var retry = document.createElement('button');
                retry.type = 'submit';
                retry.className = 'btn btn-primary';
                retry.textContent = 'Try Again';
                form.appendChild(retry);
                failActions.appendChild(form);
            }

            var back = document.createElement('a');
            back.className = 'btn btn-ghost';
            back.href = '/';
            back.textContent = 'Back to Dashboard';
            failActions.appendChild(back);

            panel.appendChild(failActions);
        }

        function poll() {
            if (stopped) return;

            controller = new AbortController();

            fetch(statusUrl, {
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
                signal: controller.signal,
                credentials: 'same-origin'
            })
                .then(function (resp) {
                    if (!resp.ok) throw new Error('status ' + resp.status);
                    return resp.json();
                })
                .then(function (payload) {
                    failures = 0;
                    delay = POLL_INITIAL_MS;

                    if (payload.status === 'active' || payload.status === 'failed') {
                        onFinished(payload);
                        return;
                    }

                    if (labelEl && payload.status === 'processing') {
                        labelEl.textContent = payload.worker_waiting
                            ? 'Waiting for the background worker'
                            : 'StudyQuest is preparing this course';
                    }
                    if (messageEl && payload.message) setMessage(payload.message);
                    updateTimeline(payload.message, false);

                    // Back off gradually so a long generation is not a hot loop.
                    delay = Math.min(POLL_MAX_MS, delay + 500);
                    timer = setTimeout(poll, delay);
                })
                .catch(function (err) {
                    if (err && err.name === 'AbortError') return;
                    failures += 1;
                    if (failures >= MAX_CONSECUTIVE_FAILURES) {
                        setMessage(
                            'Lost contact with StudyQuest. Refresh this page to check progress.'
                        );
                        if (refreshBtn) refreshBtn.hidden = false;
                        stop();
                        return;
                    }
                    timer = setTimeout(poll, POLL_MAX_MS);
                });
        }

        if (refreshBtn) {
            refreshBtn.addEventListener('click', function () {
                stop();
                stopped = false;
                failures = 0;
                delay = POLL_INITIAL_MS;
                poll();
            });
        }

        // Stop polling while the tab is hidden; resume when it returns.
        document.addEventListener('visibilitychange', function () {
            if (document.visibilityState === 'hidden') {
                if (timer) { clearTimeout(timer); timer = null; }
            } else if (!stopped && !timer) {
                poll();
            }
        });

        // Never leave a timer running after navigation.
        window.addEventListener('pagehide', stop);
        window.addEventListener('beforeunload', stop);

        var mobileDetails = panel.querySelector('.forge-mobile-details');
        if (mobileDetails && window.innerWidth < 900) {
            mobileDetails.removeAttribute('open');
        }

        if (panel.getAttribute('data-initial-status') === 'processing') {
            poll();
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();