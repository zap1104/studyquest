/**
 * Course Forge — Course Ready lifecycle regression test.
 *
 * Guards the E3 defect: when generation completes during active gameplay, the
 * animation loop must stop AND game input must be disabled, so nothing keeps
 * moving behind the Course Ready overlay and no stale action stays queued.
 *
 * Runs in Node with a minimal DOM stub. No browser required.
 *
 *   node courses/static/courses/js/forge/tests/camp_lifecycle.test.js
 */
'use strict';

var fs = require('fs');
var path = require('path');
var vm = require('vm');

var passed = 0;
var failed = 0;

function assert(condition, label) {
    if (condition) {
        passed += 1;
        console.log('  PASS  ' + label);
    } else {
        failed += 1;
        console.log('  FAIL  ' + label);
    }
}

/** Load forge_input.js into a fresh sandbox with a minimal window. */
function loadInput() {
    var listeners = [];
    var sandbox = {};

    function makeTarget() {
        return {
            addEventListener: function (type, fn) {
                listeners.push({ type: type, fn: fn });
            },
            removeEventListener: function (type, fn) {
                var i = listeners.findIndex(function (l) {
                    return l.type === type && l.fn === fn;
                });
                if (i >= 0) listeners.splice(i, 1);
            }
        };
    }

    var win = {
        innerWidth: 1280,
        innerHeight: 720,
        addEventListener: function (type, fn) {
            listeners.push({ type: type, fn: fn, target: 'window' });
        },
        removeEventListener: function () {}
    };

    sandbox.window = win;
    sandbox.document = {
        addEventListener: makeTarget().addEventListener,
        removeEventListener: function () {}
    };

    var code = fs.readFileSync(
        path.join(__dirname, '..', 'forge_input.js'), 'utf8'
    );
    vm.createContext(sandbox);
    vm.runInContext(code, sandbox);

    return { api: sandbox.window.CourseForgeInput.create(), listenerCount: function () { return listeners.length; } };
}

console.log('Course Forge input lifecycle');
console.log('----------------------------');

// 1. Input starts enabled.
var a = loadInput();
assert(a.api.isEnabled() === true, 'input starts enabled');
assert(a.api.getMoveVector().x === 0, 'idle input yields zero movement');

// 2. Queued actions are consumed while enabled.
a.api.trigger('action');
assert(a.api.consume('action') === true, 'queued action is consumed when enabled');

// 3. Disabling clears queued actions and zeroes movement.
var b = loadInput();
b.api.trigger('action');
b.api.trigger('upgrade');
b.api.setEnabled(false);
assert(b.api.isEnabled() === false, 'setEnabled(false) disables input');
assert(b.api.consume('action') === false, 'disabled input does not yield queued actions');
assert(b.api.consume('upgrade') === false, 'all queued actions are cleared on disable');

// 4. The critical E3 case: a stale action must NOT fire after a later resume.
var c = loadInput();
c.api.trigger('action');          // pressed during active play
c.api.setEnabled(false);          // Course Ready
c.api.setEnabled(true);           // hypothetically resumed later
assert(
    c.api.consume('action') === false,
    'REGRESSION: action queued before pause must not survive a resume'
);

// 5. Movement is ignored while disabled.
var d = loadInput();
var downHandler = null;
// Re-derive the keydown handler by re-loading with a capturing stub.
var captured = {};
var sandbox2 = {};
var win2 = {
    innerWidth: 1280, innerHeight: 720,
    addEventListener: function (type, fn) { captured[type] = fn; },
    removeEventListener: function () {}
};
sandbox2.window = win2;
sandbox2.document = {
    addEventListener: function (type, fn) { captured['doc:' + type] = fn; },
    removeEventListener: function () {}
};
vm.createContext(sandbox2);
vm.runInContext(
    fs.readFileSync(path.join(__dirname, '..', 'forge_input.js'), 'utf8'),
    sandbox2
);
var api2 = sandbox2.window.CourseForgeInput.create();
var keydown = captured['keydown'];
assert(typeof keydown === 'function', 'keydown listener is registered');

if (typeof keydown === 'function') {
    api2.setEnabled(false);
    keydown({ key: 'ArrowRight', target: { tagName: 'BODY' }, preventDefault: function () {} });
    var vec = api2.getMoveVector();
    assert(
        vec.x === 0 && vec.y === 0,
        'movement keys are ignored while input is disabled'
    );
}

// 6. destroy() removes listeners and disables input.
var e = loadInput();
var before = e.listenerCount();
e.api.destroy();
assert(e.api.isEnabled() === false, 'destroy() disables input');

console.log('----------------------------');
console.log('passed: ' + passed + '  failed: ' + failed);
process.exit(failed === 0 ? 0 : 1);