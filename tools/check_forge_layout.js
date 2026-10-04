/**
 * Measures whether the Course Forge control guide is visible in the viewport.
 * Drives real Chrome over the DevTools Protocol (Node built-ins only).
 *
 *   node tools/check_forge_layout.js
 */
'use strict';

var http = require('http');
var childProcess = require('child_process');
var fs = require('fs');
var os = require('os');
var path = require('path');

var BASE = process.env.SQ_BASE || 'http://127.0.0.1:8000';
var CHROME = [
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'
].filter(function (p) { return fs.existsSync(p); })[0];

var VIEWPORTS = [
    { name: '1920x1080', w: 1920, h: 1080 },
    { name: '1440x900', w: 1440, h: 900 },
    { name: '1366x768', w: 1366, h: 768 },
    { name: '390x844', w: 390, h: 844 },
    { name: '844x390', w: 844, h: 390 }
];

function get(url) {
    return new Promise(function (resolve, reject) {
        http.get(url, function (res) {
            var body = '';
            res.on('data', function (c) { body += c; });
            res.on('end', function () { resolve({ status: res.statusCode, body: body }); });
        }).on('error', reject);
    });
}
function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

function CDP(wsUrl) {
    this.ws = new WebSocket(wsUrl);
    this.id = 0;
    this.pending = {};
    var self = this;
    this.ws.addEventListener('message', function (evt) {
        var msg = JSON.parse(evt.data);
        if (msg.id && self.pending[msg.id]) {
            self.pending[msg.id](msg);
            delete self.pending[msg.id];
        }
    });
}
CDP.prototype.ready = function () {
    var self = this;
    return new Promise(function (res, rej) {
        self.ws.addEventListener('open', res);
        self.ws.addEventListener('error', rej);
    });
};
CDP.prototype.send = function (method, params) {
    var self = this;
    var id = ++this.id;
    return new Promise(function (resolve, reject) {
        self.pending[id] = function (msg) {
            if (msg.error) reject(new Error(method + ': ' + msg.error.message));
            else resolve(msg.result);
        };
        self.ws.send(JSON.stringify({ id: id, method: method, params: params || {} }));
        setTimeout(function () {
            if (self.pending[id]) { delete self.pending[id]; reject(new Error(method + ' timeout')); }
        }, 20000);
    });
};
CDP.prototype.evaluate = async function (expr) {
    var r = await this.send('Runtime.evaluate', {
        expression: expr, returnByValue: true, awaitPromise: true
    });
    if (r.exceptionDetails) throw new Error('JS: ' + JSON.stringify(r.exceptionDetails.exception));
    return r.result.value;
};

(async function main() {
    if (!CHROME) { console.error('No Chrome/Edge found.'); process.exit(1); }

    var userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'sq-layout-'));
    var chrome = childProcess.spawn(CHROME, [
        '--headless=new', '--remote-debugging-port=9223',
        '--user-data-dir=' + userDataDir, '--no-first-run',
        '--no-default-browser-check', '--disable-gpu',
        '--window-size=1920,1080', 'about:blank'
    ], { stdio: 'ignore' });

    var version = null;
    for (var i = 0; i < 40; i++) {
        await sleep(250);
        try { version = JSON.parse((await get('http://127.0.0.1:9223/json/version')).body); break; }
        catch (e) { }
    }
    if (!version) { console.error('Chrome never came up.'); chrome.kill(); process.exit(1); }

    var targets = JSON.parse((await get('http://127.0.0.1:9223/json/list')).body);
    var page = targets.find(function (t) { return t.type === 'page'; });
    var cdp = new CDP(page.webSocketDebuggerUrl);
    await cdp.ready();
    await cdp.send('Page.enable');
    await cdp.send('Runtime.enable');

    var user = process.env.SQ_USER, pass = process.env.SQ_PASS;
    if (!user || !pass) {
        console.error('Set SQ_USER and SQ_PASS.');
        chrome.kill(); process.exit(1);
    }

    // Log in.
    await cdp.send('Page.navigate', { url: BASE + '/login/' });
    await sleep(1200);
    await cdp.evaluate(
        '(function(){var f=document.querySelector("form");' +
        'f.querySelector("input[name=username]").value=' + JSON.stringify(user) + ';' +
        'f.querySelector("input[name=password]").value=' + JSON.stringify(pass) + ';' +
        'var a=f.querySelector("input[name=action]"); if(a) a.value="login";' +
        'f.submit();})()'
    );
    await sleep(2200);

    // Find the Forge page for a processing course.
    await cdp.send('Page.navigate', { url: BASE + '/courses/' });
    await sleep(1500);
    var forgeUrl = await cdp.evaluate(
        '(function(){var a=document.querySelector("a[href*=\'generating\']");' +
        'return a ? a.getAttribute("href") : null;})()'
    );
    if (!forgeUrl) {
        console.error('No processing course found. Create one first.');
        chrome.kill(); process.exit(1);
    }
    console.log('Forge page: ' + forgeUrl);
    console.log('='.repeat(64));

    var failures = 0;
    for (var v = 0; v < VIEWPORTS.length; v++) {
        var vp = VIEWPORTS[v];
        await cdp.send('Emulation.setDeviceMetricsOverride', {
            width: vp.w, height: vp.h, deviceScaleFactor: 1, mobile: vp.w < 500
        });
        await cdp.send('Page.navigate', { url: BASE + forgeUrl });
        await sleep(1800);

        var m = await cdp.evaluate(
            '(function(){' +
            ' var bar=document.querySelector(".forge-camp-hintbar");' +
            ' var wrap=document.querySelector(".forge-camp-canvas-wrap");' +
            ' var canvas=document.querySelector(".forge-camp-canvas");' +
            ' if(!bar) return {missing:true};' +
            ' var b=bar.getBoundingClientRect();' +
            ' var w=wrap?wrap.getBoundingClientRect():null;' +
            ' var c=canvas?canvas.getBoundingClientRect():null;' +
            ' return {' +
            '   barTop: Math.round(b.top), barBottom: Math.round(b.bottom),' +
            '   barHeight: Math.round(b.height),' +
            '   canvasHeight: c ? Math.round(c.height) : null,' +
            '   canvasWidth: c ? Math.round(c.width) : null,' +
            '   viewportH: window.innerHeight,' +
            '   docScrollH: document.documentElement.scrollHeight,' +
            '   visible: b.top >= 0 && b.bottom <= window.innerHeight + 1' +
            ' };' +
            '})()'
        );

        if (m.missing) {
            console.log('  ' + vp.name + '  FAIL  hint bar not in DOM');
            failures++;
            continue;
        }
        var ok = m.visible;
        if (!ok) failures++;
        console.log(
            '  ' + vp.name +
            '  ' + (ok ? 'PASS' : 'FAIL') +
            '  canvas ' + m.canvasWidth + 'x' + m.canvasHeight +
            '  bar ' + m.barTop + '-' + m.barBottom +
            '  viewport ' + m.viewportH +
            '  scrollH ' + m.docScrollH
        );
    }

    console.log('='.repeat(64));
    console.log(failures === 0
        ? 'Control guide visible at every tested viewport.'
        : failures + ' viewport(s) still hide the control guide.');

    try { cdp.ws.close(); } catch (e) { }
    chrome.kill();
    try { fs.rmSync(userDataDir, { recursive: true, force: true }); } catch (e) { }
    process.exit(failures === 0 ? 0 : 1);
})();