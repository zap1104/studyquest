/**
 * StudyQuest — Hidden Developer Sandbox Unlock.
 *
 * Implements:
 * - 5-click easter-egg shortcut on the StudyQuest logo within 3 seconds.
 * - Non-destructive: preserves normal single-click navigation and keyboard access.
 * - Accessible aria-live confirmation and subtle visual toast.
 * - Redirects authorized development testers to /courses/forge/sandbox/.
 */
(function (window) {
    'use strict';

    var REQUIRED_CLICKS = 5;
    var TIME_WINDOW_MS = 3000;

    function initLogoShortcut() {
        var brandLink = document.querySelector('[data-brand-link]') || document.querySelector('a.brand');
        if (!brandLink) return;

        var sandboxUrl = brandLink.getAttribute('data-sandbox-url') || '/courses/forge/sandbox/';
        var announcer = document.getElementById('sandbox-announcer');
        var toast = document.getElementById('sandbox-toast');

        var clickCount = 0;
        var firstClickTime = 0;
        var resetTimer = null;
        var singleClickTimer = null;

        function announceUnlock() {
            var message = 'Developer camp unlocked.';
            if (announcer) {
                announcer.textContent = message;
            }
            if (toast) {
                toast.textContent = '⛺ ' + message;
                toast.hidden = false;
                toast.classList.add('is-visible');
            }

            setTimeout(function () {
                window.location.href = sandboxUrl;
            }, 450);
        }

        brandLink.addEventListener('click', function (e) {
            // 1. Accessibility: Keyboard Enter/Space activation produces e.detail === 0.
            // Never intercept keyboard navigation.
            if (e.detail === 0) {
                return;
            }

            var now = Date.now();

            // 2. Rolling time window check
            if (now - firstClickTime > TIME_WINDOW_MS) {
                clickCount = 0;
                firstClickTime = now;
            }

            clickCount += 1;

            var isDashboard = window.location.pathname === '/' || window.location.pathname === '/dashboard/';

            if (isDashboard) {
                // On dashboard: prevent unwanted full reloads on repeated clicks
                e.preventDefault();

                if (clickCount === 1) {
                    // Normal single click on dashboard scrolls smoothly to top
                    singleClickTimer = setTimeout(function () {
                        window.scrollTo({ top: 0, behavior: 'smooth' });
                    }, 350);
                } else {
                    if (singleClickTimer) {
                        clearTimeout(singleClickTimer);
                        singleClickTimer = null;
                    }
                }

                if (clickCount >= REQUIRED_CLICKS) {
                    clickCount = 0;
                    if (resetTimer) clearTimeout(resetTimer);
                    announceUnlock();
                    return;
                }
            } else {
                // Not on dashboard:
                // Click 1 performs standard anchor navigation to dashboard
                if (clickCount > 1) {
                    e.preventDefault();
                }

                if (clickCount >= REQUIRED_CLICKS) {
                    e.preventDefault();
                    e.stopPropagation();
                    clickCount = 0;
                    if (resetTimer) clearTimeout(resetTimer);
                    announceUnlock();
                    return;
                }
            }

            // Reset counter after window expires
            if (resetTimer) clearTimeout(resetTimer);
            resetTimer = setTimeout(function () {
                clickCount = 0;
            }, TIME_WINDOW_MS);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initLogoShortcut);
    } else {
        initLogoShortcut();
    }
})(window);
