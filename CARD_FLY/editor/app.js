(function () {
  'use strict';

  var $ = function (s) { return document.querySelector(s); };
  var $$ = function (s) { return Array.prototype.slice.call(document.querySelectorAll(s)); };
  var NS = 'http://www.w3.org/2000/svg';
  var XLINK = 'http://www.w3.org/1999/xlink';
  var PX = 96 / 25.4;
  var EDITABLE = new Set(['text', 'image', 'rect', 'path', 'circle', 'ellipse', 'line', 'polygon', 'polyline']);
  var RESIZABLE = new Set(['image', 'rect', 'circle', 'ellipse']);
  var SUGGEST = {
    name: 'Student Name', student_name: 'Student Name', first_name: 'First', last_name: 'Last',
    school_name: 'Sunrise Academy', student_id: '2026-0001', reg_no: 'REG-2026-001',
    class: 'S4', sec: 'A', level: 'S4', section: 'A', phone: '+250 7xx xxx xxx',
    logo: '', image_base64: '', data_qrcode: ''
  };
  var PH = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="120"><rect width="120" height="120" fill="#dfe3ea"/><rect x="3" y="3" width="114" height="114" fill="none" stroke="#9aa2b1" stroke-width="4" stroke-dasharray="8 6"/></svg>');

  var CODE_CACHE = {};
  function seedRand(str) {
    var h = 2166136261;
    for (var i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); }
    return function () { h = (Math.imul(h | 0, 1664525) + 1013904223) | 0; return ((h >>> 8) & 0xffffff) / 0xffffff; };
  }
  function codeSample(name) {
    if (CODE_CACHE[name]) return CODE_CACHE[name];
    var cv = document.createElement('canvas');
    var rnd = seedRand(name);
    var isQr = /qrcode/i.test(name);
    if (isQr) {
      var sq = 3, M = 29, size = M * sq;               // 25 module grid + 2-module quiet zone per side
      cv.width = size; cv.height = size;
      var ctx = cv.getContext('2d');
      ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, size, size);
      ctx.fillStyle = '#000000';
      var x, y;
      for (y = 0; y < M; y++) for (x = 0; x < M; x++) {
        if (rnd() < 0.42) ctx.fillRect(x * sq, y * sq, sq, sq);
      }
      function finder(fx, fy) {
        // quiet boundary (1px), outer ring, white ring, solid 3x3 core
        ctx.fillRect(fx * sq, fy * sq, 9 * sq, 9 * sq);
        ctx.fillStyle = '#ffffff';
        ctx.fillRect((fx + 1) * sq, (fy + 1) * sq, 7 * sq, 7 * sq);
        ctx.fillStyle = '#000000';
        ctx.fillRect((fx + 2) * sq, (fy + 2) * sq, 5 * sq, 5 * sq);
      }
      finder(0, 0); finder(M - 9, 0); finder(0, M - 9);
      // a few small alignment-ish blocks for realism
      ctx.fillStyle = '#000000';
      ctx.fillRect(18 * sq, 18 * sq, 3 * sq, 3 * sq);
      ctx.fillRect(13 * sq, 2 * sq, 2 * sq, 2 * sq);
      ctx.fillRect(2 * sq, 13 * sq, 2 * sq, 2 * sq);
    } else {
      var W = 120, H = 56;
      cv.width = W; cv.height = H;
      var c2 = cv.getContext('2d');
      c2.fillStyle = '#ffffff'; c2.fillRect(0, 0, W, H);
      c2.fillStyle = '#000000';
      var bx = 2, i;
      for (i = 0; i < 26; i++) {
        var bw = 1 + Math.floor(rnd() * 3);
        var hh = (i === 0 || i === 13 || i === 25) ? H - 4 : H - 18;
        c2.fillRect(bx, 2, bw, hh);
        bx += bw + 1 + Math.floor(rnd() * 2);
      }
      // start/stop guard bars
      c2.fillRect(1, 2, 1, H - 4);
      c2.fillRect(W - 2, 2, 1, H - 4);
    }
    CODE_CACHE[name] = cv.toDataURL('image/png');
    return CODE_CACHE[name];
  }
  function hrefSample(tok) {
    var s = state.samples[tok];
    if (s) return s;
    var v = state.vars.get(tok);
    if (v && v.isCode) return codeSample(tok);
    return PH;
  }

  var state = {
    doc: null,
    view: null,
    elements: new Map(),
    order: [],
    eid: 0,
    vars: new Map(),
    vtype: {},
    samples: {},
    selEid: null,
    selDoc: null,
    zoom: 1,
    mode: 'edit',
    history: [],
    hIdx: -1,
    fileName: 'card',
    templateName: '',
    backDoc: null,
    backFileName: '',
    dnd: null,
    gesture: null,
    fields: []
  };

  function toast(msg, ms) {
    var t = $('#toast');
    t.textContent = msg;
    t.classList.add('show');
    clearTimeout(t._id);
    t._id = setTimeout(function () { t.classList.remove('show'); }, ms || 3200);
  }

  function clamp(v, a, b) { return Math.min(b, Math.max(a, v)); }

  function vb(svg) {
    var a = svg.getAttribute('viewBox');
    if (!a) return null;
    var p = a.trim().split(/[\s,]+/).map(Number);
    return { x: p[0] || 0, y: p[1] || 0, w: p[2] || 0, h: p[3] || 0 };
  }

  function unitPx(v) {
    if (v === null || v === undefined || v === '') return null;
    var m = /^\s*(-?[\d.eE]+)\s*([a-z%]*)/i.exec(String(v));
    if (!m) return null;
    var n = parseFloat(m[1]);
    var u = (m[2] || '').toLowerCase();
    if (u === 'mm') return n * PX;
    if (u === 'cm') return n * PX * 10;
    if (u === 'in') return n * 96;
    if (u === 'pt') return n * 96 / 72;
    if (u === 'pc') return n * 16;
    if (u === 'px' || u === '') return n;
    return null;
  }

  function resolveDocRect(svg) {
    var b = vb(svg);
    var rawVB = svg.getAttribute('viewBox');
    var wpx = unitPx(svg.getAttribute('width'));
    var hpx = unitPx(svg.getAttribute('height'));
    if (rawVB !== null && b && b.w > 0 && b.h > 0) {
      var ppu = (wpx !== null && wpx > 0) ? (wpx / b.w) : 1;
      return { b: b, ppu: ppu };
    }
    if (wpx !== null && hpx !== null && wpx > 0 && hpx > 0) {
      return { b: { x: 0, y: 0, w: wpx, h: hpx }, ppu: 1 };
    }
    return { b: { x: 0, y: 0, w: 300, h: 150 }, ppu: 1 };
  }

  function parseSVG(text) {
    var d;
    try { d = new DOMParser().parseFromString(text, 'image/svg+xml'); } catch (e) { return null; }
    if (d.querySelector('parsererror')) return null;
    var s = d.documentElement;
    if (!s || s.tagName.toLowerCase() !== 'svg') return null;
    return d;
  }

  function serializeDoc() {
    return new XMLSerializer().serializeToString(state.doc.documentElement);
  }

  function isLayer(g) {
    return g.getAttribute('inkscape:groupmode') === 'layer' || /^layer/i.test(g.getAttribute('id') || '');
  }

  function findLayer(root) {
    var found = null;
    (function walk(el) {
      if (found) return;
      for (var i = 0; i < el.children.length; i++) {
        var c = el.children[i];
        if (c.getAttribute && isLayer(c)) { found = c; return; }
        if (found) return;
        walk(c);
      }
    })(root);
    return found;
  }

  function decompose(attr) {
    var res = { tx: 0, ty: 0, rot: 0, rest: '' };
    if (!attr) return res;
    var toks = [], re = /[\w-]+\s*\([^)]*\)/g, m;
    while ((m = re.exec(attr))) toks.push(m[0]);
    var parts = [];
    for (var i = 0; i < toks.length; i++) {
      var t = toks[i];
      var tm = /^translate\(([^)]*)\)$/.exec(t);
      if (tm) {
        var a = tm[1].split(/[\s,]+/).map(Number);
        res.tx = a[0] || 0;
        res.ty = (a.length > 1 ? a[1] : a[0]) || 0;
        continue;
      }
      var rm = /^rotate\(([^)]*)\)$/.exec(t);
      if (rm) {
        var ra = rm[1].split(/[\s,]+/).map(Number);
        if (ra.length <= 1) { res.rot = ra[0] || 0; continue; }
      }
      parts.push(t);
    }
    res.rest = parts.join(' ');
    return res;
  }

  function composeT(t) {
    var s = 'translate(' + r2(t.tx) + ',' + r2(t.ty) + ')';
    if (t.rot) s += ' rotate(' + r2(t.rot) + ')';
    if (t.rest) s += ' ' + t.rest;
    return s;
  }

  function r2(n) { return Math.round(n * 10000) / 10000; }

  function writeT(e) {
    var s = composeT(e.t);
    e.view.setAttribute('transform', s);
    e.doc.setAttribute('transform', s);
  }

  function matchVarToken(str) {
    var m = /\{([A-Za-z_][\w]*)\}/.exec(str) || /%7B([A-Za-z_][\w]*)%7D/.exec(str);
    return m ? (m[1] || m[2]) : null;
  }

  function collectVars() {
    var map = new Map();
    if (state.fields && state.fields.length) {
      state.fields.forEach(function (fd) {
        if (!fd || !/^[A-Za-z_][\w]*$/.test(fd.name || '')) return;
        map.set(fd.name, { name: fd.name, isImage: fd.type === 'Image', count: 0, field: true });
      });
    }
    function add(name, isHref) {
      if (!name || !/^[A-Za-z_][\w]*$/.test(name)) return;
      var v = map.get(name);
      if (!v) { v = { name: name, isImage: false, count: 0 }; map.set(name, v); }
      v.count++;
      if (isHref) v.isImage = true;
      if (/^data_qrcode$/i.test(name)) v.isCode = 'qr';
      else if (/^student_barcode$/i.test(name) || /barcode/i.test(name)) v.isCode = 'barcode';
      else v.isCode = '';
    }
    function scanStr(str, isHref) {
      if (!str) return;
      var m, r1 = /\{([A-Za-z_][\w]*)\}/g;
      while ((m = r1.exec(str))) add(m[1], isHref);
      var r2 = /%7B([A-Za-z_][\w]*)%7D/g;
      while ((m = r2.exec(str))) add(m[1], isHref);
    }
    if (state.doc) {
      (function walk(el) {
      if (el.tagName === 'defs') return;
      var at;
      for (var i = 0; i < el.attributes.length; i++) {
        at = el.attributes[i];
        if (/href/i.test(at.name) && (at.value.indexOf('{') !== -1 || at.value.indexOf('%7B') !== -1)) scanStr(at.value, true);
        else scanStr(at.value, false);
      }
      for (var j = 0; j < el.childNodes.length; j++) {
        var c = el.childNodes[j];
        if (c.nodeType === 3) scanStr(c.nodeValue, false);
      }
      for (var k = 0; k < el.children.length; k++) walk(el.children[k]);
    })(state.doc.documentElement);
    }
    if (state.backDoc) (function walk(el) {
      if (el.tagName === 'defs') return;
      var at;
      for (var i = 0; i < el.attributes.length; i++) {
        at = el.attributes[i];
        if (/href/i.test(at.name) && (at.value.indexOf('{') !== -1 || at.value.indexOf('%7B') !== -1)) scanStr(at.value, true);
        else scanStr(at.value, false);
      }
      for (var j = 0; j < el.childNodes.length; j++) {
        var c = el.childNodes[j];
        if (c.nodeType === 3) scanStr(c.nodeValue, false);
      }
      for (var k = 0; k < el.children.length; k++) walk(el.children[k]);
    })(state.backDoc.documentElement);
    state.vars = map;
    var need = false;
    map.forEach(function (v) { if (!(v.name in state.samples)) need = true; });
    if (need) {
      map.forEach(function (v) {
        if (!(v.name in state.samples)) state.samples[v.name] = SUGGEST[v.name] !== undefined ? SUGGEST[v.name] : '';
      });
    }
    return map.size;
  }

  function applyImgHref(el) {
    var at;
    for (var i = 0; i < el.attributes.length; i++) {
      at = el.attributes[i];
      if (!/href/i.test(at.name)) continue;
      var tok = matchVarToken(at.value);
      if (!tok) continue;
      at.value = hrefSample(tok);
    }
  }

  function replVars(text) {
    return text.replace(/\{([A-Za-z_][\w]*)\}/g, function (m, n) {
      return (state.samples[n] !== undefined && state.samples[n] !== '') ? state.samples[n] : m;
    });
  }

  function fillDOM(root) {
    function walk(el) {
      var i, a;
      for (i = 0; i < el.attributes.length; i++) {
        a = el.attributes[i];
        if (a.value.indexOf('{') === -1 && a.value.indexOf('%7B') === -1) continue;
        var tok = matchVarToken(a.value);
        if (/href/i.test(a.name)) {
          a.value = tok ? hrefSample(tok) : PH;
        } else {
          a.value = replVars(a.value);
        }
      }
      for (i = 0; i < el.childNodes.length; i++) {
        var n = el.childNodes[i];
        if (n.nodeType === 3) {
          var r = replVars(n.nodeValue);
          if (r !== n.nodeValue) n.nodeValue = r;
        }
      }
      for (i = 0; i < el.children.length; i++) walk(el.children[i]);
    }
    walk(root);
    return root;
  }

  function render() {
    if (!state.doc) return;
    var svgD = state.doc.documentElement;
    var view = svgD.cloneNode(true);
    var mount = $('#view');
    state.elements.clear();
    state.order = [];
    state.eid = 0;

    function reg(docNode, viewNode) {
      var id = ++state.eid;
      viewNode.setAttribute('data-cf-eid', id);
      state.elements.set(id, {
        doc: docNode,
        view: viewNode,
        t: decompose(docNode.getAttribute('transform')),
        type: docNode.tagName
      });
      state.order.push(id);
    }

    function walk(d, v) {
      for (var i = 0; i < d.children.length; i++) {
        var dc = d.children[i], vc = v.children[i];
        var tag = dc.tagName;
        if (tag === 'defs') continue;
        if (tag === 'g') {
          if (isLayer(dc)) { walk(dc, vc); continue; }
          if (!dc.children.length) continue;
          applyImgHref(vc);
          reg(dc, vc);
          continue;
        }
        if (EDITABLE.has(tag)) {
          applyImgHref(vc);
          reg(dc, vc);
          walk(dc, vc);
        } else {
          walk(dc, vc);
        }
      }
    }
    walk(svgD, view);

    var rr = resolveDocRect(svgD);
    state.vb = rr.b;
    state.ppu = rr.ppu;

    var i, a;
    for (i = 0; i < svgD.attributes.length; i++) {
      a = svgD.attributes[i];
      if (/^xmlns/i.test(a.name) || a.name === 'width' || a.name === 'height' || a.name === 'id') continue;
      mount.setAttribute(a.name, a.value);
    }

    mount.replaceChildren();
    while (view.firstChild) mount.appendChild(view.firstChild);

    if (!svgD.hasAttribute('viewBox')) {
      mount.setAttribute('viewBox', rr.b.x + ' ' + rr.b.y + ' ' + rr.b.w + ' ' + rr.b.h);
    }
    mount.style.width = (rr.b.w * rr.ppu * state.zoom) + 'px';
    mount.style.height = (rr.b.h * rr.ppu * state.zoom) + 'px';
    if (state.mode === 'preview') fillDOM(mount);
    state.view = mount;
    applyViewSize();

    if (state.selDoc) {
      state.selEid = findEid(state.selDoc);
      if (!state.selEid) state.selDoc = null;
    } else {
      state.selEid = null;
    }
    $('#welcome').classList.toggle('hidden', true);
    syncSel();
    updateStatus();
    document.dispatchEvent(new CustomEvent('cardfly:render'));
  }

  function findEid(docNode) {
    var out = null;
    state.elements.forEach(function (v, id) { if (v.doc === docNode) out = id; });
    return out;
  }

  function applyViewSize() {
    var v = state.view;
    if (!v || !state.doc) return;
    var b = state.vb || vb(state.doc.documentElement);
    var ppu = state.ppu != null ? state.ppu : PX;
    var wPx = b.w * ppu * state.zoom;
    var hPx = b.h * ppu * state.zoom;
    v.style.width = wPx + 'px';
    v.style.height = hPx + 'px';
    $('#view').style.width = wPx + 'px';
    $('#view').style.height = hPx + 'px';
    $('#zoom-val').textContent = Math.round(state.zoom * 100) + '%';
  }

  function loadFromText(text, name) {
    var d = parseSVG(text);
    if (!d) { toast('Could not parse this file as SVG / .kaascan.'); return; }
    state.doc = d;
    state.fileName = (name || 'card').replace(/\.(svg|kaascan)$/i, '');
    state.templateName = '';
    state.backDoc = null;
    state.backFileName = '';
    state.history = [];
    state.hIdx = -1;
    state.selEid = null;
    state.selDoc = null;
    state.mode = 'edit';
    $('#btn-preview').classList.remove('active');
    collectVars();
    state.history.push(serializeDoc());
    state.hIdx = 0;
    render();
    zoomFit();
    updateVarPanel();
    updateLayers();
    updateHistBtns();
    updateStatus();
    saveSamples();
    toast('Loaded "' + state.fileName + '" · ' + state.vars.size + ' variable' + (state.vars.size === 1 ? '' : 's') + ' detected');
  }

  function loadFile(file) {
    var r = new FileReader();
    r.onload = function () { loadFromText(String(r.result), file.name); };
    r.onerror = function () { toast('Could not read file.'); };
    r.readAsText(file);
  }

  function loadSample(name) {
    fetch('samples/' + name).then(function (res) {
      if (!res.ok) throw new Error('http');
      return res.text();
    }).then(function (txt) { loadFromText(txt, name); })
      .catch(function () {
        toast('Could not fetch the sample over file:// — run "python3 -m http.server" inside the editor/ folder and open http://localhost:8000, or use Open File…', 5200);
      });
  }

  function readFileText(file) {
    return new Promise(function (res, rej) {
      var r = new FileReader();
      r.onload = function () { res(String(r.result)); };
      r.onerror = function () { rej(new Error('read failed')); };
      r.readAsText(file);
    });
  }

  function attachBack(backDoc, backName, label) {
    if (!backDoc) return;
    state.backDoc = backDoc;
    state.backFileName = backName || '';
    collectVars();
    updateVarPanel();
    updateStatus();
    toast('' + label + ' loaded · front + back ready', 2600);
  }

  function loadTemplateFolder(files, label) {
    if (!files || !files.length) { toast('That folder is empty.'); return; }
    var svgFiles = Array.prototype.slice.call(files).filter(function (f) {
      return /\.(svg|kaascan)$/i.test(f.name);
    });
    if (!svgFiles.length) { toast('No SVG / .kaascan files found in the folder.'); return; }
    var fronts = svgFiles.filter(function (f) { return /front/i.test(f.name); });
    var backs = svgFiles.filter(function (f) { return /back/i.test(f.name); });
    var rest = svgFiles.filter(function (f) { return !/front|back/i.test(f.name); });
    var front = fronts[0] || rest.shift() || null;
    var back = backs[0] || rest.shift() || null;
    if (!front) {
      toast('No front design found — expected a file whose name contains “front”.', 4600);
      return;
    }
    var fl = label || 'Template';
    readFileText(front).then(function (txt) {
      loadFromText(txt, front.name);
      state.templateName = fl;
      if (back) {
        readFileText(back).then(function (btxt) {
          var bd = parseSVG(btxt);
          if (bd) attachBack(bd, back.name, fl);
          else toast('Front loaded, but back "' + back.name + '" could not be parsed.');
        });
      } else {
        toast('' + fl + ' loaded (no back design found).', 3000);
      }
    }).catch(function () { toast('Could not read the front design.'); });
  }

  function parseTemplateListing(html) {
    var items = [];
    var re = /href="([^"?]+)"/g, m;
    while ((m = re.exec(html))) {
      var nm = decodeURIComponent(m[1]);
      if (nm && !/\?/.test(nm)) items.push(nm);
    }
    return items;
  }

  function listRemoteTemplates() {
    return fetch('templates/').then(function (res) {
      if (!res.ok) throw new Error('http');
      return res.text();
    }).then(parseTemplateListing);
  }

  function refreshTemplateMenu() {
    var box = $('#template-menu');
    box.innerHTML = '';
    var title = document.createElement('div');
    title.className = 'menu-title';
    title.textContent = 'Template folders';
    box.appendChild(title);
    listRemoteTemplates().then(function (items) {
      var names = items.filter(function (n) { return /\/$/.test(n); }).map(function (n) { return n.replace(/\/$/, ''); });
      if (!names.length) throw new Error('none');
      names.forEach(function (n) {
        var b = document.createElement('button');
        b.textContent = n;
        b.addEventListener('click', function () {
          box.classList.add('hidden');
          loadTemplateByName(n);
        });
        box.appendChild(b);
      });
    }).catch(function () {
      var p = document.createElement('p');
      p.className = 'menu-hint';
      p.innerHTML = 'No <code>templates/</code> folder found (or this page is open over <code>file://</code>).<br>Make folders next to the files — like <code>templates/Sunrise/front.svg</code> — or run <code>python3 -m http.server</code> here.<br><b>Use the Folder… button to load a template folder directly.</b>';
      box.appendChild(p);
    });
  }

  function loadTemplateByName(name) {
    var url = 'templates/' + encodeURIComponent(name) + '/';
    fetch(url).then(function (res) {
      if (!res.ok) throw new Error('http');
      return res.text();
    }).then(function (html) {
      var files = parseTemplateListing(html).filter(function (fn) {
        return /\.(svg|kaascan)$/i.test(fn);
      });
      if (!files.length) throw new Error('empty');
      var fronts = files.filter(function (f) { return /front/i.test(f); });
      var backs = files.filter(function (f) { return /back/i.test(f); });
      var rest = files.filter(function (f) { return !/front|back/i.test(f); });
      var front = fronts[0] || rest.shift() || null;
      var back = backs[0] || rest.shift() || null;
      if (!front) throw new Error('nofront');
      function get(fn) {
        return fetch(url + encodeURIComponent(fn)).then(function (r) {
          if (!r.ok) throw new Error('http');
          return r.text();
        });
      }
      get(front).then(function (txt) {
        loadFromText(txt, front);
        state.templateName = name;
        if (back) {
          get(back).then(function (btxt) {
            var bd = parseSVG(btxt);
            if (bd) attachBack(bd, back, 'Template "' + name + '"');
            else toast('Front loaded, but "' + back + '" could not be parsed.', 4200);
          });
        } else {
          toast('Template "' + name + '" loaded — no back design found.', 3200);
        }
      }).catch(function () { toast('Could not load template "' + name + '".', 3600); });
    }).catch(function () {
      toast('Could not list template folder "' + name + '" — is a server running? Use Folder… to load it directly.', 4600);
    });
  }

  function hitTestId(cx, cy) {
    var el = document.elementFromPoint(cx, cy);
    var n = el;
    while (n && n !== document) {
      if (n.nodeType === 1 && n.getAttribute) {
        var id = n.getAttribute('data-cf-eid');
        if (id) return +id;
      }
      n = n.parentNode;
    }
    return null;
  }

  function deltaInSpace(cx, cy, sx, sy, spaceNode) {
    var target = spaceNode || state.view;
    if (!target) return { x: 0, y: 0 };
    var inv;
    try { inv = target.getScreenCTM().inverse(); }
    catch (err) { return { x: 0, y: 0 }; }
    var a = new DOMPoint(sx, sy).matrixTransform(inv);
    var b = new DOMPoint(cx, cy).matrixTransform(inv);
    return { x: b.x - a.x, y: b.y - a.y };
  }

  var center = $('#center');

  center.addEventListener('pointerdown', function (e) {
    if (state.mode !== 'edit' || !state.doc) return;
    var hnd = e.target.closest('.handle');
    if (hnd) { beginResize(e, hnd.getAttribute('data-dir')); return; }
    var id = hitTestId(e.clientX, e.clientY);
    if (id === null) { deselect(); return; }
    select(id);
    beginMove(e);
  });

  function beginMove(e) {
    var id = state.selEid, entry = state.elements.get(id);
    if (!entry) return;
    var sx = e.clientX, sy = e.clientY;
    var st = { tx: entry.t.tx, ty: entry.t.ty };
    var parent = entry.view.parentElement;
    state.gesture = {
      kind: 'move', entry: entry, sx: sx, sy: sy, st: st, parent: parent
    };
    try { entry.view.setPointerCapture(e.pointerId); } catch (err) { }
    window.addEventListener('pointermove', onMoveMove);
    window.addEventListener('pointerup', onMoveUp);
  }

  function onMoveMove(e) {
    var g = state.gesture;
    if (!g || g.kind !== 'move') return;
    var d = deltaInSpace(e.clientX, e.clientY, g.sx, g.sy, g.parent);
    g.entry.t.tx = g.st.tx + d.x;
    g.entry.t.ty = g.st.ty + d.y;
    writeT(g.entry);
    syncSel();
  }

  function onMoveUp() {
    var g = state.gesture;
    state.gesture = null;
    window.removeEventListener('pointermove', onMoveMove);
    window.removeEventListener('pointerup', onMoveUp);
    window.removeEventListener('pointermove', onResizeMove);
    if (!g) return;
    if (g.kind === 'move') {
      writeT(g.entry);
      commitHistory();
      updateProps();
      updateLayers();
    } else if (g.kind === 'resize') {
      finishResize(g);
    }
  }

  function beginResize(e, dir) {
    var entry = state.elements.get(state.selEid);
    if (!entry || !RESIZABLE.has(entry.type)) return;
    var b = entry.view.getBBox();
    var inv, mm;
    try { mm = entry.view.getScreenCTM(); inv = mm.inverse(); }
    catch (err) { return; }
    var flipX = mm.a < 0, flipY = mm.d < 0;
    var dt = entry.doc;
    var x0 = parseFloat(dt.getAttribute('x')) || b.x;
    var y0 = parseFloat(dt.getAttribute('y')) || b.y;
    var w0 = parseFloat(dt.getAttribute('width')) || b.width;
    var h0 = parseFloat(dt.getAttribute('height')) || b.height;
    var ax, ay;
    if (entry.type === 'circle' || entry.type === 'ellipse') {
      ax = b.x + b.width / 2;
      ay = b.y + b.height / 2;
    } else {
      ax = (dir === 'nw' || dir === 'sw') ? (flipX ? x0 : x0 + w0) : (flipX ? x0 + w0 : x0);
      ay = (dir === 'nw' || dir === 'ne') ? (flipY ? y0 : y0 + h0) : (flipY ? y0 + h0 : y0);
    }
    state.gesture = {
      kind: 'resize', entry: entry, dir: dir, inv: inv,
      ax: ax, ay: ay, x0: x0, y0: y0
    };
    window.addEventListener('pointermove', onResizeMove);
    window.addEventListener('pointerup', onMoveUp);
  }

  function onResizeMove(e) {
    var g = state.gesture;
    if (!g || g.kind !== 'resize') return;
    var v = g.entry.view, dt = g.entry.doc, t = g.entry.type;
    var set = function (key, val) { v.setAttribute(key, r2(val)); dt.setAttribute(key, r2(val)); };
    var p = new DOMPoint(e.clientX, e.clientY).matrixTransform(g.inv);
    var MIN = 0.2;
    if (t === 'circle') {
      set('r', clamp(Math.hypot(p.x - g.ax, p.y - g.ay), MIN, 2000));
    } else if (t === 'ellipse') {
      set('rx', clamp(Math.abs(p.x - g.ax), MIN, 2000));
      set('ry', clamp(Math.abs(p.y - g.ay), MIN, 2000));
    } else {
      var w = clamp(Math.abs(p.x - g.ax), MIN, 2000);
      var h = clamp(Math.abs(p.y - g.ay), MIN, 2000);
      set('x', Math.min(g.ax, p.x));
      set('y', Math.min(g.ay, p.y));
      set('width', w);
      set('height', h);
    }
    syncSel();
  }

  function finishResize(g) {
    commitHistory();
    updateProps();
    updateLayers();
    window.removeEventListener('pointermove', onResizeMove);
  }

  function select(eid) {
    state.selEid = eid;
    state.selDoc = state.elements.get(eid).doc;
    syncSel();
    updateProps();
    updateLayers();
  }

  function deselect() {
    state.selEid = null;
    state.selDoc = null;
    syncSel();
    updateProps();
    updateLayers();
  }

  function syncSel() {
    var box = $('#sel');
    if (state.mode !== 'edit' || !state.selEid) { box.style.display = 'none'; return; }
    var e = state.elements.get(state.selEid);
    if (!e || !e.view || !e.view.isConnected) { box.style.display = 'none'; return; }
    var r = e.view.getBoundingClientRect();
    if (!r.width) { box.style.display = 'none'; return; }
    var pr = $('#page').getBoundingClientRect();
    box.style.display = 'block';
    box.style.left = (r.left - pr.left) + 'px';
    box.style.top = (r.top - pr.top) + 'px';
    box.style.width = r.width + 'px';
    box.style.height = r.height + 'px';
    var show = RESIZABLE.has(e.type);
    $$('.handle').forEach(function (h) { h.style.display = show ? 'block' : 'none'; });
  }

  function commitHistory() {
    if (!state.doc) return;
    state.history = state.history.slice(0, state.hIdx + 1);
    state.history.push(serializeDoc());
    state.hIdx = state.history.length - 1;
    if (state.history.length > 80) {
      var over = state.history.length - 80;
      state.history.splice(0, over);
      state.hIdx -= over;
    }
    updateHistBtns();
  }

  function commitAfter() { commitHistory(); }

  var _commitT = null;
  function debCommit(ms) {
    clearTimeout(_commitT);
    _commitT = setTimeout(commitHistory, ms || 400);
  }

  function updateHistBtns() {
    $('#btn-undo').disabled = state.hIdx <= 0;
    $('#btn-redo').disabled = state.hIdx >= state.history.length - 1;
  }

  function undo() {
    if (state.hIdx <= 0) return;
    state.hIdx--;
    restoreAt(state.hIdx);
  }
  function redo() {
    if (state.hIdx >= state.history.length - 1) return;
    state.hIdx++;
    restoreAt(state.hIdx);
  }
  function restoreAt(i) {
    var str = state.history[i];
    var d = parseSVG(str);
    if (!d) { toast('History restore failed'); state.hIdx++; return; }
    state.doc = d;
    collectVars();
    render();
    updateVarPanel();
    updateLayers();
    updateHistBtns();
    updateStatus();
    saveSamples();
  }

  function screenToLayer(cx, cy) {
    var svg = state.view;
    if (!svg) return { x: 0, y: 0 };
    var target = findLayer(svg) || svg;
    var inv;
    try { inv = target.getScreenCTM().inverse(); }
    catch (err) { return { x: 0, y: 0 }; }
    var p = new DOMPoint(cx, cy).matrixTransform(inv);
    return { x: p.x, y: p.y };
  }

  function addElement(type, cx, cy, opts) {
    opts = opts || {};
    if (!state.doc) return;
    var d = state.doc;
    var svgD = d.documentElement;
    var layer = findLayer(svgD) || svgD;
    var p = (cx !== undefined) ? screenToLayer(cx, cy) : centerPointCalc();
    var el = null;
    if (type === 'text') {
      el = d.createElementNS(NS, 'text');
      el.setAttribute('x', r2(p.x));
      el.setAttribute('y', r2(p.y));
      el.setAttribute('font-size', '4');
      el.setAttribute('fill', '#1a1a1a');
      el.setAttribute('font-family', 'Arial');
      var ts = d.createElementNS(NS, 'tspan');
      ts.textContent = opts.content || 'New text';
      el.appendChild(ts);
    } else if (type === 'image') {
      el = d.createElementNS(NS, 'image');
      el.setAttribute('x', r2(p.x - 6));
      el.setAttribute('y', r2(p.y - 8));
      el.setAttribute('width', '12');
      el.setAttribute('height', '16');
      el.setAttribute('preserveAspectRatio', 'none');
      el.setAttributeNS(XLINK, 'xlink:href', opts.href || PH);
    } else if (type === 'rect') {
      el = d.createElementNS(NS, 'rect');
      el.setAttribute('x', r2(p.x - 5));
      el.setAttribute('y', r2(p.y - 3));
      el.setAttribute('width', '10');
      el.setAttribute('height', '6');
      el.setAttribute('rx', '0.5');
      el.setAttribute('fill', '#aad400');
    } else if (type === 'qr' || type === 'barcode') {
      el = d.createElementNS(NS, 'image');
      el.setAttribute('x', r2(p.x - 6));
      el.setAttribute('y', r2(p.y - 6));
      el.setAttribute('width', type === 'qr' ? '12' : '14');
      el.setAttribute('height', type === 'qr' ? '12' : '7');
      el.setAttribute('preserveAspectRatio', 'none');
      el.setAttributeNS(XLINK, 'xlink:href', type === 'qr' ? '{data_qrcode}' : '{student_barcode}');
    }
    layer.appendChild(el);
    if (type === 'qr' || type === 'barcode') collectVars();
    state.selDoc = el;
    commitAfter();
    render();
    updateLayers();
    updateProps();
  }

  function addVarElement(name, cx, cy) {
    var v = state.vars.get(name);
    if (!v) return;
    if (effType(v) === 'Image') {
      addElement('image', cx, cy, { href: '{' + name + '}' });
    } else {
      addElement('text', cx, cy, { content: '{' + name + '}' });
    }
  }

  function centerPointCalc() {
    var r = center.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  }

  function effType(v) {
    return (state.vtype[v.name] && state.vtype[v.name] !== 'Auto') ? state.vtype[v.name] : (v.isImage ? 'Image' : 'Text');
  }

  function deleteSel() {
    if (!state.selEid) return;
    var e = state.elements.get(state.selEid);
    e.doc.remove();
    state.selEid = null;
    state.selDoc = null;
    commitHistory();
    render();
    updateLayers();
    updateProps();
  }

  function duplicateSel() {
    if (!state.selEid) return;
    var e = state.elements.get(state.selEid);
    var c = e.doc.cloneNode(true);
    e.doc.parentNode.insertBefore(c, e.doc.nextSibling);
    state.selDoc = c;
    commitHistory();
    render();
    updateLayers();
    updateProps();
  }

  function reorder(eid, dir) {
    var e = state.elements.get(eid);
    if (!e) return;
    var n = e.doc, p = n.parentNode;
    var sib = dir > 0 ? n.nextElementSibling : n.previousElementSibling;
    if (!sib) return;
    p.insertBefore(n, dir > 0 ? sib.nextElementSibling : sib);
    state.selDoc = n;
    commitHistory();
    render();
    updateLayers();
  }

  function nudge(key) {
    var e = state.elements.get(state.selEid);
    if (!e) return;
    var amt = key.shiftKey ? 2 : 0.5;
    if (key.indexOf('Arrow') === -1) return;
    if (key === 'ArrowLeft') e.t.tx -= amt;
    if (key === 'ArrowRight') e.t.tx += amt;
    if (key === 'ArrowUp') e.t.ty -= amt;
    if (key === 'ArrowDown') e.t.ty += amt;
    writeT(e);
    syncSel();
    updateProps();
    clearTimeout(_commitT);
    debCommit(350);
  }

  function setZoom(z) {
    state.zoom = z;
    applyViewSize();
    syncSel();
  }
  function zoomBy(f) { setZoom(clamp(state.zoom * f, 0.15, 8)); }
  function zoomFit() {
    if (!state.doc) return;
    var b = state.vb || vb(state.doc.documentElement);
    var ppu = state.ppu != null ? state.ppu : PX;
    var W = b.w * ppu, H = b.h * ppu;
    if (!(W > 0) || !(H > 0)) { setZoom(1); return; }
    var c = center;
    var cs = getComputedStyle(c);
    var px = parseFloat(cs.paddingLeft) || 0;
    var py = parseFloat(cs.paddingTop) || 0;
    var room = 20;
    var availW = c.clientWidth - px * 2 - room;
    var availH = c.clientHeight - py * 2 - room;
    var z = Math.min(availW / W, availH / H, 8);
    setZoom(Math.max(z, 0.05));
  }

  function togglePreview() {
    if (!state.doc) return;
    state.mode = state.mode === 'edit' ? 'preview' : 'edit';
    $('#btn-preview').classList.toggle('active', state.mode === 'preview');
    center.classList.toggle('previewing', state.mode === 'preview');
    render();
    toast(state.mode === 'preview' ? 'Preview: sample values shown. Edit mode keeps the original {placeholders}.' : 'Back to editing.', 2200);
  }

  function updateStatus() {
    var info = 'No template loaded';
    if (state.doc) {
      var b = state.vb || vb(state.doc.documentElement);
      var ppu = state.ppu != null ? state.ppu : PX;
      var mmW = b.w * ppu / 96 * 25.4;
      var mmH = b.h * ppu / 96 * 25.4;
      info = state.fileName + ' · ' + r2(mmW) + '×' + r2(mmH) + ' mm · ' + state.elements.size + ' elements · ' + state.vars.size + ' variables';
      if (state.backDoc) info += ' · back: ' + state.backFileName;
    }
    $('#status-info').textContent = info;
  }

  function updateVarPanel() {
    var list = $('#vars-list');
    updateFieldsPanel();
    $('#vars-count').textContent = state.vars.size === 0
      ? 'No variables detected'
      : state.vars.size + ' variable' + (state.vars.size === 1 ? '' : 's') + ' detected';
    var html = '';
    state.vars.forEach(function (v) {
      var t = effType(v);
      var img = t === 'Image';
      var sam = state.samples[v.name] || '';
      var chipCls = img ? 'has-image' : '';
      html += '<div class="vrow" draggable="true" data-var="' + v.name + '">';
      html += '<div class="vhead"><span class="vchip ' + chipCls + '">{' + v.name + '}</span>';
      html += (v.isCode ? '<span class="vchip-badge ' + (v.isCode === 'qr' ? 'is-qr' : 'is-bc') + '">' + (v.isCode === 'qr' ? 'QR' : 'BARCODE') + '</span>' : '');
      html += '<select class="vsel" data-var="' + v.name + '"><option>Auto</option><option' + (t === 'Text' ? ' selected' : '') + '>Text</option><option' + (t === 'Image' ? ' selected' : '') + '>Image</option></select>';
      html += '<span class="vcount">×' + v.count + '</span></div>';
      html += '<div class="vctl" data-ctl="' + v.name + '">';
      if (img) {
        html += (sam ? '<img class="vimg" src="' + sam.replace(/"/g, '&quot;') + '">' : '<span class="vimg"></span>');
        html += (v.isCode ? '<button class="mini-btn v-gen" data-var="' + v.name + '" title="Generate a fresh sample ' + (v.isCode === 'qr' ? 'QR' : 'barcode') + ' for preview">Generate</button>' : '');
        html += '<button class="mini-btn v-file" data-var="' + v.name + '" title="Load an image sample">Image…</button>';
        if (sam) html += '<button class="mini-btn v-clear" data-var="' + v.name + '">✕</button>';
      } else {
        html += '<input class="vinput" data-var="' + v.name + '" placeholder="Sample value for preview & export" value="' + sam.replace(/"/g, '&quot;') + '">';
      }
      html += '</div></div>';
    });
    list.innerHTML = html || '<div class="vempty">No {placeholders} found in this card yet.<br>Type text like {name} into a text layer to create one.</div>';

    list.querySelectorAll('.vrow').forEach(function (row) {
      row.addEventListener('dragstart', function (e) {
        state.dnd = { kind: 'var', name: row.getAttribute('data-var') };
        e.dataTransfer.setData('text/plain', '{' + row.getAttribute('data-var') + '}');
      });
      row.addEventListener('dragend', function () { state.dnd = null; });
    });
    list.querySelectorAll('.vsel').forEach(function (s) {
      s.addEventListener('change', function () {
        state.vtype[s.getAttribute('data-var')] = s.value;
        updateVarPanel();
        updateProps();
        render();
        saveSamples();
      });
    });
    list.querySelectorAll('.vinput').forEach(function (inp) {
      inp.addEventListener('input', function () {
        state.samples[inp.getAttribute('data-var')] = inp.value;
        saveSamples();
      });
      inp.addEventListener('change', function () {
        render();
      });
    });
    list.querySelectorAll('.v-file').forEach(function (b) {
      b.addEventListener('click', function () {
        state._fileVar = b.getAttribute('data-var');
        $('#vfile').click();
      });
    });
    list.querySelectorAll('.v-gen').forEach(function (b) {
      b.addEventListener('click', function () {
        var n = b.getAttribute('data-var');
        if (state.vars.get(n) && state.vars.get(n).isCode) CODE_CACHE[n] = '';
        state.samples[n] = codeSample(n);
        saveSamples();
        updateVarPanel();
        render();
      });
    });
    list.querySelectorAll('.v-clear').forEach(function (b) {
      b.addEventListener('click', function () {
        state.samples[b.getAttribute('data-var')] = '';
        saveSamples();
        updateVarPanel();
        render();
      });
    });
  }

  function updateFieldsPanel() {
    var list = $('#fields-list');
    if (!list) return;
    if (!state.fields.length) {
      list.innerHTML = '<div class="vempty">Define what every card must have — e.g. name, id, school, photo.<br>Then right-click a text or image on the card and pick the variable.</div>';
      return;
    }
    var html = '';
    state.fields.forEach(function (f, i) {
      html += '<div class="frow" data-i="' + i + '">';
      html += '<input class="field-in f-name" value="' + esc(f.name) + '" placeholder="field_name" spellcheck="false">';
      html += '<select class="field-in f-type"><option' + (f.type !== 'Image' ? ' selected' : '') + '>Text</option><option' + (f.type === 'Image' ? ' selected' : '') + '>Image</option></select>';
      html += '<button class="mini-btn danger f-del" title="Remove this required field">✕</button>';
      html += '</div>';
    });
    list.innerHTML = html;
    list.querySelectorAll('.f-del').forEach(function (b) {
      b.addEventListener('click', function () {
        var i = +b.closest('.frow').getAttribute('data-i');
        var f = state.fields[i];
        var arr = state.fields.slice();
        arr.splice(i, 1);
        saveFields(arr);
        collectVars();
        updateVarPanel();
        updateStatus();
        render();
        toast(f.name ? 'Removed field {' + f.name + '}' : 'Field removed.');
      });
    });
    list.querySelectorAll('.f-name').forEach(function (inp) {
      inp.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter') inp.blur();
      });
      inp.addEventListener('change', function () {
        var i = +inp.closest('.frow').getAttribute('data-i');
        var val = inp.value.trim();
        if (!val) { inp.value = state.fields[i].name; return; }
        if (!/^[A-Za-z_][\w]*$/.test(val)) {
          toast('Variable names must be letters, digits and _ (no spaces).', 3600);
          inp.value = state.fields[i].name;
          return;
        }
        if (val !== state.fields[i].name) {
          var arr = state.fields.slice();
          arr[i] = { name: val, type: arr[i].type };
          saveFields(arr);
          collectVars();
          updateVarPanel();
          updateStatus();
          render();
          toast('Renamed to {' + val + '}.');
        }
      });
    });
    list.querySelectorAll('.f-type').forEach(function (sel) {
      sel.addEventListener('change', function () {
        var i = +sel.closest('.frow').getAttribute('data-i');
        var arr = state.fields.slice();
        arr[i] = { name: arr[i].name, type: sel.value };
        saveFields(arr);
        collectVars();
        updateVarPanel();
        updateProps();
        render();
      });
    });
  }

  function saveSamples() {
    try { localStorage.setItem('cf.samples', JSON.stringify(state.samples)); } catch (e) { }
  }
  function loadSamples() {
    try {
      var s = localStorage.getItem('cf.samples');
      if (s) state.samples = JSON.parse(s);
    } catch (e) { }
  }
  function loadFields() {
    try {
      var raw = localStorage.getItem('cf.fields');
      if (!raw) return [];
      var a = JSON.parse(raw);
      if (!Array.isArray(a)) return [];
      return a.filter(function (f) {
        return f && typeof f.name === 'string' && /^[A-Za-z_][\w]*$/.test(f.name);
      }).map(function (f) {
        return { name: f.name, type: f.type === 'Image' ? 'Image' : 'Text' };
      });
    } catch (e) { return []; }
  }
  function saveFields(arr) {
    state.fields = arr;
    try { localStorage.setItem('cf.fields', JSON.stringify(arr)); } catch (e) { }
  }
  function downloadSamples() {
    downloadStr(JSON.stringify(state.samples, null, 2), 'card.variables.json', 'application/json');
  }

  function updateLayers() {
    var list = $('#layers-list');
    var html = '';
    for (var i = 0; i < state.order.length; i++) {
      var id = state.order[i];
      var e = state.elements.get(id);
      if (!e) continue;
      var name = nameFor(e);
      var active = state.selEid === id ? ' active' : '';
      html += '<div class="layer-row' + active + '" data-id="' + id + '">';
      html += '<span class="layer-icon">' + iconFor(e) + '</span>';
      html += '<span class="layer-name">' + name + '</span>';
      html += '<span class="layer-tools">';
      html += '<button class="lt" data-act="up" title="Bring forward">▲</button>';
      html += '<button class="lt" data-act="down" title="Send backward">▼</button>';
      html += '<button class="lt del" data-act="del" title="Delete">✕</button>';
      html += '</span></div>';
    }
    list.innerHTML = html || '<div class="vempty">No layers yet.</div>';
    list.querySelectorAll('.layer-row').forEach(function (row) {
      row.addEventListener('click', function (ev) {
        var id = +row.getAttribute('data-id');
        if (ev.target.closest('[data-act]')) return;
        select(id);
      });
      row.querySelectorAll('[data-act]').forEach(function (b) {
        b.addEventListener('click', function (e) {
          e.stopPropagation();
          var id = +row.getAttribute('data-id');
          var act = b.getAttribute('data-act');
          if (act === 'up') reorder(id, -1);
          else if (act === 'down') reorder(id, 1);
          else { state.selEid = id; state.selDoc = state.elements.get(id).doc; deleteSel(); }
        });
      });
    });
  }

  function nameFor(e) {
    if (e.type === 'text') {
      var txt = '';
      var ts = e.doc.querySelector('tspan');
      txt = (ts && ts.textContent) || e.doc.textContent || '';
      var tok = matchVarToken(txt);
      if (tok) return '<span class="var-tag">{' + tok + '}</span>';
      return txt.replace(/\s+/g, ' ').slice(0, 30) || 'text';
    }
    if (e.type === 'image') {
      var href = '';
      for (var i = 0; i < e.doc.attributes.length; i++) {
        var at = e.doc.attributes[i];
        if (/href/i.test(at.name)) href = at.value;
      }
      var tk = matchVarToken(href);
      if (tk) return '<span class="var-tag">#{' + tk + '}</span>';
      return (e.doc.getAttribute('id') || 'image').slice(0, 20);
    }
    return (e.doc.getAttribute('id') || e.type);
  }

  function iconFor(e) {
    if (e.type === 'text') return 'T';
    if (e.type === 'image') return '▣';
    if (e.type === 'rect') return '▭';
    if (e.type === 'circle') return '●';
    if (e.type === 'ellipse') return '⬭';
    if (e.type === 'line') return '╱';
    if (e.type === 'g') return '▦';
    return '◇';
  }

  function updateProps() {
    var box = $('#props');
    if (!state.doc) {
      box.innerHTML = '<div class="props-empty"><h3>No template loaded</h3>Open an SVG or .kaascan file to start designing.</div>';
      return;
    }
    if (!state.selEid) {
      box.innerHTML = '<div class="props-empty"><h3>Nothing selected</h3>Click any element on the canvas — text, image or shape — to edit its position, size, font, colours and variable binding here. Drag &lbrace;variables&rbrace; from the left panel onto the card to add data fields.</div>';
      return;
    }
    var e = state.elements.get(state.selEid);
    buildProps(box, e);
  }

  function buildProps(box, e) {
    var t = e.type;
    var html = '<div class="sec"><div class="sec-title">Position & rotation</div><div class="grid2">';
    html += fieldNum('X', 'prop-x', r2(e.t.tx));
    html += fieldNum('Y', 'prop-y', r2(e.t.ty));
    html += fieldNum('Rotate °', 'prop-rot', r2(e.t.rot));
    html += fieldNum('Opacity', 'prop-op', opacityOf(e));
    html += '</div></div>';

    if (t === 'text') {
      var ts = e.doc.querySelector('tspan');
      var txt = (ts && ts.textContent) || '';
      var fs = parseFloat(getComputedStyle(e.view).fontSize) || 4;
      var ff = getComputedStyle(e.view).fontFamily;
      var col = getComputedStyle(e.view).fill;
      var tok = matchVarToken(txt);
      html += '<div class="sec"><div class="sec-title">Text</div>';
      if (state.vars.size) {
        html += '<div class="grow" style="margin-bottom:8px"><label>Variable</label><select class="field-in prop-bind"><option value="">— none —</option>';
        state.vars.forEach(function (v) {
          html += '<option value="' + v.name + '"' + (tok === v.name ? ' selected' : '') + '>{' + v.name + '}</option>';
        });
        html += '</select></div>';
      }
      var bindStr = tok ? 'Tok' : '';
      html += '<textarea class="field-in prop-content" rows="2">' + esc(txt) + '</textarea>';
      html += '<div class="grid2" style="margin-top:8px">';
      html += '<div class="grow" style="grid-template-columns:1fr"><input class="field-in" list="cf-fonts" id="prop-font" value="' + esc(ff) + '" title="Font family"></div>';
      html += fieldNum('Size', 'prop-size', r2(fs));
      html += '</div><datalist id="cf-fonts"><option value="Minigap"><option value="Z003"><option value="Arial"><option value="Inter"><option value="Times New Roman"><option value="Courier New"><option value="Verdana"><option value="Georgia"></datalist>';
      html += '<div class="pill-row" style="margin-top:8px"><button class="mini-btn prop-bold">B</button><button class="mini-btn prop-italic">I</button><input type="color" class="field-in prop-fill" value="' + toHex(col) + '" style="width:42px;padding:2px;height:27px"></div>';
      html += '</div>';
    }

    if (t === 'image' || t === 'rect') {
      var w = attrOf(e, 'width'), h = attrOf(e, 'height');
      html += '<div class="sec"><div class="sec-title">Size</div><div class="grid2">';
      html += fieldNum('Width', 'prop-w', w);
      html += fieldNum('Height', 'prop-h', h);
      html += '</div></div>';
    }
    if (t === 'circle') {
      var r = attrOf(e, 'r');
      html += '<div class="sec"><div class="sec-title">Size</div><div class="grid2">' + fieldNum('Radius', 'prop-r', r) + '</div></div>';
    }
    if (t === 'ellipse') {
      html += '<div class="sec"><div class="sec-title">Size</div><div class="grid2">' +
        fieldNum('RX', 'prop-rx', attrOf(e, 'rx')) + fieldNum('RY', 'prop-ry', attrOf(e, 'ry')) + '</div></div>';
    }

    if (t === 'rect') {
      html += '<div class="sec"><div class="sec-title">Shape</div><div class="grid2">' +
        fieldNum('Corner radius', 'prop-rx', attrOf(e, 'rx')) +
        '<div class="grow" style="grid-template-columns:1fr"><input type="color" class="field-in prop-fill" value="' + toHex(fillOf(e)) + '" title="Fill"></div>' +
        '</div></div>';
    }
    if (t === 'image') {
      var href = hrefOf(e);
      var tk = matchVarToken(href);
      html += '<div class="sec"><div class="sec-title">Image</div>';
      html += '<div class="grow" style="margin-bottom:8px"><label>Source</label><span class="pill" style="font-size:10.5px;overflow:hidden;text-overflow:ellipsis">' + esc(shortSrc(href)) + '</span></div>';
      html += '<div class="btn-row" style="margin-top:0"><button class="btn prop-file">Choose image…</button><button class="btn prop-clear" ' + (tk ? '' : 'disabled') + '>Reset to {' + (tk || 'var') + '}</button></div>';
      html += '<div class="grow" style="margin-top:10px"><label>fit</label><select class="field-in prop-par"><option' + (attrOf(e, 'preserveAspectRatio') === 'none' ? ' selected' : '') + ' value="none">stretch (none)</option><option' + (attrOf(e, 'preserveAspectRatio') === 'xMidYMid meet' ? ' selected' : '') + ' value="xMidYMid meet">fit (meet)</option><option' + (attrOf(e, 'preserveAspectRatio') === 'xMidYMid slice' ? ' selected' : '') + ' value="xMidYMid slice">cover (slice)</option></select></div>';
      html += '</div>';
    }
    if (t === 'path' || t === 'circle' || t === 'ellipse' || t === 'polygon' || t === 'polyline' || t === 'line') {
      html += '<div class="sec"><div class="sec-title">Shape</div><div class="grow"><label>Fill</label><input type="color" class="field-in prop-fill" value="' + toHex(fillOf(e)) + '"></div></div>';
    }

    html += '<div class="btn-row"><button class="btn btn-wide prop-dup">⧉ Duplicate</button><button class="btn btn-wide danger prop-del">🗑 Delete</button></div>';
    box.innerHTML = html;
    bindProps(box, e);
  }

  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function fieldNum(label, id, val) {
    return '<div class="grow"><label>' + label + '</label><input class="field-in" id="' + id + '" type="number" step="0.01" data-init="' + (isFinite(val) ? val : '') + '" value="' + (isFinite(val) ? val : '') + '"></div>';
  }

  function attrOf(e, name) {
    var v = e.doc.getAttribute(name);
    if (v === null) v = e.doc.style && e.doc.style.getPropertyValue && e.doc.style.getPropertyValue(name);
    return v === null || v === '' || v === undefined ? '' : parseFloat(v);
  }
  function fillOf(e) {
    var c = e.doc.getAttribute('fill');
    if (!c) c = getComputedStyle(e.view).fill;
    return c || '#000000';
  }
  function opacityOf(e) {
    var o = e.doc.getAttribute('opacity');
    if (!o) {
      var cs = getComputedStyle(e.view);
      o = cs.opacity;
    }
    return parseFloat(o) || 1;
  }
  function hrefOf(e) {
    for (var i = 0; i < e.doc.attributes.length; i++) {
      var at = e.doc.attributes[i];
      if (/href/i.test(at.name)) return at.value;
    }
    return '';
  }
  function shortSrc(h) {
    if (/^data:/.test(h)) return 'data:image/… (' + Math.round(h.length / 1024) + ' KB embedded)';
    return h.slice(0, 60);
  }
  function toHex(c) {
    if (!c || c === 'none') return '#000000';
    if (/^#/.test(c)) return c.length === 4 ? '#' + c[1] + c[1] + c[2] + c[2] + c[3] + c[3] : c;
    var m = /rgba?\(([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)/.exec(c);
    if (m) {
      function h(n2) { n2 = clamp(Math.round(parseFloat(n2)), 0, 255); return ('0' + n2.toString(16)).slice(-2); }
      return '#' + h(m[1]) + h(m[2]) + h(m[3]);
    }
    return '#000000';
  }
  function toRgba(hex) {
    hex = hex.replace('#', '');
    if (hex.length === 3) hex = hex[0] + hex[0] + hex[1] + hex[1] + hex[2] + hex[2];
    var r = parseInt(hex.slice(0, 2), 16), g = parseInt(hex.slice(2, 4), 16), b = parseInt(hex.slice(4, 6), 16);
    return 'rgb(' + r + ', ' + g + ', ' + b + ')';
  }

  function bindProps(box, e) {
    function getF(id) { return parseFloat(box.querySelector('#' + id).value); }
    function setBoth(key, val) {
      e.view.setAttribute(key, val);
      e.doc.setAttribute(key, val);
      syncSel();
    }
    function fieldChanged(fn) {
      return function (ev) {
        var inp = ev.currentTarget;
        if (inp.value === inp.getAttribute('data-init')) return;
        fn(); commitHistory(); updateProps();
      };
    }

    var x = box.querySelector('#prop-x');
    if (x) x.addEventListener('change', fieldChanged(function () { e.t.tx = getF('prop-x'); writeT(e); }));
    var y = box.querySelector('#prop-y');
    if (y) y.addEventListener('change', fieldChanged(function () { e.t.ty = getF('prop-y'); writeT(e); }));
    var rot = box.querySelector('#prop-rot');
    if (rot) rot.addEventListener('change', fieldChanged(function () { e.t.rot = getF('prop-rot'); writeT(e); }));
    var op = box.querySelector('#prop-op');
    if (op) op.addEventListener('change', fieldChanged(function () {
      var v = clamp(opacityOf(e), 0, 1);
      e.view.setAttribute('opacity', v);
      e.doc.setAttribute('opacity', v);
    }));

    var bind = box.querySelector('.prop-bind');
    if (bind) bind.addEventListener('change', function () {
      var v = bind.value;
      var ts = e.doc.querySelector('tspan') || e.doc;
      var tv = e.view.querySelector('tspan') || e.view;
      ts.textContent = v ? '{' + v + '}' : '';
      tv.textContent = v ? '{' + v + '}' : '';
      commitAfter();
      updateProps();
      updateLayers();
      syncSel();
    });

    var content = box.querySelector('.prop-content');
    if (content) {
      var last = content.value;
      content.addEventListener('keyup', function () {
        if (content.value === last) return;
        last = content.value;
        var ts = e.doc.querySelector('tspan');
        if (ts) ts.textContent = content.value;
        else e.doc.textContent = content.value;
        var v2 = e.view.querySelector('tspan');
        if (v2) v2.textContent = content.value;
        else e.view.textContent = content.value;
        clearTimeout(_commitT);
        debCommit(400);
        updateLayers();
      });
    }

    var font = box.querySelector('#prop-font');
    if (font) font.addEventListener('change', function () {
      e.view.style.fontFamily = font.value;
      e.doc.style.fontFamily = font.value;
      commitAfter(); updateProps();
    });
    var size = box.querySelector('#prop-size');
    if (size) size.addEventListener('change', function () {
      e.view.style.fontSize = getF('prop-size') + 'px';
      e.doc.style.fontSize = getF('prop-size') + 'px';
      commitAfter(); updateProps();
    });
    var bold = box.querySelector('.prop-bold');
    if (bold) {
      var bN = getComputedStyle(e.view).fontWeight === '700' || fontWeightBiz(e.doc) === 'bold';
      if (bN) bold.classList.add('active');
      bold.addEventListener('click', function () {
        var fw = getComputedStyle(e.view).fontWeight === '700' ? '400' : '700';
        e.view.style.fontWeight = fw; e.doc.style.fontWeight = fw;
        commitAfter(); updateProps();
      });
    }
    var ital = box.querySelector('.prop-italic');
    if (ital) {
      var iN = getComputedStyle(e.view).fontStyle === 'italic';
      if (iN) ital.classList.add('active');
      ital.addEventListener('click', function () {
        var fs2 = getComputedStyle(e.view).fontStyle === 'italic' ? 'normal' : 'italic';
        e.view.style.fontStyle = fs2; e.doc.style.fontStyle = fs2;
        commitAfter(); updateProps();
      });
    }
    var fill = box.querySelector('.prop-fill');
    if (fill) fill.addEventListener('change', function () {
      var c = toRgba(fill.value);
      e.view.style.fill = c;
      e.doc.style.fill = c;
      commitAfter();
    });

    var w = box.querySelector('#prop-w');
    if (w) w.addEventListener('change', fieldChanged(function () { setBoth('width', getF('prop-w')); }));
    var h = box.querySelector('#prop-h');
    if (h) h.addEventListener('change', fieldChanged(function () { setBoth('height', getF('prop-h')); }));
    var rr = box.querySelector('#prop-r');
    if (rr) rr.addEventListener('change', fieldChanged(function () { setBoth('r', getF('prop-r')); }));
    var rx = box.querySelector('#prop-rx');
    if (rx) rx.addEventListener('change', fieldChanged(function () { setBoth('rx', getF('prop-rx')); }));
    var ry = box.querySelector('#prop-ry');
    if (ry) ry.addEventListener('change', fieldChanged(function () { setBoth('ry', getF('prop-ry')); }));

    var par = box.querySelector('.prop-par');
    if (par) par.addEventListener('change', function () { setBoth('preserveAspectRatio', par.value); commitAfter(); });
    var fileBtn = box.querySelector('.prop-file');
    if (fileBtn) fileBtn.addEventListener('click', function () { state._fileEl = e; $('#imgfile').click(); });

    var clearB = box.querySelector('.prop-clear');
    if (clearB) clearB.addEventListener('click', function () {
      var tk = matchVarToken(hrefOf(e)) || 'image_base64';
      setAttrNS(e, 'xlink:href', '{' + tk + '}');
      commitAfter();
      updateProps();
      render();
    });

    var dup = box.querySelector('.prop-dup');
    if (dup) dup.addEventListener('click', duplicateSel);
    var del = box.querySelector('.prop-del');
    if (del) del.addEventListener('click', deleteSel);
  }

  function fontWeightBiz(doc) {
    return doc.getAttribute('font-weight') || '';
  }

  function setAttrNS(e, qname, val) {
    e.view.setAttributeNS(XLINK, qname, val);
    e.doc.setAttributeNS(XLINK, qname, val);
    syncSel();
  }

  var imgfile = document.createElement('input');
  imgfile.id = 'imgfile';
  imgfile.type = 'file';
  imgfile.accept = 'image/*';
  imgfile.style.display = 'none';
  document.body.appendChild(imgfile);
  imgfile.addEventListener('change', function () {
    var f = imgfile.files[0];
    if (!f) return;
    var r = new FileReader();
    r.onload = function () {
      var data = String(r.result);
      if (state._fileVar) {
        state.samples[state._fileVar] = data;
        state._fileVar = null;
        saveSamples();
        updateVarPanel();
        render();
        toast('Sample image embedded for {variable}.');
      } else if (state._fileEl) {
        var e = state._fileEl;
        setAttrNS(e, 'xlink:href', data);
        commitAfter();
        state._fileEl = null;
        updateProps();
        render();
        toast('Image embedded into the element.');
      }
    };
    r.readAsDataURL(f);
    imgfile.value = '';
  });

  var vfile = document.createElement('input');
  vfile.id = 'vfile';
  vfile.type = 'file';
  vfile.accept = 'image/*';
  vfile.style.display = 'none';
  document.body.appendChild(vfile);
  document.addEventListener('change', function (e) {
    if (e.target !== vfile) return;
    var f = vfile.files[0];
    if (!f) return;
    var r = new FileReader();
    r.onload = function () {
      state.samples[state._fileVar] = String(r.result);
      state._fileVar = null;
      saveSamples();
      updateVarPanel();
      render();
      toast('Image sample set.');
    };
    r.readAsDataURL(f);
    vfile.value = '';
  });

  function filledDocString() {
    var d = parseSVG(serializeDoc());
    if (!d) return serializeDoc();
    fillDOM(d.documentElement);
    return new XMLSerializer().serializeToString(d.documentElement);
  }

  function backFilledString() {
    if (!state.backDoc) return null;
    var str;
    try { str = new XMLSerializer().serializeToString(state.backDoc.documentElement); }
    catch (e) { return null; }
    var d = parseSVG(str);
    if (!d || !d.documentElement) return null;
    fillDOM(d.documentElement);
    try { return new XMLSerializer().serializeToString(d.documentElement); }
    catch (e) { return null; }
  }

  function backDesignSvg() {
    var real = backFilledString();
    if (real) return real;
    var b = state.vb ? state.vb : { x: 0, y: 0, w: 86, h: 54 };
    var w = r2(b.w), h = r2(b.h);
    var cx = r2(b.w / 2), cy = r2(b.h / 2);
    return ['<svg xmlns="http://www.w3.org/2000/svg" width="' + w + '" height="' + h + '" viewBox="' + b.x + ' ' + b.y + ' ' + w + ' ' + h + '">',
      '<rect width="' + w + '" height="' + h + '" fill="#F0C020"/>',
      '<circle cx="' + r2(b.w * 0.16) + '" cy="' + r2(b.h * 0.2) + '" r="' + r2(b.h * 0.16) + '" fill="#D02020"/>',
      '<polygon points="' + r2(b.w * 0.82) + ',' + r2(b.h * 0.62) + ' ' + w + ',' + h + ' ' + r2(b.w * 0.62) + ',' + h + '" fill="#1040C0"/>',
      '<rect x="' + r2(b.w * 0.36) + '" y="' + r2(b.h * 0.22) + '" width="' + r2(b.w * 0.28) + '" height="' + r2(b.w * 0.28) + '" fill="#121212" transform="rotate(45 ' + cx + ' ' + cy + ')"/>',
      '<text x="' + cx + '" y="' + r2(b.h * 0.44) + '" text-anchor="middle" font-family="Arial, sans-serif" font-weight="bold" font-size="' + r2(b.h * 0.11) + '" fill="#121212">STUDENT CARD</text>',
      '<text x="' + cx + '" y="' + r2(b.h * 0.55) + '" text-anchor="middle" font-family="Arial, sans-serif" letter-spacing="2" font-size="' + r2(b.h * 0.07) + '" fill="#121212">CARDFLY STUDIO</text>',
      '<rect x="' + r2(b.w * 0.03) + '" y="' + r2(b.h * 0.03) + '" width="' + r2(b.w * 0.94) + '" height="' + r2(b.h * 0.94) + '" fill="none" stroke="#121212" stroke-width="' + r2(b.h * 0.03) + '"/>'
    ].join('');
  }

  function downloadStr(str, name, type) {
    downloadBlob(new Blob([str], { type: type || 'image/svg+xml' }), name);
  }
  function downloadBlob(blob, name) {
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  }

  function exportKind(kind) {
    if (!state.doc) { toast('Load a template first.'); return; }
    var base = state.fileName;
    if (kind === 'svg') {
      downloadStr(serializeDoc(), base + '.svg');
      toast('Exported ' + base + '.svg (placeholders preserved).');
    } else if (kind === 'svg-filled') {
      downloadStr(filledDocString(), base + '.filled.svg');
      toast('Exported ' + base + '.filled.svg (values filled in).');
    } else if (kind === 'png') {
      exportPNG();
    }
  }

  function exportPNG() {
    var fill = filledDocString();
    var d = parseSVG(fill);
    if (!d) { toast('PNG export failed'); return; }
    var b = vb(d.documentElement);
    var scale = 10;
    var s = d.documentElement;
    var rr = resolveDocRect(s);
    var W0 = rr.b.w * rr.ppu, H0 = rr.b.h * rr.ppu;
    if (!(W0 > 0) || !(H0 > 0)) { toast('PNG export failed — card has no size'); return; }
    var W = Math.round(W0 * scale), H = Math.round(H0 * scale);
    if (!s.hasAttribute('viewBox')) {
      s.setAttribute('viewBox', rr.b.x + ' ' + rr.b.y + ' ' + rr.b.w + ' ' + rr.b.h);
    }
    s.setAttribute('width', W);
    s.setAttribute('height', H);
    s.setAttribute('viewBox', b.x + ' ' + b.y + ' ' + b.w + ' ' + b.h);
    var final = new XMLSerializer().serializeToString(s);
    var url = URL.createObjectURL(new Blob([final], { type: 'image/svg+xml' }));
    var img = new Image();
    img.onload = function () {
      var c = document.createElement('canvas');
      c.width = W; c.height = H;
      var ctx = c.getContext('2d');
      ctx.drawImage(img, 0, 0, W, H);
      URL.revokeObjectURL(url);
      c.toBlob(function (blob) {
        if (!blob) { toast('PNG export failed'); return; }
        downloadBlob(blob, state.fileName + '.png');
        toast('Exported ' + state.fileName + '.png (' + W + '×' + H + ' px).');
      }, 'image/png');
    };
    img.onerror = function () {
      URL.revokeObjectURL(url);
      toast('PNG export failed — some images could not be rasterised.', 4200);
    };
    img.src = url;
  }

  document.addEventListener('dragover', function (e) { e.preventDefault(); });
  document.addEventListener('drop', function (e) { e.preventDefault(); });
  center.addEventListener('dragover', function (e) {
    e.preventDefault();
    if (state.mode === 'edit') center.classList.add('dragging');
  });
  center.addEventListener('dragleave', function (e) {
    if (!center.contains(e.relatedTarget)) center.classList.remove('dragging');
  });
  center.addEventListener('drop', function (e) {
    e.preventDefault();
    center.classList.remove('dragging');
    if (!state.doc) {
      var fl = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (fl && /\.(svg|kaascan)$/i.test(fl.name)) { loadFile(fl); return; }
    }
    if (state.mode !== 'edit' || !state.doc) return;
    var files = e.dataTransfer && e.dataTransfer.files;
    var f = files && files[0];
    if (f) {
      if (/\.(png|jpe?g|gif|webp|bmp|avif)$/i.test(f.name)) {
        var rd = new FileReader();
        rd.onload = function () {
          addElement('image', e.clientX, e.clientY, { href: String(rd.result) });
          toast('Image added — drag to position, or right-click to bind it to a variable.');
        };
        rd.onerror = function () { toast('Could not read that image file.'); };
        rd.readAsDataURL(f);
        return;
      }
      if (/\.(svg|kaascan)$/i.test(f.name)) { loadFile(f); return; }
    }
    var payload = state.dnd;
    if (payload) {
      if (payload.kind === 'var') addVarElement(payload.name, e.clientX, e.clientY);
      else if (payload.kind === 'new') addElement(payload.type, e.clientX, e.clientY);
      state.dnd = null;
    } else if (state.selEid) {
      moveToDrop(e.clientX, e.clientY);
    }
  });

  function moveToDrop(cx, cy) {
    var e = state.elements.get(state.selEid);
    if (!e) return;
    var target = screenToLayer(cx, cy);
    var r = e.view.getBoundingClientRect();
    var cur = { x: r.left + r.width / 2, y: r.top + r.height / 2 };
    var curLayer = screenToLayer(cur.x, cur.y);
    e.t.tx += target.x - curLayer.x;
    e.t.ty += target.y - curLayer.y;
    writeT(e);
    commitHistory();
    syncSel();
    updateProps();
  }

  /* Right-click any layer on the card → bind it to one of the card's variables */
  var ctx = document.createElement('div');
  ctx.id = 'ctx-menu';
  ctx.className = 'hidden';
  document.body.appendChild(ctx);

  function closeCtx() {
    ctx.classList.add('hidden');
    ctx.innerHTML = '';
  }

  function boundToken(entry) {
    if (!entry) return null;
    if (entry.type === 'text') {
      var ts = entry.doc.querySelector('tspan') || entry.doc;
      return matchVarToken((ts && ts.textContent) || '');
    }
    if (entry.type === 'image') return matchVarToken(hrefOf(entry));
    return null;
  }

  function buildCtxMenu(id, x, y) {
    var entry = state.elements.get(id);
    if (!entry) return;
    var isBindable = entry.type === 'text' || entry.type === 'image';
    var vars = [];
    state.vars.forEach(function (v) { vars.push(v); });
    var curTok = boundToken(entry);
    var html = '<div class="ctx-title">' + esc(entry.type) + ' layer' + (curTok ? ' · bound to <span class="tok">' + curTok + '</span>' : '') + '</div>';
    if (!isBindable) {
      html += '<div class="ctx-empty">Only text and image layers can hold a variable.</div>';
    } else if (!vars.length) {
      html += '<div class="ctx-empty">No variables yet.<br>Add required fields in the Variables tab, then bind them here.</div>';
    } else {
      html += '<div class="ctx-hd">Bind to variable</div>';
      vars.forEach(function (v) {
        var on = curTok === v.name ? ' on' : '';
        html += '<button data-act="bind" data-var="' + v.name + '" class="' + on + '">' +
          '<span class="cico">' + (v.isImage ? '▣' : 'A') + '</span>' +
          '<span class="tok">{' + v.name + '}</span>' +
          (v.count ? '<span class="cused">·' + v.count + '</span>' : '<span class="cnew">new</span>') +
          '</button>';
      });
      if (curTok) {
        html += '<div class="ctx-sep"></div><button data-act="unbind" data-var="' + curTok + '"><span class="tok">✕ Unbind {' + curTok + '}</span></button>';
      }
    }
    ctx.innerHTML = html;
    ctx.onclick = function (ev) {
      var b = ev.target.closest('[data-act]');
      if (!b) return;
      var act = b.getAttribute('data-act');
      closeCtx();
      if (act === 'bind') bindVarToElement(id, b.getAttribute('data-var'));
      else unbindVarFromElement(id);
    };
    ctx.classList.remove('hidden');
    var r = ctx.getBoundingClientRect();
    ctx.style.left = Math.max(4, Math.min(x, window.innerWidth - r.width - 10)) + 'px';
    ctx.style.top = Math.max(4, Math.min(y, window.innerHeight - r.height - 10)) + 'px';
  }

  function bindVarToElement(id, name) {
    var entry = state.elements.get(id);
    if (!entry || !name) return;
    var docEl = entry.doc, viewEl = entry.view;
    if (entry.type === 'text') {
      var ts = docEl.querySelector('tspan') || docEl;
      var tv = viewEl.querySelector('tspan') || viewEl;
      ts.textContent = '{' + name + '}';
      tv.textContent = '{' + name + '}';
    } else if (entry.type === 'image') {
      setAttrNS(entry, 'xlink:href', '{' + name + '}');
    } else return;
    commitHistory();
    collectVars();
    render();
    updateVarPanel();
    updateLayers();
    updateProps();
    updateStatus();
    syncSel();
    toast('Bound {' + name + '} — set its sample value in the Variables tab.', 3000);
  }

  function unbindVarFromElement(id) {
    var entry = state.elements.get(id);
    if (!entry) return;
    if (entry.type === 'text') {
      var ts = entry.doc.querySelector('tspan') || entry.doc;
      var tv = entry.view.querySelector('tspan') || entry.view;
      ts.textContent = '';
      tv.textContent = '';
    } else if (entry.type === 'image') {
      setAttrNS(entry, 'xlink:href', PH);
    } else return;
    commitHistory();
    collectVars();
    render();
    updateVarPanel();
    updateLayers();
    updateProps();
    updateStatus();
    syncSel();
    toast('Unbound.');
  }

  center.addEventListener('contextmenu', function (e) {
    if (state.mode !== 'edit' || !state.doc) { closeCtx(); return; }
    var id = hitTestId(e.clientX, e.clientY);
    if (id === null) { closeCtx(); return; }
    e.preventDefault();
    select(id);
    buildCtxMenu(id, e.clientX, e.clientY);
  });
  document.addEventListener('click', function (e) {
    if (!e.target.closest('#ctx-menu')) closeCtx();
  });
  document.addEventListener('contextmenu', function (e) {
    if (!e.target.closest('#center')) closeCtx();
  });

  $$('.pal-item').forEach(function (it) {
    it.addEventListener('dragstart', function (e) {
      state.dnd = { kind: 'new', type: it.getAttribute('data-type') };
      e.dataTransfer.setData('text/plain', it.getAttribute('data-type'));
    });
    it.addEventListener('dragend', function () { state.dnd = null; });
    it.addEventListener('click', function () {
      if (state.doc) addElement(it.getAttribute('data-type'));
    });
  });

  $('#open-file').addEventListener('click', function () { $('#file-input').click(); });
  $('#btn-open-big').addEventListener('click', function () { $('#file-input').click(); });
  $('#btn-open-folder-big').addEventListener('click', function () { $('#folder-input').click(); });
  $('#btn-folder').addEventListener('click', function () { $('#folder-input').click(); });
  $('#folder-input').addEventListener('change', function () {
    var fl = this.files && this.files[0];
    var label = fl && fl.webkitRelativePath ? fl.webkitRelativePath.split('/')[0] : '';
    var files = this.files;
    this.value = '';
    loadTemplateFolder(files, label || undefined);
  });
  $('#btn-templates').addEventListener('click', function (e) {
    e.stopPropagation();
    var box = $('#template-menu');
    if (box.classList.contains('hidden')) {
      refreshTemplateMenu();
      box.classList.remove('hidden');
    } else {
      box.classList.add('hidden');
    }
  });
  document.addEventListener('click', function (e) {
    if (!e.target.closest('#btn-templates') && !e.target.closest('#template-menu')) {
      $('#template-menu').classList.add('hidden');
    }
  });
  $('#template-menu').addEventListener('click', function (e) { e.stopPropagation(); });
  $('#sample-select').addEventListener('change', function (e) {
    var v = e.target.value;
    if (v) { loadSample(v); e.target.value = ''; }
  });
  $('#btn-sample-front').addEventListener('click', function () { loadSample('front.card.kaascan'); });
  $('#btn-sample-back').addEventListener('click', function () { loadSample('back.card.kaascan'); });
  $('#file-input').addEventListener('change', function () {
    var f = this.files[0];
    if (f) loadFile(f);
    this.value = '';
  });

  $('#btn-undo').addEventListener('click', undo);
  $('#btn-redo').addEventListener('click', redo);
  $('#btn-delete').addEventListener('click', deleteSel);
  $('#btn-duplicate').addEventListener('click', duplicateSel);
  $('#btn-preview').addEventListener('click', togglePreview);
  $('#btn-docs').addEventListener('click', function () {
    window.open('docs.html', '_blank');
  });
  $('#btn-rescan').addEventListener('click', function () {
    collectVars();
    updateVarPanel();
    render();
    toast('Re-scanned: ' + state.vars.size + ' variable(s).');
  });
  $('#btn-add-field').addEventListener('click', function () {
    var arr = state.fields.slice();
    arr.push({ name: '', type: 'Text' });
    saveFields(arr);
    updateVarPanel();
    var inp = $('#fields-list .frow:last-child .f-name');
    if (inp) inp.focus();
  });
  $('#btn-vjson').addEventListener('click', downloadSamples);

  /* ---- Bridge: fetch the cardgen contract and import its variables ---- */
  function bridgeListen() {
    var url = $('#bridge-url').value.trim();
    var status = $('#bridge-status');
    if (!url) { status.textContent = 'Enter a bridge URL.'; status.className = 'bridge-status err'; return; }
    status.textContent = 'Fetching contract\u2026'; status.className = 'bridge-status';
    fetch(url, { mode: 'cors' })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (data) {
        var contracts = (data && data.contracts) || [];
        var vars = [];
        if (contracts.length) {
          var first = contracts[0];
          vars = (first && first.variables) || [];
        } else if (data && data.variables) {
          vars = data.variables;
        }
        var fields = vars.map(function (v) {
          return { name: v.name, type: v.type === 'Image' ? 'Image' : 'Text' };
        });
        if (!fields.length) { status.textContent = 'No variables found in the contract.'; status.className = 'bridge-status err'; return; }
        saveFields(fields);
        updateFieldsPanel();
        collectVars();
        updateVarPanel();
        updateStatus();
        render();
        status.textContent = 'Loaded ' + fields.length + ' variable(s) from bridge.';
        status.className = 'bridge-status ok';
        toast('Bridge loaded ' + fields.length + ' variable(s).');
      })
      .catch(function (err) {
        status.textContent = 'Bridge error: ' + (err.message || err);
        status.className = 'bridge-status err';
      });
  }
  $('#btn-bridge-listen').addEventListener('click', bridgeListen);
  $('#btn-bridge-docs').addEventListener('click', function () {
    window.open('docs.html', '_blank');
  });

  $('#btn-export').addEventListener('click', function (e) {
    e.stopPropagation();
    $('#export-menu').classList.toggle('hidden');
  });
  document.addEventListener('click', function (e) {
    if (!e.target.closest('#btn-export')) $('#export-menu').classList.add('hidden');
  });
  $('#export-menu').addEventListener('click', function (e) {
    var b = e.target.closest('[data-export]');
    if (!b) return;
    $('#export-menu').classList.add('hidden');
    exportKind(b.getAttribute('data-export'));
  });

  /* Save-to-SDK — write the current front (+ back) straight into the SDK's
     templates_base/ via the CardFly server, so generate_card() can use it. */
  function saveToSdk() {
    if (!state.doc) { toast('Load a template first.'); return; }
    var name = (state.templateName || state.fileName || '').trim();
    if (!name) { name = window.prompt('New SDK template name:').trim(); }
    name = (name || '').replace(/\.(svg|kaascan)$/i, '').trim();
    if (!name) { toast('Save cancelled — no template name.'); return; }

    var files = [{ side: 'front', filename: 'front.card.kaascan', content: serializeDoc() }];
    if (state.backDoc && state.backDoc.documentElement) {
      var backText;
      try {
        backText = new XMLSerializer().serializeToString(state.backDoc.documentElement);
      } catch (e) { backText = null; }
      if (backText) files.push({ side: 'back', filename: 'back.card.kaascan', content: backText });
    }
    var btn = $('#btn-save-sdk');
    var orig = btn.textContent;
    btn.textContent = 'Saving…';
    fetch('/save-template', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name, files: files })
    }).then(function (r) { return r.json(); }).then(function (res) {
      btn.textContent = orig;
      if (res && res.ok) {
        state.templateName = name;
        refreshTemplateMenu();
        toast('Saved "' + name + '" to the SDK templates_base ✓');
      } else {
        toast('Save failed: ' + ((res && res.error) || 'unknown error'));
      }
    }).catch(function (err) {
      btn.textContent = orig;
      toast('Save failed — is the CardFly SDK server running? (' + (err.message || err) + ')');
    });
  }

  $('#btn-save-sdk').addEventListener('click', saveToSdk);

  $('#zoom-in').addEventListener('click', function () { zoomBy(1.2); });
  $('#zoom-out').addEventListener('click', function () { zoomBy(1 / 1.2); });
  $('#zoom-fit').addEventListener('click', zoomFit);

  $$('.tab').forEach(function (t) {
    t.addEventListener('click', function () {
      $$('.tab').forEach(function (o) { o.classList.toggle('active', o === t); });
      var name = t.getAttribute('data-tab');
      ['elements', 'variables', 'layers', 'templates', 'mixer'].forEach(function (n) {
        $('#tab-' + n).classList.toggle('active', n === name);
      });
      if (typeof window.cardfly === 'object' && window.cardfly._onTab) window.cardfly._onTab(name);
    });
  });

  document.addEventListener('keydown', function (e) {
    var tag = (e.target.tagName || '').toLowerCase();
    var typing = tag === 'input' || tag === 'textarea' || tag === 'select';
    if ((e.ctrlKey || e.metaKey) && !typing) {
      var k = e.key.toLowerCase();
      if (k === 'z') { e.preventDefault(); if (e.shiftKey) redo(); else undo(); return; }
      if (k === 'y') { e.preventDefault(); redo(); return; }
      if (k === '=' || k === '+') { e.preventDefault(); zoomBy(1.15); return; }
      if (k === '-') { e.preventDefault(); zoomBy(1 / 1.15); return; }
      if (k === '0') { e.preventDefault(); zoomFit(); return; }
    }
    if (typing) return;
    if (state.mode !== 'edit' || !state.doc) return;
    if (e.key === 'Delete' || e.key === 'Backspace') { if (state.selEid) { e.preventDefault(); deleteSel(); } }
    else if (/^Arrow/.test(e.key)) { e.preventDefault(); nudge(e.key); }
    else if (e.key === 'Escape') { deselect(); }
  });

  window.addEventListener('load', function () {
    loadSamples();
    state.fields = loadFields();
    collectVars();
    updateFieldsPanel();
    $('#zoom-val').textContent = '100%';
    updateHistBtns();
    updateStatus();
    var smp = new URLSearchParams(location.search).get('sample');
    if (smp) loadSample(smp + '.card.kaascan');
  });

  /* Hide "Save to SDK" unless the CardFly SDK server is actually answering —
     the static override or file:// setups can't write templates. */
  function detectSdkServer() {
    var btn = $('#btn-save-sdk');
    if (!btn) return;
    fetch('/template-list', { method: 'GET' }).then(function (r) {
      return r.json();
    }).then(function (j) {
      btn.classList.toggle('hidden', !(j && j.ok));
    }).catch(function () {
      btn.classList.add('hidden');
    });
  }
  detectSdkServer();

  window.cardfly = {
    state: state,
    select: select,
    deselect: deselect,
    addElement: addElement,
    addVarElement: addVarElement,
    togglePreview: togglePreview,
    undo: undo,
    redo: redo,
    deleteSel: deleteSel,
    duplicateSel: duplicateSel,
    render: render,
    zoomFit: zoomFit,
    exportKind: exportKind,
    samples: state.samples,
    filledDocString: function () { return filledDocString(); },
    backDesignSvg: function () { return backDesignSvg(); },
    backFilledString: function () { return backFilledString(); },
    loadTemplateFolder: loadTemplateFolder,
    bindVar: bindVarToElement,
    fields: state.fields,
    toast: toast,
    commitHistory: commitHistory,
    refreshUi: function () {
      collectVars();
      updateVarPanel();
      updateLayers();
      updateStatus();
      updateProps();
    }
  };
})();