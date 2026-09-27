/**
 * Study Focus — terminology catalogue controller (DF-2 drill-down).
 *
 * One dialog, one visible hierarchy level, one scroll owner.
 *
 *   Academic Areas  ->  Subjects  ->  Topics
 *                  \->  Search results (unified, bypasses the hierarchy)
 *
 * Design notes:
 * - `activeTopics` (owned by the Study Focus modal) is the single authoritative
 *   selection. This module never keeps a second copy; `catalogueState.selected`
 *   is a Set of the same label strings, reconciled against `activeTopics` on
 *   every commit and on every open.
 * - Only label strings ever reach the server. The catalogue's stable `key`
 *   values are retained in memory for identity/focus restoration only, so the
 *   POST contract (`topic_names` = comma-joined labels) is unchanged.
 * - No nested scroll regions: `.focus-catalogue-body` is the only element with
 *   `overflow-y: auto`. Every other container is `overflow: visible`.
 */
(function () {
    'use strict';

    var MAX_TOPICS = 6;

    var LEVELS = ['areas', 'subjects', 'topics', 'search'];

    var LEVEL_TITLES = {
        areas: 'Browse by academic area',
        subjects: 'Choose a subject',
        topics: 'Choose the topics that need more attention',
        search: 'Search results'
    };

    function createController(config) {
        var root = config.root;
        var data = config.data;

        var areas = (data && data.areas) || [];

        // ---- DOM references -------------------------------------------------
        var el = {
            dialog: config.dialog,
            body: root.querySelector('[data-catalogue-body]'),
            heading: root.querySelector('[data-catalogue-heading]'),
            subheading: root.querySelector('[data-catalogue-subheading]'),
            breadcrumb: root.querySelector('[data-catalogue-breadcrumb]'),
            search: root.querySelector('[data-catalogue-search]'),
            clearSearch: root.querySelector('[data-catalogue-clear-search]'),
            popular: root.querySelector('[data-catalogue-popular]'),
            list: root.querySelector('[data-catalogue-list]'),
            subjectAction: root.querySelector('[data-catalogue-subject-action]'),
            subjectActionBtn: root.querySelector('[data-catalogue-subject-action-btn]'),
            tray: root.querySelector('[data-catalogue-tray]'),
            trayStatus: root.querySelector('[data-catalogue-tray-status]'),
            trayChips: root.querySelector('[data-catalogue-tray-chips]'),
            trayClear: root.querySelector('[data-catalogue-tray-clear]'),
            capMessage: root.querySelector('[data-catalogue-cap-message]'),
            footerPrimary: config.footerPrimary,
            footerSecondary: config.footerSecondary
        };

        // ---- State ----------------------------------------------------------
        var state = {
            level: 'areas',
            areaKey: null,
            subjectKey: null,
            searchQuery: '',
            selected: new Set(),
            returnFocusKey: null
        };

        // ---- Catalogue indexes ---------------------------------------------
        var areaByKey = {};
        var subjectByKey = {};
        var subjectArea = {};   // subjectKey -> area

        areas.forEach(function (area) {
            areaByKey[area.key] = area;
            (area.subjects || []).forEach(function (subject) {
                subjectByKey[subject.key] = subject;
                subjectArea[subject.key] = area;
            });
        });

        // ---- Helpers --------------------------------------------------------
        function escapeHtml(str) {
            return String(str)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;');
        }

        function norm(value) {
            return String(value || '').toLowerCase();
        }

        /** Every searchable string belonging to a subject. */
        function subjectHaystack(subject) {
            var area = subjectArea[subject.key];
            var parts = [subject.label, subject.code, area ? area.label : ''];
            return parts.concat(subject.aliases || []).map(norm);
        }

        /** Every searchable string belonging to a topic. */
        function topicHaystack(topic, subject) {
            var area = subjectArea[subject.key];
            var parts = [
                topic.label,
                subject.label,
                subject.code,
                area ? area.label : ''
            ];
            return parts.concat(topic.aliases || []).map(norm);
        }

        function matches(haystack, query) {
            for (var i = 0; i < haystack.length; i++) {
                if (haystack[i].indexOf(query) !== -1) return true;
            }
            return false;
        }

        function topicCount(area) {
            return (area.subjects || []).reduce(function (sum, subject) {
                return sum + (subject.topics || []).length;
            }, 0);
        }

        function getSelected() {
            return Array.from(state.selected);
        }

        // ---- Rendering primitives ------------------------------------------
        /** A real <button> row. Never a div with role="button". */
        function makeRow(opts) {
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'focus-catalogue-row' + (opts.modifier || '');
            btn.setAttribute('data-key', opts.key || '');

            var icon = document.createElement('span');
            icon.className = 'focus-catalogue-row-icon';
            icon.setAttribute('aria-hidden', 'true');
            icon.textContent = opts.icon || '';
            btn.appendChild(icon);

            var main = document.createElement('span');
            main.className = 'focus-catalogue-row-main';

            var label = document.createElement('span');
            label.className = 'focus-catalogue-row-label';
            label.textContent = opts.label;
            main.appendChild(label);

            if (opts.meta) {
                var meta = document.createElement('span');
                meta.className = 'focus-catalogue-row-meta';
                meta.textContent = opts.meta;
                main.appendChild(meta);
            }

            btn.appendChild(main);

            if (opts.tag) {
                var tag = document.createElement('span');
                tag.className = 'focus-catalogue-row-tag';
                tag.textContent = opts.tag;
                btn.appendChild(tag);
            }

            if (opts.chevron) {
                var chevron = document.createElement('span');
                chevron.className = 'focus-catalogue-row-chevron';
                chevron.setAttribute('aria-hidden', 'true');
                chevron.textContent = '\u203A';
                btn.appendChild(chevron);
            }

            if (opts.onClick) btn.addEventListener('click', opts.onClick);
            return btn;
        }

        /** A checkbox row. Real <input type="checkbox"> inside a <label>. */
        function makeTopicRow(topic, subject) {
            var wrapper = document.createElement('label');
            wrapper.className = 'focus-topic-row';
            wrapper.setAttribute('data-key', topic.key || '');

            var checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.className = 'focus-topic-checkbox';
            checkbox.value = topic.label;
            checkbox.checked = state.selected.has(topic.label);

            var text = document.createElement('span');
            text.className = 'focus-topic-label';
            text.textContent = topic.label;

            checkbox.addEventListener('change', function () {
                toggleTopic(topic.label, checkbox, subject);
            });

            wrapper.appendChild(checkbox);
            wrapper.appendChild(text);
            return wrapper;
        }

        function emptyState(message) {
            var p = document.createElement('p');
            p.className = 'focus-catalogue-empty';
            p.textContent = message;
            return p;
        }

        // ---- Level renderers -----------------------------------------------
        function renderAreas() {
            el.heading.textContent = 'Browse by academic area';
            el.subheading.textContent = 'Choose an area to see its common subjects.';
            el.breadcrumb.hidden = true;
            el.subjectAction.hidden = true;
            el.list.setAttribute('aria-label', 'Academic areas');

            areas.forEach(function (area) {
                el.list.appendChild(makeRow({
                    key: area.key,
                    icon: area.icon || '\uD83D\uDCDA',
                    label: area.label,
                    tag: (area.subjects || []).length + ' subjects',
                    chevron: true,
                    onClick: function () { goToSubjects(area.key, area.key); }
                }));
            });
        }

        function renderSubjects() {
            var area = areaByKey[state.areaKey];
            if (!area) return goToAreas();

            el.heading.textContent = area.label;
            el.subheading.textContent = 'Choose a subject to see its common topics.';
            el.breadcrumb.hidden = false;
            el.breadcrumb.textContent = '\u2039 Academic Areas';
            el.breadcrumb.onclick = function () { goToAreas(state.areaKey); };
            el.subjectAction.hidden = true;
            el.list.setAttribute('aria-label', area.label + ' subjects');

            (area.subjects || []).forEach(function (subject) {
                el.list.appendChild(makeRow({
                    key: subject.key,
                    label: subject.label,
                    meta: (subject.topics || []).length + ' topics',
                    tag: subject.code || '',
                    chevron: true,
                    onClick: function () { goToTopics(subject.key, subject.key); }
                }));
            });
        }

        function renderTopics() {
            var subject = subjectByKey[state.subjectKey];
            if (!subject) return goToAreas();

            var area = subjectArea[subject.key];

            el.heading.textContent = subject.label;
            el.subheading.textContent = 'Choose the topics that need more attention.';
            el.breadcrumb.hidden = false;
            el.breadcrumb.textContent = '\u2039 ' + (area ? area.label : 'Academic Areas');
            el.list.setAttribute('aria-label', subject.label + ' topics');

            // Secondary subject action
            el.subjectAction.hidden = false;
            var currentRaw = (config.getSubjectName() || '').trim();
            var current = norm(currentRaw);
            var isCurrent = current && current === norm(subject.label);

            if (isCurrent) {
                el.subjectActionBtn.textContent = '✓ ' + subject.label + ' is your focus subject';
                el.subjectActionBtn.classList.add('is-current');
            } else {
                el.subjectActionBtn.textContent = 'Use ' + subject.label + ' as My Subject';
                el.subjectActionBtn.classList.remove('is-current');
            }

            el.subjectActionBtn.onclick = function () {
                useAsSubject(subject, true);
                el.subjectActionBtn.textContent = '✓ ' + subject.label + ' is your focus subject';
                el.subjectActionBtn.classList.add('is-current');
            };

            (subject.topics || []).forEach(function (topic) {
                el.list.appendChild(makeTopicRow(topic, subject));
            });
        }

        function renderSearch() {
            var query = norm(state.searchQuery).trim();
            el.heading.textContent = 'Search results for \u201C' + state.searchQuery + '\u201D';
            el.subheading.textContent = 'Subjects and topics that match your search.';
            el.breadcrumb.hidden = false;
            el.breadcrumb.textContent = '\u2039 Browse by academic area';
            el.subjectAction.hidden = true;
            el.list.setAttribute('aria-label', 'Search results');

            var subjectHits = [];
            var topicHits = [];

            areas.forEach(function (area) {
                (area.subjects || []).forEach(function (subject) {
                    if (matches(subjectHaystack(subject), query)) {
                        subjectHits.push(subject);
                    }
                    (subject.topics || []).forEach(function (topic) {
                        if (matches(topicHaystack(topic, subject), query)) {
                            topicHits.push({ topic: topic, subject: subject });
                        }
                    });
                });
            });

            if (!subjectHits.length && !topicHits.length) {
                el.list.appendChild(emptyState(
                    'Nothing matched \u201C' + state.searchQuery + '\u201D. ' +
                    'You can still type your topic manually on the Focus form.'
                ));
                return;
            }

            if (subjectHits.length) {
                el.list.appendChild(sectionLabel('Subjects'));
                subjectHits.forEach(function (subject) {
                    var area = subjectArea[subject.key];
                    el.list.appendChild(makeRow({
                        key: subject.key,
                        label: subject.label,
                        meta: area ? area.label : '',
                        tag: subject.code || '',
                        chevron: true,
                        onClick: function () { goToTopics(subject.key, subject.key); }
                    }));
                });
            }

            if (topicHits.length) {
                el.list.appendChild(sectionLabel('Topics'));
                topicHits.forEach(function (hit) {
                    var area = subjectArea[hit.subject.key];
                    var context = hit.subject.label +
                        (area ? ' \u00B7 ' + area.label : '');

                    var isSelected = state.selected.has(hit.topic.label);

                    el.list.appendChild(makeRow({
                        key: hit.topic.key,
                        label: hit.topic.label,
                        meta: context,
                        tag: isSelected ? '\u2713 Added' : 'Add topic',
                        modifier: isSelected ? ' is-selected' : '',
                        onClick: function (event) {
                            addTopicFromSearch(hit.topic.label, event.currentTarget, hit.subject);
                        }
                    }));
                });
            }
        }

        function sectionLabel(text) {
            var heading = document.createElement('h3');
            heading.className = 'focus-catalogue-section-label';
            heading.textContent = text;
            return heading;
        }

        // ---- Selection ------------------------------------------------------
        function toggleTopic(label, checkbox, subject) {
            if (state.selected.has(label)) {
                state.selected.delete(label);
            } else {
                if (state.selected.size >= MAX_TOPICS) {
                    checkbox.checked = false;
                    showCapMessage();
                    return;
                }
                state.selected.add(label);
                clearCapMessage();
                // Only suggest a subject when the field is empty.
                if (subject && !config.getSubjectName()) useAsSubject(subject, false);
            }
            commit();
            if (state.level === 'topics' && checkbox) {
                checkbox.closest('.focus-topic-row')
                    .classList.toggle('is-selected', checkbox.checked);
            }
        }

        function addTopicFromSearch(label, rowEl, subject) {
            if (state.selected.has(label)) {
                state.selected.delete(label);
            } else {
                if (state.selected.size >= MAX_TOPICS) {
                    showCapMessage();
                    return;
                }
                state.selected.add(label);
                clearCapMessage();
                if (subject && !config.getSubjectName()) useAsSubject(subject, false);
            }
            commit();
            // Re-render so the row's tag reflects the new state.
            render();
            if (rowEl && rowEl.dataset.key) {
                var next = el.list.querySelector('[data-key="' + rowEl.dataset.key + '"]');
                if (next) next.focus();
            }
        }

        function showCapMessage() {
            el.capMessage.hidden = false;
            el.capMessage.textContent =
                'You can select up to ' + MAX_TOPICS + ' priority topics. ' +
                'Remove one before adding another.';
        }

        function clearCapMessage() {
            el.capMessage.hidden = true;
            el.capMessage.textContent = '';
        }

        /** Push the staged selection into the modal's authoritative list. */
        function commit() {
            config.onSelectionChange(getSelected());
            renderTray();
        }

        function renderTray() {
            var count = state.selected.size;
            var labels = getSelected();

            el.trayStatus.textContent = count === 0
                ? 'No topics selected yet'
                : count + ' of ' + MAX_TOPICS + ' topics selected';

            el.trayChips.innerHTML = '';
            labels.forEach(function (label) {
                var chip = document.createElement('span');
                chip.className = 'focus-topic-chip';
                chip.innerHTML = '<span>' + escapeHtml(label) + '</span>' +
                    '<button type="button" aria-label="Remove topic ' +
                    escapeHtml(label) + '">&times;</button>';
                chip.querySelector('button').addEventListener('click', function () {
                    state.selected.delete(label);
                    clearCapMessage();
                    commit();
                    render();
                });
                el.trayChips.appendChild(chip);
            });

            el.trayClear.hidden = count === 0;

            var btnApply = document.getElementById('btn-catalogue-apply') || el.footerPrimary;
            if (btnApply) {
                btnApply.textContent = count === 0
                    ? 'Add Selected Topics'
                    : 'Add ' + count + ' Topic' + (count === 1 ? '' : 's');
                btnApply.disabled = count === 0;
            }
        }

        // ---- Subject handling ----------------------------------------------
        /**
         * @param {object} subject
         * @param {boolean} force  true when the learner explicitly pressed
         *                         "Use X as My Subject" (overrides the field).
         */
        function useAsSubject(subject, force) {
            var current = norm(config.getSubjectName());
            if (!current || force) {
                config.setSubject(subject.label, subject.code || '');
            }
        }

        // ---- Navigation -----------------------------------------------------
        function setLevel(level, focusKey) {
            state.level = level;
            state.returnFocusKey = focusKey || null;
            render();
            moveFocusToHeading();
        }

        function goToAreas(returnKey) {
            state.areaKey = null;
            state.subjectKey = null;
            setLevel('areas', returnKey);
        }

        function goToSubjects(areaKey, returnKey) {
            state.areaKey = areaKey;
            state.subjectKey = null;
            setLevel('subjects', returnKey);
        }

        function goToTopics(subjectKey, returnKey) {
            state.subjectKey = subjectKey;
            var subject = subjectByKey[subjectKey];
            state.areaKey = subject ? (subjectArea[subjectKey] || {}).key : state.areaKey;
            setLevel('topics', returnKey);
        }

        function goBack() {
            if (state.level === 'topics') return goToSubjects(state.areaKey, state.subjectKey);
            if (state.level === 'subjects') return goToAreas(state.areaKey);
            if (state.level === 'search') return goToAreas();
            return false;
        }

        /** Announce + focus the current level heading. */
        function moveFocusToHeading() {
            if (!el.heading) return;
            el.heading.setAttribute('tabindex', '-1');
            el.heading.focus({ preventScroll: true });
            if (el.body) el.body.scrollTop = 0;
        }

        function render() {
            el.list.innerHTML = '';

            if (state.level === 'subjects') renderSubjects();
            else if (state.level === 'topics') renderTopics();
            else if (state.level === 'search') renderSearch();
            else renderAreas();

            renderTray();
        }

        // ---- Search wiring --------------------------------------------------
        function applySearch(query) {
            state.searchQuery = query;
            if (el.clearSearch) el.clearSearch.hidden = !query;
            if (!query) return goToAreas();
            setLevel('search');
        }

        if (el.search) {
            el.search.addEventListener('input', function () {
                var value = el.search.value.trim();
                state.searchQuery = value;
                if (el.clearSearch) el.clearSearch.hidden = !value;
                if (!value) {
                    goToAreas();
                } else {
                    state.level = 'search';
                    render();
                }
            });
        }

        if (el.clearSearch) {
            el.clearSearch.addEventListener('click', function () {
                el.search.value = '';
                state.searchQuery = '';
                el.clearSearch.hidden = true;
                goToAreas();
            });
        }

        if (el.popular) {
            el.popular.querySelectorAll('[data-query]').forEach(function (chip) {
                chip.addEventListener('click', function () {
                    var query = chip.getAttribute('data-query');
                    el.search.value = query;
                    applySearch(query);
                });
            });
        }

        if (el.breadcrumb) {
            el.breadcrumb.addEventListener('click', function () {
                var returningKey = state.returnFocusKey;
                goBack();
                if (returningKey) {
                    var row = el.list.querySelector('[data-key="' + returningKey + '"]');
                    if (row) row.focus();
                }
            });
        }

        if (el.trayClear) {
            el.trayClear.addEventListener('click', function () {
                state.selected.clear();
                clearCapMessage();
                commit();
                render();
            });
        }

        // ---- Public API -----------------------------------------------------
        return {
            /**
             * Reconcile staged state with the modal's authoritative selection
             * and show the root level. Called every time the catalogue opens.
             */
            open: function (initialTopics, suggestedSubjectKey) {
                state.selected = new Set(initialTopics || []);
                state.searchQuery = '';
                state.returnFocusKey = null;
                clearCapMessage();

                if (el.search) el.search.value = '';
                if (el.clearSearch) el.clearSearch.hidden = true;

                if (suggestedSubjectKey && subjectByKey[suggestedSubjectKey]) {
                    goToTopics(suggestedSubjectKey, suggestedSubjectKey);
                } else {
                    goToAreas();
                }
            },
            refresh: function (initialTopics) {
                state.selected = new Set(initialTopics || []);
                render();
            },
            goBack: goBack,
            getLevel: function () { return state.level; },
            render: render
        };
    }

    /**
     * Find suggested subject key matching free-text subject string.
     */
    function findSuggestedSubjectKey(data, currentSubject) {
        var current = (currentSubject || '').trim().toLowerCase();
        if (!current || !data || !data.areas) return null;

        for (var i = 0; i < data.areas.length; i++) {
            var area = data.areas[i];
            var subjects = area.subjects || [];
            for (var j = 0; j < subjects.length; j++) {
                var subj = subjects[j];
                var label = (subj.label || '').toLowerCase();
                var code = (subj.code || '').toLowerCase();
                var aliases = (subj.aliases || []).map(function (a) { return a.toLowerCase(); });
                if (label === current || code === current || aliases.indexOf(current) !== -1) {
                    return subj.key;
                }
            }
        }
        return null;
    }

    /**
     * Load academic catalogue data from embedded JSON script tag or fallback API.
     */
    async function loadCatalogueData(apiUrl) {
        var scriptEl = document.getElementById('academic-catalogue-data');
        if (scriptEl && scriptEl.textContent.trim()) {
            try {
                var parsed = JSON.parse(scriptEl.textContent);
                if (parsed && Array.isArray(parsed.areas) && parsed.areas.length > 0) {
                    return parsed;
                }
            } catch (e) {
                console.error('Failed to parse embedded academic catalogue:', e);
            }
        }
        if (apiUrl) {
            try {
                var resp = await fetch(apiUrl);
                if (resp.ok) {
                    var data = await resp.json();
                    if (data && Array.isArray(data.areas) && data.areas.length > 0) {
                        return data;
                    }
                }
            } catch (err) {
                console.error('Error fetching academic catalogue fallback:', err);
            }
        }
        return null;
    }

    window.StudyQuestCatalogue = {
        create: createController,
        findSuggestedSubjectKey: findSuggestedSubjectKey,
        loadData: loadCatalogueData,
        MAX_TOPICS: MAX_TOPICS,
        LEVELS: LEVELS,
        LEVEL_TITLES: LEVEL_TITLES
    };

    window.FocusCatalogueController = window.StudyQuestCatalogue;
})();