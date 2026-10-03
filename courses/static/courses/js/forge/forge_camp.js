/**
 * Course Forge Minigame — "StudyQuest Camp" Main Controller.
 *
 * Coordinates:
 * - Web Audio API procedural sound engine (chop, strike, pickup, fanfare).
 * - Canvas RAF game loop with velocity physics and entity rendering.
 * - Mobile virtual joystick and touch controls.
 * - Accessible "Wait quietly" toggle and Upgrade Drawer UI.
 * - Public API: window.CourseForgeCamp (start, pause, resume, destroy, getSummary).
 */
(function (window) {
    'use strict';

    // --- Web Audio Procedural Sound Synthesizer ---
    function createAudioSystem() {
        var ctx = null;
        var muted = false;

        try {
            muted = window.localStorage && window.localStorage.getItem('studyquest:forge:muted') === '1';
        } catch (e) {}

        function getCtx() {
            if (!ctx) {
                var AudioContextClass = window.AudioContext || window.webkitAudioContext;
                if (AudioContextClass) {
                    ctx = new AudioContextClass();
                }
            }
            if (ctx && ctx.state === 'suspended') {
                ctx.resume();
            }
            return ctx;
        }

        function playTone(freq, type, duration, startVol, endVol) {
            if (muted) return;
            try {
                var actx = getCtx();
                if (!actx) return;
                var osc = actx.createOscillator();
                var gain = actx.createGain();
                osc.type = type || 'sine';
                osc.frequency.setValueAtTime(freq, actx.currentTime);
                gain.gain.setValueAtTime(startVol || 0.12, actx.currentTime);
                gain.gain.exponentialRampToValueAtTime(endVol || 0.001, actx.currentTime + duration);
                osc.connect(gain);
                gain.connect(actx.destination);
                osc.start();
                osc.stop(actx.currentTime + duration);
            } catch (e) {}
        }

        return {
            isMuted: function () { return muted; },
            setMuted: function (val) {
                muted = !!val;
                try {
                    if (window.localStorage) {
                        window.localStorage.setItem('studyquest:forge:muted', muted ? '1' : '0');
                    }
                } catch (e) {}
            },
            toggleMuted: function () {
                this.setMuted(!muted);
                return muted;
            },
            playChop: function () {
                playTone(140, 'triangle', 0.12, 0.18, 0.001);
            },
            playTreeFall: function () {
                playTone(90, 'sine', 0.35, 0.22, 0.001);
            },
            playStrike: function () {
                playTone(320, 'sawtooth', 0.14, 0.15, 0.001);
            },
            playDefeat: function () {
                if (muted) return;
                try {
                    var actx = getCtx();
                    if (!actx) return;
                    var t = actx.currentTime;
                    var notes = [440, 554, 659];
                    notes.forEach(function (f, i) {
                        var osc = actx.createOscillator();
                        var gain = actx.createGain();
                        osc.frequency.setValueAtTime(f, t + i * 0.06);
                        gain.gain.setValueAtTime(0.12, t + i * 0.06);
                        gain.gain.exponentialRampToValueAtTime(0.001, t + i * 0.06 + 0.15);
                        osc.connect(gain);
                        gain.connect(actx.destination);
                        osc.start(t + i * 0.06);
                        osc.stop(t + i * 0.06 + 0.15);
                    });
                } catch (e) {}
            },
            playWoodPickup: function () {
                playTone(520, 'sine', 0.08, 0.14, 0.001);
            },
            playCrystalPickup: function () {
                playTone(880, 'triangle', 0.12, 0.16, 0.001);
            },
            playUpgrade: function () {
                if (muted) return;
                try {
                    var actx = getCtx();
                    if (!actx) return;
                    var t = actx.currentTime;
                    var fanfare = [523.25, 659.25, 783.99, 1046.50];
                    fanfare.forEach(function (f, i) {
                        var osc = actx.createOscillator();
                        var gain = actx.createGain();
                        osc.type = 'triangle';
                        osc.frequency.setValueAtTime(f, t + i * 0.08);
                        gain.gain.setValueAtTime(0.16, t + i * 0.08);
                        gain.gain.exponentialRampToValueAtTime(0.001, t + i * 0.08 + 0.22);
                        osc.connect(gain);
                        gain.connect(actx.destination);
                        osc.start(t + i * 0.08);
                        osc.stop(t + i * 0.08 + 0.22);
                    });
                } catch (e) {}
            },
            playSwing: function () {
                playTone(220, 'sine', 0.07, 0.06, 0.001);
            },
            destroy: function () {
                if (ctx) {
                    try { ctx.close(); } catch (e) {}
                    ctx = null;
                }
            }
        };
    }

    // --- Camp Coordinator ---
    function initCamp() {
        var root = document.querySelector('[data-camp-root]');
        if (!root) return null;

        var statusPanel = document.querySelector('[data-forge-status]');
        var courseId = (root && root.getAttribute('data-course-id')) || (statusPanel && statusPanel.getAttribute('data-course-id')) || 'default';

        var state = window.CourseForgeState.create(courseId);
        var input = window.CourseForgeInput.create();
        var audio = createAudioSystem();
        var camera = window.CourseForgeCamera ? window.CourseForgeCamera.create({
            viewportWidth: 640,
            viewportHeight: 360,
            worldWidth: 1920,
            worldHeight: 1200
        }) : null;
        var entities = window.CourseForgeEntities.createManager(state, audio, camera);
        var plots = window.CourseForgePlots ? window.CourseForgePlots.create(state, audio, entities, camera) : null;

        if (camera && entities.getPlayer()) {
            camera.snapTo(entities.getPlayer().x, entities.getPlayer().y);
        }

        var isRunning = true;
        var isCalmMode = false;
        var rafId = null;
        var lastTime = performance.now();

        // 1. Build Camp DOM Layout inside root
        root.innerHTML = '';
        root.removeAttribute('hidden');

        var container = document.createElement('div');
        container.className = 'forge-camp-container';

        // Camp Header / Bar (Compact Single-Row Toolbar)
        var topBar = document.createElement('div');
        topBar.className = 'forge-camp-topbar';
        topBar.innerHTML = [
            '<div class="forge-camp-title-row">',
            '  <span class="forge-camp-badge">⛺ StudyQuest Camp</span>',
            '  <span class="forge-camp-status-chip" title="Course generation is actively running in background">',
            '    <span class="forge-chip-dot" aria-hidden="true"></span>',
            '    <span>Course generating</span>',
            '  </span>',
            '</div>',
            '<div class="forge-camp-controls-row">',
            '  <button type="button" class="btn btn-ghost btn-sm forge-camp-mute-btn" data-forge-mute title="Toggle Sound (M)">' + (audio.isMuted() ? '🔇 <span class="forge-btn-text">Unmute</span>' : '🔊 <span class="forge-btn-text">Sound</span>') + '</button>',
            '  <button type="button" class="btn btn-ghost btn-sm forge-camp-calm-btn" data-forge-calm title="Wait without minigame">🕊️ <span class="forge-btn-text">Wait Quietly</span></button>',
            '</div>'
        ].join('');
        container.appendChild(topBar);

        // Canvas Viewport Wrap
        var canvasWrap = document.createElement('div');
        canvasWrap.className = 'forge-camp-canvas-wrap';

        var canvas = document.createElement('canvas');
        canvas.className = 'forge-camp-canvas';
        canvas.setAttribute('tabindex', '0');
        canvas.setAttribute('aria-label', 'StudyQuest Camp forest minigame');
        canvasWrap.appendChild(canvas);

        // Mobile On-screen Touch Controls Overlay
        var touchOverlay = document.createElement('div');
        touchOverlay.className = 'forge-touch-overlay';
        touchOverlay.innerHTML = [
            '<div class="forge-joystick-zone" data-joy-zone>',
            '  <div class="forge-joystick-base" data-joy-base>',
            '    <div class="forge-joystick-knob" data-joy-knob></div>',
            '  </div>',
            '</div>',
            '<div class="forge-touch-actions">',
            '  <button type="button" class="forge-touch-btn forge-touch-upgrade" data-touch-upgrade aria-label="Upgrades">🎒</button>',
            '  <button type="button" class="forge-touch-btn forge-touch-action" data-touch-action aria-label="Chop or Attack">🪓 ACTION</button>',
            '</div>'
        ].join('');
        canvasWrap.appendChild(touchOverlay);

        // Upgrades Modal Drawer
        var upgradeModal = document.createElement('div');
        upgradeModal.className = 'forge-upgrade-modal';
        upgradeModal.hidden = true;
        upgradeModal.innerHTML = [
            '<div class="forge-upgrade-card">',
            '  <div class="forge-upgrade-head">',
            '    <h3>Camp Upgrades</h3>',
            '    <button type="button" class="forge-upgrade-close" data-forge-close-upgrades aria-label="Close upgrades">×</button>',
            '  </div>',
            '  <div class="forge-upgrade-list" data-forge-upgrade-list></div>',
            '  <p class="forge-upgrade-note">Upgrades last for this course preparation session.</p>',
            '</div>'
        ].join('');
        canvasWrap.appendChild(upgradeModal);

        // Calm Mode Placeholder
        var calmCard = document.createElement('div');
        calmCard.className = 'forge-calm-card';
        calmCard.hidden = true;
        calmCard.innerHTML = [
            '<div class="forge-calm-content">',
            '  <p class="forge-calm-icon">☕</p>',
            '  <h3>Resting at Camp</h3>',
            '  <p>The minigame is paused. StudyQuest continues preparing your course in the background.</p>',
            '  <button type="button" class="btn btn-secondary btn-sm" data-forge-resume-game>Resume Minigame</button>',
            '</div>'
        ].join('');
        canvasWrap.appendChild(calmCard);

        container.appendChild(canvasWrap);

        // Bottom Controls Hint Bar
        var hintBar = document.createElement('div');
        hintBar.className = 'forge-camp-hintbar';
        hintBar.innerHTML = [
            '<div class="forge-hint-left">',
            '  <span><strong>WASD / Arrows</strong> Move</span> · ',
            '  <span><strong>Space / Click</strong> Action</span> · ',
            '  <span><strong>E / Approach Plot</strong> Build & Upgrade</span>',
            '</div>',
            '<div class="forge-hint-right">',
            '  <span><strong>M</strong> Mute</span>',
            '</div>'
        ].join('');
        container.appendChild(hintBar);

        root.appendChild(container);

        // Renderer
        var renderer = window.CourseForgeRenderer.create(canvas);

        // Bind Touch Inputs
        var joyBase = touchOverlay.querySelector('[data-joy-base]');
        var joyKnob = touchOverlay.querySelector('[data-joy-knob]');
        input.bindJoystickElements(joyBase, joyKnob);

        var touchActionBtn = touchOverlay.querySelector('[data-touch-action]');
        input.bindActionButton(touchActionBtn);
        if (touchActionBtn) {
            touchActionBtn.addEventListener('click', function () {
                if (plots && plots.getActivePromptPlot()) {
                    plots.interact();
                }
            });
        }

        var touchUpgradeBtn = touchOverlay.querySelector('[data-touch-upgrade]');
        if (touchUpgradeBtn) {
            touchUpgradeBtn.addEventListener('click', toggleUpgradesModal);
        }

        // Click canvas to trigger action / build / focus
        canvas.addEventListener('click', function () {
            if (plots && plots.getActivePromptPlot()) {
                if (plots.interact()) {
                    canvas.focus();
                    return;
                }
            }
            input.trigger('action');
            canvas.focus();
        });

        // Wire Upgrades Drawer
        var openUpgradesBtn = topBar.querySelector('[data-forge-open-upgrades]');
        var closeUpgradesBtn = upgradeModal.querySelector('[data-forge-close-upgrades]');
        var upgradeListEl = upgradeModal.querySelector('[data-forge-upgrade-list]');

        function renderUpgradeList() {
            if (!upgradeListEl) return;
            var stateData = state.getData();
            upgradeListEl.innerHTML = '';

            // Section 1: Camp Buildings & Construction Plots
            var plotsList = state.getAllPlots ? state.getAllPlots() : [];
            if (plotsList.length > 0) {
                var plotSecTitle = document.createElement('div');
                plotSecTitle.className = 'forge-upgrade-sec-title';
                plotSecTitle.innerHTML = '<span style="font-size:0.78rem; font-weight:800; color:#38bdf8; text-transform:uppercase; letter-spacing:0.06em;">⛺ Camp Structures & Plots</span>';
                plotSecTitle.style.marginBottom = '6px';
                upgradeListEl.appendChild(plotSecTitle);

                plotsList.forEach(function (plot) {
                    var item = document.createElement('div');
                    item.className = 'forge-upgrade-item' + (plot.isMax ? ' is-max' : '');

                    var costHtml = '';
                    if (plot.isMax) {
                        costHtml = '<span class="badge-max">MAX LEVEL</span>';
                    } else if (plot.cost) {
                        var costs = [];
                        if (plot.cost.wood > 0) {
                            var woodOk = stateData.wood >= plot.cost.wood;
                            costs.push('<span class="' + (woodOk ? 'cost-ok' : 'cost-need') + '">🪵 ' + plot.cost.wood + '</span>');
                        }
                        if (plot.cost.crystals > 0) {
                            var crysOk = stateData.crystals >= plot.cost.crystals;
                            costs.push('<span class="' + (crysOk ? 'cost-ok' : 'cost-need') + '">💎 ' + plot.cost.crystals + '</span>');
                        }
                        costHtml = costs.join(' ');
                    }

                    var actionText = plot.isMax ? 'Max' : (plot.currentLevel === 0 ? 'Build' : 'Upgrade');
                    item.innerHTML = [
                        '<div class="forge-upgrade-info">',
                        '  <div class="forge-upgrade-name">',
                        '    <span class="forge-upg-icon">' + plot.icon + '</span>',
                        '    <strong>' + plot.name + '</strong>',
                        '    <span class="forge-upg-tier">Lv. ' + plot.currentLevel + '/' + plot.maxLevel + '</span>',
                        '  </div>',
                        '  <p class="forge-upgrade-desc">' + plot.description + '</p>',
                        '</div>',
                        '<div class="forge-upgrade-action">',
                        '  <div class="forge-upgrade-cost">' + costHtml + '</div>',
                        '  <button type="button" class="btn btn-sm btn-primary forge-btn-buy" ' + (!plot.canAfford ? 'disabled' : '') + '>',
                        actionText,
                        '  </button>',
                        '</div>'
                    ].join('');

                    var buyBtn = item.querySelector('.forge-btn-buy');
                    if (buyBtn && !plot.isMax) {
                        buyBtn.addEventListener('click', function () {
                            if (plots) {
                                plots.interact(plot.id);
                            } else {
                                state.buyPlotUpgrade(plot.id);
                            }
                            renderUpgradeList();
                        });
                    }

                    upgradeListEl.appendChild(item);
                });
            }

            // Section 2: Explorer Equipment & Tools
            var list = state.getAllUpgrades();
            var equipSecTitle = document.createElement('div');
            equipSecTitle.className = 'forge-upgrade-sec-title';
            equipSecTitle.innerHTML = '<span style="font-size:0.78rem; font-weight:800; color:#fbbf24; text-transform:uppercase; letter-spacing:0.06em;">🎒 Explorer Equipment & Tools</span>';
            equipSecTitle.style.marginTop = '12px';
            equipSecTitle.style.marginBottom = '6px';
            upgradeListEl.appendChild(equipSecTitle);

            list.forEach(function (upg) {
                var item = document.createElement('div');
                item.className = 'forge-upgrade-item' + (upg.isMax ? ' is-max' : '');

                var costHtml = '';
                if (upg.isMax) {
                    costHtml = '<span class="badge-max">MAX LEVEL</span>';
                } else if (upg.cost) {
                    var costs = [];
                    if (upg.cost.wood > 0) {
                        var woodOk = stateData.wood >= upg.cost.wood;
                        costs.push('<span class="' + (woodOk ? 'cost-ok' : 'cost-need') + '">🪵 ' + upg.cost.wood + '</span>');
                    }
                    if (upg.cost.crystals > 0) {
                        var crysOk = stateData.crystals >= upg.cost.crystals;
                        costs.push('<span class="' + (crysOk ? 'cost-ok' : 'cost-need') + '">💎 ' + upg.cost.crystals + '</span>');
                    }
                    costHtml = costs.join(' ');
                }

                item.innerHTML = [
                    '<div class="forge-upgrade-info">',
                    '  <div class="forge-upgrade-name">',
                    '    <span class="forge-upg-icon">' + upg.icon + '</span>',
                    '    <strong>' + upg.name + '</strong>',
                    '    <span class="forge-upg-tier">Lv. ' + upg.currentLevel + '/' + upg.maxLevel + '</span>',
                    '  </div>',
                    '  <p class="forge-upgrade-desc">' + upg.description + '</p>',
                    '</div>',
                    '<div class="forge-upgrade-action">',
                    '  <div class="forge-upgrade-cost">' + costHtml + '</div>',
                    '  <button type="button" class="btn btn-sm btn-primary forge-btn-buy" ' + (!upg.canAfford ? 'disabled' : '') + '>',
                    upg.isMax ? 'Max' : 'Upgrade',
                    '  </button>',
                    '</div>'
                ].join('');

                var buyBtn = item.querySelector('.forge-btn-buy');
                if (buyBtn && !upg.isMax) {
                    buyBtn.addEventListener('click', function () {
                        if (state.buyUpgrade(upg.id)) {
                            audio.playUpgrade();
                            var p = entities.getPlayer();
                            entities.spawnFloatingText('Upgrade!', p ? p.x : 960, p ? p.y - 30 : 570, '#38bdf8');
                            renderUpgradeList();
                        }
                    });
                }

                upgradeListEl.appendChild(item);
            });
        }

        function toggleUpgradesModal() {
            var isHidden = upgradeModal.hidden;
            upgradeModal.hidden = !isHidden;
            if (!upgradeModal.hidden) {
                renderUpgradeList();
            }
        }

        if (openUpgradesBtn) openUpgradesBtn.addEventListener('click', toggleUpgradesModal);
        if (closeUpgradesBtn) closeUpgradesBtn.addEventListener('click', toggleUpgradesModal);

        // Wire Mute Button
        var muteBtn = topBar.querySelector('[data-forge-mute]');
        function updateMuteBtn() {
            if (muteBtn) {
                muteBtn.innerHTML = audio.isMuted() ? '🔇 <span class="forge-btn-text">Unmute</span>' : '🔊 <span class="forge-btn-text">Sound</span>';
            }
        }
        if (muteBtn) {
            muteBtn.addEventListener('click', function () {
                audio.toggleMuted();
                updateMuteBtn();
            });
        }

        // Wire Calm Mode Toggle
        var calmBtn = topBar.querySelector('[data-forge-calm]');
        var resumeBtn = calmCard.querySelector('[data-forge-resume-game]');

        function setCalmMode(calm) {
            isCalmMode = calm;
            if (calm) {
                calmCard.hidden = false;
                canvas.style.display = 'none';
                touchOverlay.style.display = 'none';
                if (calmBtn) calmBtn.innerHTML = '🎮 <span class="forge-btn-text">Play Minigame</span>';
            } else {
                calmCard.hidden = true;
                canvas.style.display = 'block';
                touchOverlay.style.display = '';
                if (calmBtn) calmBtn.innerHTML = '🕊️ <span class="forge-btn-text">Wait Quietly</span>';
                renderer.resize();
            }
        }

        if (calmBtn) {
            calmBtn.addEventListener('click', function () {
                setCalmMode(!isCalmMode);
            });
        }
        if (resumeBtn) {
            resumeBtn.addEventListener('click', function () {
                setCalmMode(false);
            });
        }

        // Subscribe state changes to re-render upgrade list if open
        state.subscribe(function () {
            if (!upgradeModal.hidden) {
                renderUpgradeList();
            }
        });

        // Main RAF Loop
        function loop(timestamp) {
            if (!isRunning) return;

            var dt = (timestamp - lastTime) / 1000;
            lastTime = timestamp;
            if (dt > 0.1) dt = 0.1; // clamp lag spikes

            // Consume input edge triggers
            if (input.consume('upgrade')) {
                if (plots && plots.getActivePromptPlot()) {
                    plots.interact();
                } else {
                    toggleUpgradesModal();
                }
            }
            if (input.consume('mute')) {
                audio.toggleMuted();
                updateMuteBtn();
            }
            if (input.consume('pause')) {
                setCalmMode(!isCalmMode);
            }

            var actionPressed = input.consume('action');
            var moveVec = input.getMoveVector();

            if (!isCalmMode) {
                entities.update(dt, moveVec, actionPressed);
                var p = entities.getPlayer();
                if (plots && p) {
                    plots.update(dt, p.x, p.y);
                    if (touchActionBtn) {
                        var activeP = plots.getActivePromptPlot();
                        if (activeP) {
                            touchActionBtn.textContent = (activeP.currentLevel === 0 ? '🔨 BUILD' : '⬆️ UPGRADE');
                        } else {
                            touchActionBtn.textContent = '🪓 ACTION';
                        }
                    }
                }
                if (camera) {
                    camera.update(dt, entities.getPlayer());
                }
                renderer.render(dt, entities, state, camera, plots);
            }

            rafId = requestAnimationFrame(loop);
        }

        rafId = requestAnimationFrame(loop);

        // Visibility handling
        function onVisibilityChange() {
            if (document.visibilityState === 'hidden') {
                if (rafId) {
                    cancelAnimationFrame(rafId);
                    rafId = null;
                }
            } else if (isRunning && !rafId) {
                lastTime = performance.now();
                rafId = requestAnimationFrame(loop);
            }
        }
        document.addEventListener('visibilitychange', onVisibilityChange);

        function pause() {
            isRunning = false;
            if (rafId) {
                cancelAnimationFrame(rafId);
                rafId = null;
            }
        }

        function resume() {
            if (!isRunning) {
                isRunning = true;
                lastTime = performance.now();
                rafId = requestAnimationFrame(loop);
            }
        }

        function destroy() {
            pause();
            document.removeEventListener('visibilitychange', onVisibilityChange);
            input.destroy();
            renderer.destroy();
            audio.destroy();
            root.innerHTML = '';
        }

        return {
            pause: pause,
            resume: resume,
            destroy: destroy,
            getState: function () { return state; },
            getCamera: function () { return camera; },
            getEntities: function () { return entities; },
            getSummary: function () { return state.getSummary(); }
        };
    }

    // Auto-mount when DOM is ready
    function autoStart() {
        if (window.CourseForgeCampInstance) return;
        try {
            window.CourseForgeCampInstance = initCamp();
        } catch (e) {
            console.error('[CourseForgeCamp] Failed to initialize:', e);
        }
    }

    window.CourseForgeCamp = {
        init: initCamp,
        pause: function () {
            if (window.CourseForgeCampInstance) window.CourseForgeCampInstance.pause();
        },
        resume: function () {
            if (window.CourseForgeCampInstance) window.CourseForgeCampInstance.resume();
        },
        destroy: function () {
            if (window.CourseForgeCampInstance) {
                window.CourseForgeCampInstance.destroy();
                window.CourseForgeCampInstance = null;
            }
        },
        getSummary: function () {
            if (window.CourseForgeCampInstance) {
                return window.CourseForgeCampInstance.getSummary();
            }
            return null;
        },
        getCamera: function () {
            if (window.CourseForgeCampInstance) {
                return window.CourseForgeCampInstance.getCamera();
            }
            return null;
        }
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', autoStart);
    } else {
        autoStart();
    }
})(window);
