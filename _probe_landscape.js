/** Probe why the landscape media query does not apply. */
'use strict';
var http = require('http'), cp = require('child_process'), fs = require('fs'),
    os = require('os'), path = require('path');

var CHROME = [
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe'
].filter(function (p) { return fs.existsSync(p); })[0];

var get = function (u) {
    return new Promise(function (res, rej) {
        http.get(u, function (r) {
            var b = ''; r.on('data', function (c) { b += c; });
            r.on('end', function () { res({ s: r.statusCode, b: b }); });
        }).on('error', rej);
    });
};
var sleep = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };

(async function () {
    var dir = fs.mkdtempSync(path.join(os.tmpdir(), 'sq-'));
    var ch = cp.spawn(CHROME, ['--headless=new', '--remote-debugging-port=9225',
        '--user-data-dir=' + dir, '--no-first-run', '--disable-gpu', 'about:blank'],
        { stdio: 'ignore' });

    for (var i = 0; i < 40; i++) {
        await sleep(250);
        try { await get('http://127.0.0.1:9225/json/version'); break; } catch (e) { }
    }
    var t = JSON.parse((await get('http://127.0.0.1:9225/json/list')).b)
        .find(function (x) { return x.type === 'page'; });

    var ws = new WebSocket(t.webSocketDebuggerUrl);
    var id = 0, pend = {};
    ws.addEventListener('message', function (e) {
        var m = JSON.parse(e.data);
        if (m.id && pend[m.id]) { pend[m.id](m); delete pend[m.id]; }
    });
    await new Promise(function (r) { ws.addEventListener('open', r); });

    var send = function (method, params) {
        return new Promise(function (res, rej) {
            var i = ++id;
            pend[i] = function (m) { m.error ? rej(new Error(m.error.message)) : res(m.result); };
            ws.send(JSON.stringify({ id: i, method: method, params: params || {} }));
        });
    };
    var ev = async function (ex) {
        var r = await send('Runtime.evaluate',
            { expression: ex, returnByValue: true, awaitPromise: true });
        if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
        return r.result.value;
    };

    await send('Page.enable');
    await send('Runtime.enable');
    await send('Page.navigate', { url: 'http://127.0.0.1:8000/login/' });
    await sleep(1200);
    await ev('(function(){var f=document.querySelector("form");' +
        'f.querySelector("input[name=username]").value="layout_probe";' +
        'f.querySelector("input[name=password]").value="layout-probe-12345";' +
        'var a=f.querySelector("input[name=action]"); if(a)a.value="login"; f.submit();})()');
    await sleep(2200);

    await send('Emulation.setDeviceMetricsOverride',
        { width: 844, height: 390, deviceScaleFactor: 1, mobile: true });
    await send('Page.navigate', { url: 'http://127.0.0.1:8000/courses/14/generating/' });
    await sleep(2200);

    var info = await ev([
        '(function(){',
        ' var ws=document.querySelector(".course-forge-workspace");',
        ' var cs=ws?getComputedStyle(ws):null;',
        ' var wrap=document.querySelector(".forge-camp-canvas-wrap");',
        ' var wcs=wrap?getComputedStyle(wrap):null;',
        ' return JSON.stringify({',
        '   innerW: window.innerWidth, innerH: window.innerHeight,',
        '   mqLandscape: window.matchMedia("(orientation: landscape)").matches,',
        '   mqShort: window.matchMedia("(max-height: 520px)").matches,',
        '   cols: cs ? cs.gridTemplateColumns : null,',
        '   wrapMaxH: wcs ? wcs.maxHeight : null,',
        '   wrapH: wrap ? Math.round(wrap.getBoundingClientRect().height) : null,',
        '   panelH: (function(){var p=document.querySelector(".forge-status-panel");',
        '            return p?Math.round(p.getBoundingClientRect().height):null;})()',
        ' });',
        '})()'
    ].join('\n'));
    console.log(info);

    ws.close(); ch.kill();
    try { fs.rmSync(dir, { recursive: true, force: true }); } catch (e) { }
})();