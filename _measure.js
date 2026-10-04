/** Measure the landscape Forge layout precisely. */
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
    var ch = cp.spawn(CHROME, ['--headless=new', '--remote-debugging-port=9227',
        '--user-data-dir=' + dir, '--no-first-run', '--disable-gpu', 'about:blank'],
        { stdio: 'ignore' });
    for (var i = 0; i < 40; i++) {
        await sleep(250);
        try { await get('http://127.0.0.1:9227/json/version'); break; } catch (e) { }
    }
    var t = JSON.parse((await get('http://127.0.0.1:9227/json/list')).b)
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
    await sleep(2200);
    await send('Emulation.setDeviceMetricsOverride',
        { width: 844, height: 390, deviceScaleFactor: 1, mobile: true });
    await send('Page.navigate', { url: 'http://127.0.0.1:8000/courses/14/generating/' });
    await sleep(2200);

    var out = await ev([
        '(function(){',
        ' var L=[];',
        ' var root=getComputedStyle(document.documentElement);',
        ' L.push("--topbar-height = ["+root.getPropertyValue("--topbar-height")+"]");',
        ' var ws=document.querySelector(".course-forge-workspace");',
        ' var cs=getComputedStyle(ws);',
        ' L.push("workspace height="+cs.height+" maxHeight="+cs.maxHeight);',
        ' L.push("workspace rect h="+Math.round(ws.getBoundingClientRect().height)+',
        '        " top="+Math.round(ws.getBoundingClientRect().top));',
        ' var wrap=document.querySelector(".forge-page-wrap");',
        ' L.push("wrap top="+Math.round(wrap.getBoundingClientRect().top)+',
        '        " h="+Math.round(wrap.getBoundingClientRect().height));',
        ' var topbar=document.querySelector(".topbar");',
        ' if(topbar){ var tb=topbar.getBoundingClientRect();',
        '   L.push("topbar h="+Math.round(tb.height)+" bottom="+Math.round(tb.bottom)); }',
        ' else { L.push("topbar: not found"); }',
        ' L.push("body children:");',
        ' Array.prototype.forEach.call(document.body.children,function(el){',
        '   var r=el.getBoundingClientRect();',
        '   L.push("  <"+el.tagName.toLowerCase()+" class=\\""+(el.className||"").toString().slice(0,40)+"\\"> top="+Math.round(r.top)+" h="+Math.round(r.height));',
        ' });',
        ' L.push("scrollH="+document.documentElement.scrollHeight+" innerH="+window.innerHeight);',
        ' return L.join("\\n");',
        '})()'
    ].join('\n'));
    console.log(out);

    ws.close(); ch.kill();
    try { fs.rmSync(dir, { recursive: true, force: true }); } catch (e) { }
})();