/** Confirm the dashboard shows exactly one Study Focus invitation. */
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
    var ch = cp.spawn(CHROME, ['--headless=new', '--remote-debugging-port=9230',
        '--user-data-dir=' + dir, '--no-first-run', '--disable-gpu',
        '--window-size=1600,1000', 'about:blank'], { stdio: 'ignore' });
    for (var i = 0; i < 40; i++) {
        await sleep(250);
        try { await get('http://127.0.0.1:9230/json/version'); break; } catch (e) {}
    }
    var t = JSON.parse((await get('http://127.0.0.1:9230/json/list')).b)
        .find(function (x) { return x.type === 'page'; });
    var ws = new WebSocket(t.webSocketDebuggerUrl);
    var id = 0, pend = {};
    ws.addEventListener('message', function (e) {
        var m = JSON.parse(e.data);
        if (m.id && pend[m.id]) { pend[m.id](m); delete pend[m.id]; }
    });
    await new Promise(function (r) { ws.addEventListener('open', r); });
    var send = function (m, p) {
        return new Promise(function (res, rej) {
            var i = ++id;
            pend[i] = function (x) { x.error ? rej(new Error(x.error.message)) : res(x.result); };
            ws.send(JSON.stringify({ id: i, method: m, params: p || {} }));
        });
    };
    var ev = async function (ex) {
        var r = await send('Runtime.evaluate',
            { expression: ex, returnByValue: true, awaitPromise: true });
        if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
        return r.result.value;
    };

    await send('Page.enable'); await send('Runtime.enable');
    await send('Page.navigate', { url: 'http://127.0.0.1:8000/login/' });
    await sleep(1200);
    await ev('(function(){var f=document.querySelector("form");' +
        'f.querySelector("input[name=username]").value="layout_probe";' +
        'f.querySelector("input[name=password]").value="layout-probe-12345";' +
        'var a=f.querySelector("input[name=action]"); if(a)a.value="login"; f.submit();})()');
    await sleep(2400);

    var out = await ev([
        '(function(){',
        ' var body=document.body.innerText;',
        ' return JSON.stringify({',
        '   path: window.location.pathname,',
        '   hasPanel: body.indexOf("SET A STUDY FOCUS") !== -1,',
        '   panelCount: (body.match(/SET A STUDY FOCUS/g)||[]).length,',
        '   hasPill: body.indexOf("Set Study Focus") !== -1,',
        '   hasExplainer: body.indexOf("tailor practice runs") !== -1,',
        '   hasCTA: body.indexOf("Find My Study Focus") !== -1,',
        '   focusCards: document.querySelectorAll(".focus-dashboard-card").length',
        ' });',
        '})()'
    ].join('\n'));
    console.log(out);

    ws.close(); ch.kill();
    try { fs.rmSync(dir, { recursive: true, force: true }); } catch (e) {}
})();