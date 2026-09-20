/* CardFly Enterprise — template library, reusable components, template mixer.
   Loaded after app.js and talks to the CardFly SDK server via its JSON endpoints. */

(function () {
  'use strict';

  function cf() { return window.cardfly; }

  function $(sel) { return document.querySelector(sel); }
  function $all(sel) { return Array.prototype.slice.call(document.querySelectorAll(sel)); }

  var API = {
    list: '/template-list',
    saveTemplate: '/save-template',
    dup: '/duplicate-template',
    rename: '/rename-template',
    delTemplate: '/template/',        // + name
    listComponents: '/components',
    saveComponent: '/save-component',
    delComponent: '/components/',     // + name
    templateFile: '/templates/'        // + name + '/' + file
  };

  function toast(msg, ms) { (cf() && cf().toast) ? cf().toast(msg, ms) : window.alert(msg); }

  function post(url, body) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    }).then(function (r) { return r.json(); });
  }

  function del(url) {
    return fetch(url, { method: 'DELETE' }).then(function (r) { return r.json(); });
  }

  function getJSON(url) {
    return fetch(url).then(function (r) { return r.json(); });
  }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function uid() { return 'cfc' + Math.random().toString(36).slice(2, 9); }

  /* -------- helpers on the live document -------- */

  function layerContainer() {
    var svgD = cf().state.doc && cf().state.doc.documentElement;
    if (!svgD) return null;
    function find(el) {
      for (var i = 0; i < el.children.length; i++) {
        var c = el.children[i];
        if (c.tagName === 'g' &&
            (c.getAttribute('inkscape:groupmode') === 'layer' || /^layer/i.test(c.getAttribute('id') || ''))) {
          return c;
        }
        var r = find(c);
        if (r) return r;
      }
      return null;
    }
    return find(svgD) || svgD;
  }

  function parseSVGFragment(text) {
    // Wrap a bare fragment in a full <svg> with the expected namespaces.
    var NS = 'http://www.w3.org/2000/svg';
    var XL = 'http://www.w3.org/1999/xlink';
    var wrapped = '<svg xmlns="' + NS + '" xmlns:xlink="' + XL + '" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.0.dtd">'
      + text + '</svg>';
    var d = new DOMParser().parseFromString(wrapped, 'image/svg+xml');
    if (d.querySelector('parsererror')) return null;
    return d.documentElement;
  }

  function importFragmentIntoDoc(fragRoot, label) {
    var st = cf().state;
    if (!st.doc) { toast('Load a template on the canvas first.', 3000); return; }
    var layer = layerContainer();
    if (!layer || !layer.appendChild) return;
    var doc = st.doc;
    var kids = [];
    for (var i = 0; i < fragRoot.children.length; i++) kids.push(fragRoot.children[i]);
    var count = 0;
    // Also accept a single top-level element (component SVGs are one node).
    if (!kids.length && fragRoot.childNodes) {
      for (var j = 0; j < fragRoot.childNodes.length; j++) {
        if (fragRoot.childNodes[j].nodeType === 1) kids.push(fragRoot.childNodes[j]);
      }
    }
    kids.forEach(function (k) {
      layer.appendChild(doc.importNode(k, true));
      count++;
    });
    if (!count) { toast('Nothing to merge.', 2500); return; }
    st.selDoc = null;
    st.selEid = null;
    cf().commitHistory();
    cf().render();
    cf().refreshUi();
    toast('Merged ' + count + ' element' + (count === 1 ? '' : 's') + ' (' + (label || 'component') + ') onto the card.', 2800);
  }

  /* Remove a stray <defs>, styles and metadata from a serialized element so it
     imports cleanly and keeps {placeholders}. */
  function sanitizeElementXml(xml) {
    var frag = parseSVGFragment(xml);
    if (!frag) return xml;
    ['defs', 'title', 'desc', 'metadata'].forEach(function (tag) {
      var els = frag.querySelectorAll(tag);
      for (var i = els.length - 1; i >= 0; i--) els[i].remove();
    });
    return new XMLSerializer().serializeToString(frag);
  }

  /* Serialize a single element node (and its subtree) to XML. */
  function elementToXml(node) {
    if (!node) return null;
    var ser = new XMLSerializer();
    return ser.serializeToString(node);
  }

  /* Tolerant title/name -> safe filename */
  function safeSlug(s) {
    return (s || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'component';
  }

  /* ================================================================
     COMPONENTS PALETTE
     ================================================================ */

  function refreshComponents() {
    var list = $('#components-list');
    if (!list) return;
    getJSON(API.listComponents).then(function (res) {
      var comps = (res && res.components) || [];
      if (!comps.length) {
        list.innerHTML = '<div class="vempty">No saved components yet.<br>Select a layer and press “+ Save selected”.</div>';
        return;
      }
      list.innerHTML = '';
      comps.forEach(function (c) {
        var item = document.createElement('div');
        item.className = 'comp-item';
        item.draggable = true;
        item.title = 'Add "' + c.title + '"\nDrag onto the card for a precise spot.';
        item.innerHTML =
          '<span class="comp-thumb"><span class="empty-glyph">' + esc(c.title.charAt(0).toUpperCase()) + '</span></span>' +
          '<span class="comp-info">' +
            '<span class="comp-title">' + esc(c.title) + '</span><br>' +
            '<span class="comp-cat">' + esc(c.category || 'general') + '</span>' +
          '</span>' +
          '<button class="comp-del" title="Delete this component">✕</button>';

        item.addEventListener('dragstart', function (ev) {
          ev.dataTransfer.setData('text/plain', 'component:' + c.name);
        });
        item.addEventListener('click', function (ev) {
          if (ev.target.closest('.comp-del')) return;
          addComponent(c.name);
        });
        item.querySelector('.comp-del').addEventListener('click', function (ev) {
          ev.stopPropagation();
          if (!window.confirm('Delete component "' + c.title + '"?')) return;
          del(API.delComponent + encodeURIComponent(c.name)).then(function (r) {
            (r && r.ok) ? toast('Component deleted.') : toast('Delete failed.');
            refreshComponents();
          });
        });

        // Preview the actual svg artwork as a thumbnail via a data URI so the
        // browser computes its intrinsic size and object-fit can fit it.
        fetch(API.delComponent + encodeURIComponent(c.name)).then(function (r) { return r.text(); }).then(function (txt) {
          var thumb = item.querySelector('.comp-thumb');
          thumb.innerHTML = '';
          var img = document.createElement('img');
          img.alt = '';
          img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(txt);
          img.onerror = function () { thumb.innerHTML = '<span class="empty-glyph">' + esc(c.title.charAt(0).toUpperCase()) + '</span>'; };
          thumb.appendChild(img);
        }).catch(function () { /* keep glyph */ });

        list.appendChild(item);
      });
    }).catch(function () {
      list.innerHTML = '<div class="vempty">Could not reach the SDK server for components.</div>';
    });
  }

  function addComponent(name) {
    if (!cf().state.doc) { toast('Load a template first.', 2800); return; }
    fetch(API.delComponent + encodeURIComponent(name)).then(function (r) { return r.text(); }).then(function (txt) {
      var frag = parseSVGFragment(txt);
      if (!frag) { toast('Could not parse component "' + name + '".', 3000); return; }
      importFragmentIntoDoc(frag, name);
    }).catch(function (err) {
      toast('Could not load component: ' + (err && err.message), 3200);
    });
  }

  function saveSelectedComponent() {
    var st = cf().state;
    if (!st.doc) { toast('Load a template first.', 2500); return; }
    if (!st.selDoc) { toast('Select a layer on the canvas first, then save it as a component.', 3800); return; }
    var el = st.selDoc;
    var nameGuess = el.getAttribute('id') || el.tagName || 'component';
    var title = (window.prompt('Component name (e.g. “Stamp”, “Header”, “Signature”)', nameGuess) || '').trim();
    if (!title) { toast('Component save cancelled.'); return; }
    var xml = sanitizeElementXml(elementToXml(el));
    var fname = safeSlug(title) + '.svg';
    post(API.saveComponent, {
      name: fname,
      svg: xml,
      meta: { title: title, category: 'general', kind: el.tagName }
    }).then(function (r) {
      if (r && r.ok) { toast('Saved component "' + title + '".'); refreshComponents(); }
      else toast('Save failed: ' + ((r && r.error) || 'unknown'));
    }).catch(function (err) { toast('Save failed: ' + (err && err.message)); });
  }

  /* ================================================================
     TEMPLATE LIBRARY
     ================================================================ */

  function loadTemplate(name, front, back) {
    var files = [];
    function buildFile(url, fname) {
      return fetch(url).then(function (r) { return r.text(); })
        .then(function (txt) { files.push(new File([txt], fname, { type: 'image/svg+xml' })); });
    }
    var tasks = [];
    if (front) tasks.push(buildFile(API.templateFile + encodeURIComponent(name) + '/' + encodeURIComponent(front), front));
    if (back) tasks.push(buildFile(API.templateFile + encodeURIComponent(name) + '/' + encodeURIComponent(back), back));
    Promise.all(tasks).then(function () {
      cf().loadTemplateFolder(files, name);
      toast('Loaded "' + name + '".', 2200);
      refreshTemplates();
    }).catch(function (err) {
      toast('Could not load "' + name + '": ' + (err && err.message), 3200);
    });
  }

  function deleteTemplate(name) {
    if (!window.confirm('Delete template "' + name + '" from the SDK library?')) return;
    del(API.delTemplate + encodeURIComponent(name)).then(function (r) {
      (r && r.ok) ? toast('Deleted "' + name + '".') : toast('Delete failed.');
      refreshTemplates();
      refreshMixerSource();
    });
  }

  function duplicateTemplate(name) {
    var proposed = name + ' copy';
    var n = (window.prompt('New template name', proposed) || '').trim();
    if (!n) return;
    post(API.dup, { from: name, name: n }).then(function (r) {
      if (r && r.ok) { toast('Duplicated "' + name + '" → "' + n + '".'); refreshTemplates(); refreshMixerSource(); }
      else toast('Duplicate failed: ' + ((r && r.error) || 'unknown'));
    }).catch(function (err) { toast('Duplicate failed: ' + (err && err.message)); });
  }

  function currentTemplateName() {
    return (cf().state.templateName || cf().state.fileName || '').replace(/\.(svg|kaascan)$/i, '').trim();
  }

  function renderLibrary(res) {
    var box = $('#template-library');
    if (!box) return;
    var templates = (res && res.templates) || [];
    if (!templates.length) {
      box.innerHTML = '<div class="vempty">No templates in the SDK library yet.<br>Use “Save to SDK” to add one.</div>';
      return;
    }
    var cur = currentTemplateName();
    box.innerHTML = '';
    templates.forEach(function (t) {
      var item = document.createElement('div');
      item.className = 'lib-item' + (cur && t.name === cur ? ' current' : '');
      item.innerHTML =
        '<div class="lib-name">' + esc(t.name) + '</div>' +
        '<div class="lib-sub">front: ' + esc(t.front || '—') + ' · back: ' + esc(t.back || '—') + '</div>' +
        '<div class="lib-actions">' +
          '<button class="mini-btn" data-act="load">Load</button>' +
          '<button class="mini-btn" data-act="dup">Duplicate</button>' +
          '<button class="mini-btn danger" data-act="del">Delete</button>' +
        '</div>';
      item.querySelector('[data-act="load"]').addEventListener('click', function () {
        loadTemplate(t.name, t.front, t.back);
      });
      item.querySelector('[data-act="dup"]').addEventListener('click', function () {
        duplicateTemplate(t.name);
      });
      item.querySelector('[data-act="del"]').addEventListener('click', function () {
        deleteTemplate(t.name);
      });
      box.appendChild(item);
    });
  }

  function refreshTemplates() {
    var box = $('#template-library');
    if (!box) return;
    getJSON(API.list).then(function (res) {
      renderLibrary(res);
      if ($('#mixer-source')) populateMixerSource(res);
    }).catch(function () {
      box.innerHTML = '<div class="vempty">Could not reach the SDK server for the template library.</div>';
    });
  }

  /* ================================================================
     TEMPLATE MIXER
     ================================================================ */

  function populateMixerSource(res) {
    var sel = $('#mixer-source');
    if (!sel) return;
    var templates = (res && res.templates) || [];
    var current = sel.value;
    sel.innerHTML = '';
    if (!templates.length) {
      sel.innerHTML = '<option value="" disabled>No templates</option>';
      return;
    }
    templates.forEach(function (t) {
      var o = document.createElement('option');
      o.value = t.name;
      o.textContent = t.name;
      sel.appendChild(o);
    });
    if (current && templates.some(function (t) { return t.name === current; })) sel.value = current;
  }

  function refreshMixerSource() {
    getJSON(API.list).then(function (res) { populateMixerSource(res); }).catch(function () {});
  }

  var mixerData = null; // { name: <src>, front: <parsed>, back: <parsed> }

  function inspectMixerSource() {
    var sel = $('#mixer-source');
    var box = $('#mixer-content');
    var name = sel && sel.value;
    if (!name) { box.innerHTML = '<div class="vempty">Pick a source template first.</div>'; return; }
    if (!cf().state.doc) { box.innerHTML = '<div class="vempty">Load a template on the canvas first — the mixer merges into the current design.</div>'; return; }

    // find the front/back filenames for this template
    getJSON(API.list).then(function (res) {
      var t = ((res && res.templates) || []).filter(function (x) { return x.name === name; })[0];
      if (!t) throw new Error('template not found');
      var files = [];
      var tasks = [];
      function addFile(fname, side) {
        if (!fname) return;
        tasks.push(fetch(API.templateFile + encodeURIComponent(name) + '/' + encodeURIComponent(fname))
          .then(function (r) { return r.text(); })
          .then(function (txt) { files.push({ side: side, text: txt, fname: fname }); }));
      }
      addFile(t.front, 'front');
      addFile(t.back, 'back');
      return Promise.all(tasks).then(function () { return files; });
    }).then(function (files) {
      mixerData = { name: name, front: null, back: null };
      files.forEach(function (f) {
        var d = new DOMParser().parseFromString(f.text, 'image/svg+xml');
        if (d.querySelector('parsererror')) return;
        mixerData[f.side] = { doc: d, fname: f.fname };
      });
      renderMixerLayers(box);
    }).catch(function (err) {
      box.innerHTML = '<div class="vempty">Could not inspect "' + esc(name) + '": ' + esc(err && err.message) + '</div>';
    });
  }

  /* List top-level editable nodes (mirrors the editor's walk: skip <defs>,
     descend into layers, treat <g> groups and editable tags as pickable). */
  function collectLayers(doc) {
    var out = [];
    var EDITABLE = { text: 1, rect: 1, circle: 1, ellipse: 1, line: 1, path: 1, image: 1, polyline: 1, polygon: 1 };
    var root = doc.documentElement;

    function isLayer(g) {
      return g.getAttribute('inkscape:groupmode') === 'layer' || /^layer/i.test(g.getAttribute('id') || '');
    }
    function walk(el) {
      for (var i = 0; i < el.children.length; i++) {
        var c = el.children[i];
        if (c.tagName === 'defs' || c.tagName === 'title' || c.tagName === 'desc' || c.tagName === 'metadata') continue;
        var tag = (c.tagName || '').toLowerCase();
        if (tag === 'g') {
          if (isLayer(c)) { walk(c); continue; }
          if (!c.children.length) continue;
          // treat a plain group as one importable unit
          out.push({ node: c, name: c.getAttribute('id') || 'Group', kind: 'group' });
          continue;
        }
        if (EDITABLE[tag]) {
          out.push({ node: c, name: nodeLabel(c), kind: tag });
          walk(c);
        }
      }
    }
    walk(root);
    return out;
  }

  function nodeLabel(node) {
    var tag = (node.tagName || '').toLowerCase();
    if (tag === 'text') {
      var ts = node.querySelector('tspan');
      var txt = (ts && ts.textContent) || node.textContent || '';
      var m = /\{([A-Za-z_][\w]*)\}/.exec(txt);
      if (m) return '{' + m[1] + '}';
      return txt.replace(/\s+/g, ' ').slice(0, 28) || 'Text';
    }
    if (tag === 'image') {
      var href = '';
      for (var i = 0; i < node.attributes.length; i++) {
        var a = node.attributes[i];
        if (/href/i.test(a.name)) href = a.value;
      }
      var vm = /\{([A-Za-z_][\w]*)\}/.exec(href);
      if (vm) return '#{' + vm[1] + '}';
      return node.getAttribute('id') || 'Image';
    }
    return node.getAttribute('id') || tag;
  }

  function renderMixerLayers(box) {
    box.innerHTML = '';
    if (!mixerData) { box.innerHTML = '<div class="vempty">Nothing to show.</div>'; return; }

    var title = document.createElement('div');
    title.className = 'mixer-side-title';
    title.textContent = 'Source: ' + mixerData.name;
    box.appendChild(title);

    var layers = [];
    function addSide(side, label) {
      var data = mixerData[side];
      if (!data || !data.doc) return;
      var items = collectLayers(data.doc);
      items.forEach(function (it) { it.side = side; it.sideLabel = label; });
      layers = layers.concat(items);
    }
    addSide('front', 'Front');
    addSide('back', 'Back');

    if (!layers.length) {
      box.innerHTML = '<div class="vempty">No editable layers found in this template.</div>';
      return;
    }

    var frag = document.createDocumentFragment();
    function addSideBlock(label, listItems) {
      var block = document.createElement('div');
      block.className = 'mixer-side';
      var t = document.createElement('div');
      t.className = 'mixer-side-title';
      t.textContent = label;
      block.appendChild(t);
      listItems.forEach(function (it) {
        var row = document.createElement('label');
        row.className = 'mixer-layer';
        var cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.value = it.side;
        cb.dataset.side = it.side;
        row.appendChild(cb);
        var nm = document.createElement('span');
        nm.className = 'ml-name';
        nm.textContent = it.name;
        row.appendChild(nm);
        var kd = document.createElement('span');
        kd.className = 'ml-kind';
        kd.textContent = it.kind;
        row.appendChild(kd);
        row._item = it;
        block.appendChild(row);
      });
      frag.appendChild(block);
    }

    var frontItems = layers.filter(function (l) { return l.side === 'front'; });
    var backItems = layers.filter(function (l) { return l.side === 'back'; });
    if (frontItems.length) addSideBlock('Front', frontItems);
    if (backItems.length) addSideBlock('Back', backItems);

    var tools = document.createElement('div');
    tools.className = 'mixer-tools';
    var checkAll = document.createElement('button');
    checkAll.className = 'mixer-checkall';
    checkAll.textContent = 'Select all';
    tools.appendChild(checkAll);
    var merge = document.createElement('button');
    merge.className = 'btn btn-yellow';
    merge.textContent = 'Merge into current';
    merge.style.flex = '1';
    tools.appendChild(merge);
    frag.appendChild(tools);

    box.appendChild(frag);

    checkAll.addEventListener('click', function () {
      var boxes = $all('#mixer-content .mixer-layer input[type="checkbox"]');
      var anyOff = boxes.some(function (b) { return !b.checked; });
      boxes.forEach(function (b) { b.checked = anyOff; });
    });

    merge.addEventListener('click', function () {
      var sel = [];
      $all('#mixer-content .mixer-layer').forEach(function (row) {
        var cb = row.querySelector('input[type="checkbox"]');
        if (cb && cb.checked && row._item) sel.push(row._item);
      });
      if (!sel.length) { toast('Tick at least one layer to merge.', 2500); return; }
      if (!cf().state.doc) { toast('Load a template on the canvas first.', 2800); return; }

      var st = cf().state;
      var layer = layerContainer();
      sel.forEach(function (it) {
        var clone = it.node.cloneNode(true);
        layer.appendChild(st.doc.importNode(clone, true));
      });
      st.selDoc = null;
      st.selEid = null;
      cf().commitHistory();
      cf().render();
      cf().refreshUi();
      toast('Merged ' + sel.length + ' layer(s) from "' + mixerData.name + '" onto the card.', 3000);
    });
  }

  /* Keep mixers' variable badges live if the user re-opens the tab. */
  function onTab(name) {
    if (name === 'templates') refreshTemplates();
    if (name === 'mixer') { refreshMixerSource(); }
    if (name === 'elements') refreshComponents();
  }

  /* ================================================================
     LIVE DATA · PREVIEW BY SCHOOL + STUDENT
     ================================================================ */
  var live = {
    students: [],
    active: null,          // { student_id, student_name, ... }
    photoUrl: null,        // current proxied photo URL
    qrDataUri: null,
    loading: false
  };

  function setLiveStatus(msg, tone) {
    var st = $('#live-status');
    if (!st) return;
    st.textContent = msg || '';
    st.style.color = tone === 'err' ? 'var(--red, #c0392b)' : (tone === 'ok' ? 'var(--green, #2e7d32)' : '');
  }

  function refreshSchools() {
    var sel = $('#live-school');
    if (!sel) return;
    sel.disabled = true;
    var refreshBtn = $('#btn-live-refresh');
    if (refreshBtn) refreshBtn.disabled = true;
    setLiveStatus('Loading schools…');
    getJSON('/admin/schools').then(function (res) {
      var schools = (res && res.schools) || [];
      sel.innerHTML = '';
      if (!schools.length) {
        sel.innerHTML = '<option value="" disabled>No schools found</option>';
        setLiveStatus('The Kaascan admin feed returned no schools.', 'err');
      } else {
        schools.forEach(function (s) {
          var o = document.createElement('option');
          o.value = s.name;
          o.textContent = s.name;
          sel.appendChild(o);
        });
        sel.value = schools[0].name;
        loadStudents(sel.value);
        setLiveStatus('');
      }
    }).catch(function () {
      sel.innerHTML = '<option value="" disabled>Could not reach the SDK server</option>';
      setLiveStatus('Could not load schools. Is the SDK server running?', 'err');
    }).then(function () {
      sel.disabled = false;
      if (refreshBtn) refreshBtn.disabled = false;
    });
  }

  function loadStudents(school) {
    var sel = $('#live-student');
    if (!sel) return;
    sel.disabled = true;
    sel.innerHTML = '<option value="" disabled>Loading…</option>';
    var loadBtn = $('#btn-live-load');
    if (loadBtn) loadBtn.disabled = true;
    setLiveStatus('Loading students for ' + school + '…');
    getJSON('/admin/students?school=' + encodeURIComponent(school)).then(function (res) {
      var students = (res && res.students) || [];
      live.students = students;
      sel.innerHTML = '';
      if (!students.length) {
        sel.innerHTML = '<option value="" disabled>No students found</option>';
        setLiveStatus('No students for ' + school + '.', 'err');
      } else {
        students.forEach(function (s, i) {
          var o = document.createElement('option');
          o.value = i;
          o.textContent = s.student_name + '  ·  ' + (s.student_class || '—');
          sel.appendChild(o);
        });
        sel.value = '0';
        setLiveStatus(students.length + ' student' + (students.length === 1 ? '' : 's') + ' found.');
      }
    }).catch(function () {
      sel.innerHTML = '<option value="" disabled>Could not load students</option>';
      setLiveStatus('Could not load students.' + (school ? ' Did the school name change?' : ''), 'err');
    }).then(function () {
      sel.disabled = false;
      if (loadBtn) loadBtn.disabled = !live.students.length;
    });
  }

  function onStudentChange() {
    var sel = $('#live-student');
    var loadBtn = $('#btn-live-load');
    if (loadBtn) loadBtn.disabled = !(sel && sel.value !== '' && live.students.length);
  }

  /* Map a raw student record onto every known variable name, plus the photo
     and QR (fetched proxied server-side) so the card can render real data. */
  function fillLiveSamples(student) {
    var st = cf().state;
    if (!st) return;
    var firstName = (student.student_name || '').split(/\s+/)[0] || '';
    var lastName = (student.student_name || '').split(/\s+/).slice(1).join(' ') || '';
    var cls = student.student_class || '';

    var map = {
      name: student.student_name,
      student_name: student.student_name,
      full_name: student.student_name,
      first_name: firstName,
      last_name: lastName,
      class: cls,
      grade: cls,
      level: cls,
      section: '',
      veec: cls,
      student_class: cls,
      student_id: student.student_id,
      id: student.student_id,
      card_no: student.student_id,
      reg_no: student.student_id,
      reference: student.student_id,
      school_name: student.school_name,
      school: student.school_name
    };
    Object.keys(map).forEach(function (k) { if (map[k] !== undefined) st.samples[k] = map[k]; });

    // Photo: apply to any image-ish variable, proxied to a same-origin URL so
    // the browser can show it without a CORS issue on the Kaascan CDN.
    if (live.photoUrl) {
      ['image_base64', 'photo', 'student_photo', 'picture', 'avatar', 'photo_url'].forEach(function (k) {
        st.samples[k] = live.photoUrl;
      });
    }
    // QR: real QR PNG of the student id.
    if (live.qrDataUri) {
      ['data_qrcode', 'qr', 'qrcode'].forEach(function (k) { st.samples[k] = live.qrDataUri; });
    }
  }

  function loadLiveStudent() {
    if (live.loading) return;
    if (!cf().state.doc) {
      setLiveStatus('Load a template on the canvas first — the live data fills its {placeholders}.', 'err');
      return;
    }
    var sel = $('#live-student');
    var idx = sel ? sel.value : '';
    if (idx === '' || !live.students.length) {
      setLiveStatus('Pick a student first.', 'err');
      return;
    }
    var student = live.students[parseInt(idx, 10)];
    if (!student) { setLiveStatus('Student not found.', 'err'); return; }
    live.active = student;
    setLiveStatus('Player ' + student.student_name + '…');
    live.loading = true;

    // Photo + QR in parallel (proxied server-side).
    var photoP = student.student_photo_url
      ? getBlobishURL('/admin/photo?url=' + encodeURIComponent(student.student_photo_url))
      : Promise.resolve(null);
    var qrP = getJSON('/admin/qr?text=' + encodeURIComponent(student.student_id))
      .then(function (r) { return (r && r.ok) ? r.qr : null; })
      .catch(function () { return null; });

    Promise.all([photoP, qrP]).then(function (res) {
      live.photoUrl = res[0] || null;
      live.qrDataUri = res[1] || null;
      fillLiveSamples(student);
      var st = cf().state;
      if (st && st.mode !== 'preview') {
        st.mode = 'preview';
        var pb = $('#btn-preview');
        if (pb) pb.classList.add('active');
        var c = document.getElementById('center');
        if (c) c.classList.add('previewing');
      }
      cf().refreshUi();
      cf().render();
      toast('Loaded ' + (student.student_name || 'student') + ' into the preview.', 2600);
      setLiveStatus('Preview filled with ' + (student.student_name || 'student') + ' (' + (student.student_class || '—') + ').', 'ok');
    }).catch(function () {
      setLiveStatus('Could not finish loading the live data.', 'err');
    }).then(function () {
      live.loading = false;
    });
  }

  // Like getJSON but resolves to "ok" for raw byte routes rather than parsing.
  function getBlobishURL(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error('photo request failed');
      return url; // same-origin proxied URL is usable directly as <image href>
    });
  }

  function initLiveData() {
    var schoolSel = $('#live-school');
    var studentSel = $('#live-student');
    var loadBtn = $('#btn-live-load');
    var refreshBtn = $('#btn-live-refresh');
    if (!schoolSel || !studentSel || !loadBtn) return;
    if (refreshBtn) refreshBtn.addEventListener('click', refreshSchools);
    schoolSel.addEventListener('change', function () { loadStudents(schoolSel.value); });
    studentSel.addEventListener('change', onStudentChange);
    loadBtn.addEventListener('click', loadLiveStudent);
    refreshSchools();
  }

  /* -------- drag & drop from palettes onto the canvas -------- */
  function installDropping() {
    var center = document.getElementById('center');
    if (!center) return;
    center.addEventListener('dragover', function (ev) {
      var kind = ev.dataTransfer && ev.dataTransfer.getData('text/plain');
      if (kind && kind.indexOf('component:') === 0) ev.preventDefault();
    });
    center.addEventListener('drop', function (ev) {
      var kind = ev.dataTransfer && ev.dataTransfer.getData('text/plain');
      if (!kind || kind.indexOf('component:') !== 0) return;
      ev.preventDefault();
      var name = kind.slice('component:'.length);
      addComponent(name);
    });
  }

  /* -------- theme toggle (light = default, dark = optional) -------- */
  var THEME_KEY = 'cardfly-theme';
  function currentTheme() {
    return document.documentElement.getAttribute('data-theme') || 'light';
  }
  function applyTheme(theme) {
    var root = document.documentElement;
    if (theme === 'dark') root.setAttribute('data-theme', 'dark');
    else root.removeAttribute('data-theme');
    var btn = $('#btn-theme');
    if (btn) {
      btn.textContent = theme === 'dark' ? '☀ Light' : '☾ Dark';
      btn.title = theme === 'dark' ? 'Switch to the light theme' : 'Switch to the dark theme';
    }
    try { localStorage.setItem(THEME_KEY, theme); } catch (e) {}
  }
  function initTheme() {
    var saved = null;
    try { saved = localStorage.getItem(THEME_KEY); } catch (e) {}
    var theme = saved === 'dark' ? 'dark' : 'light'; // default = light
    applyTheme(theme);
    var btn = $('#btn-theme');
    if (btn) btn.addEventListener('click', function () {
      applyTheme(currentTheme() === 'dark' ? 'light' : 'dark');
    });
  }

  function init() {
    if (window.cardfly) window.cardfly._onTab = onTab;
    initTheme();
    // Reuse app.js's toast/layer drag pipeline by remaining a passive observer.
    var btnSave = $('#btn-save-component');
    if (btnSave) btnSave.addEventListener('click', saveSelectedComponent);

    var btnRefresh = $('#btn-refresh-templates');
    if (btnRefresh) btnRefresh.addEventListener('click', refreshTemplates);

    var mixerLoad = $('#btn-mixer-load');
    if (mixerLoad) mixerLoad.addEventListener('click', inspectMixerSource);

    refreshTemplates();
    refreshComponents();
    refreshMixerSource();
    installDropping();
    initLiveData();
  }

  if (window.cardfly) {
    // app.js has already finished; start immediately.
    init();
  } else {
    // Fallback: wait for it.
    var i = setInterval(function () {
      if (window.cardfly) { clearInterval(i); init(); }
    }, 200);
    setTimeout(function () { if (!window.cardfly) window.__cardflyEnterpriseMissing = true; }, 10000);
  }
})();
