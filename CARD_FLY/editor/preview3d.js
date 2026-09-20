import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';

var wrap = document.getElementById('three-wrap');
var stage = document.getElementById('three-stage');
var nameEl = document.getElementById('three-name');
var btnOpen = document.getElementById('btn-3d');
var btnClose = document.getElementById('btn-3d-close');

var renderer = null;
var scene = null;
var camera = null;
var controls = null;
var card = null;
var rafId = null;
var running = false;
var opened = false;
var lastKey = null;
var frame = null;
var resizeObs = null;
var refreshTimer = null;
var refreshing = null;
var cardCenterY = 0;
var framedInit = false;
var userMoved = false;

var CARD_T = 1.6;
var TEX = 4096;

var canvasCache = Object.create(null);
var pendingCanvases = Object.create(null);
var CACHE_MAX = 10;

/* ------------------------------------------------------------
   Background presets + persistence
   ------------------------------------------------------------ */
var BG = {
  obsidian: { type: 'color', value: 0x0a0e1a, label: 'Obsidian' },
  graphite: { type: 'color', value: 0x1e2433, label: 'Graphite' },
  navy:     { type: 'color', value: 0x0f2038, label: 'Navy' },
  slate:    { type: 'color', value: 0x55637d, label: 'Slate' },
  cloud:    { type: 'color', value: 0xe8edf6, label: 'Cloud' },
  coral:    { type: 'color', value: 0xffb49a, label: 'Coral' },
  mint:     { type: 'color', value: 0xb9f0d6, label: 'Mint' },
  sky:      { type: 'color', value: 0xc9e2ff, label: 'Sky' },
  gradient: { type: 'gradient', top: '#0b1020', bottom: '#27355a', label: 'Gradient' }
};
var BG_STORE = 'cardfly3d.bg';
var bgKey = 'obsidian';
var customColor = '#0a0e1a';
var bgInput = document.getElementById('bg-custom');

function storeBg() {
  try {
    localStorage.setItem(BG_STORE, bgKey === 'custom' ? 'custom:' + customColor : bgKey);
  } catch (e) { }
}

function hexOf(color) {
  if (typeof color === 'number') return '#' + color.toString(16).padStart(6, '0');
  return color;
}

function setBackground(key) {
  if (!scene) return;
  bgKey = key;
  var spec = (key === 'custom')
    ? { type: 'color', value: parseInt(customColor.slice(1), 16) }
    : BG[key];
  if (!spec) spec = BG.obsidian;

  if (scene._bgTex) { scene._bgTex.dispose(); scene._bgTex = null; }

  if (spec.type === 'gradient') {
    var cv = document.createElement('canvas');
    cv.width = 4; cv.height = 256;
    var g = cv.getContext('2d');
    g.fillStyle = '#000';
    g.fillRect(0, 0, 4, 256);
    var gr = g.createLinearGradient(0, 0, 0, 256);
    gr.addColorStop(0, spec.top);
    gr.addColorStop(1, spec.bottom);
    g.fillStyle = gr;
    g.fillRect(0, 0, 4, 256);
    var tex = new THREE.CanvasTexture(cv);
    tex.colorSpace = THREE.SRGBColorSpace;
    scene._bgTex = tex;
    scene.background = tex;
  } else {
    scene.background = new THREE.Color(spec.value);
  }

  /* keep the native color picker in sync with the current solid colour */
  if (bgInput) {
    if (spec.type === 'gradient') bgInput.value = hexOf(spec.bottom);
    else bgInput.value = hexOf(spec.value);
  }

  /* highlight the active swatch */
  var sw = wrap.querySelectorAll('.bg-swatch');
  for (var i = 0; i < sw.length; i++) {
    sw[i].classList.toggle('active', sw[i].getAttribute('data-bg') === bgKey);
  }
  storeBg();
}

function restoreBg() {
  var saved = null;
  try { saved = localStorage.getItem(BG_STORE); } catch (e) { }
  if (saved && saved.indexOf('custom:') === 0) {
    customColor = saved.slice(7);
    bgKey = 'custom';
  } else if (saved && BG[saved]) {
    bgKey = saved;
  }
  var spec = (bgKey === 'custom')
    ? { type: 'color', value: parseInt(customColor.slice(1), 16) }
    : BG[bgKey];
  if (bgInput) bgInput.value = spec.type === 'gradient' ? hexOf(spec.bottom) : hexOf(spec.value);
}

function getC() {
  return window.cardfly || null;
}

function cardDims() {
  var c = getC();
  var b = (c && c.state && c.state.vb) ? c.state.vb : { w: 85.6, h: 54 };
  return { w: b.w, h: b.h };
}

function hashStr(s) {
  var h1 = 0x811c9dc5, h2 = 0x01000193, i, c;
  for (i = 0; i < s.length; i++) {
    c = s.charCodeAt(i);
    h1 = Math.imul(h1 ^ c, 16777619);
    if ((i & 1) === 0) h2 = Math.imul(h2 ^ c, 16777619);
  }
  return (h1 >>> 0).toString(16) + '-' + (h2 >>> 0).toString(16);
}

function normalizeAndRasterize(svgText, maxPx) {
  return new Promise(function (resolve, reject) {
    var doc;
    try {
      doc = new DOMParser().parseFromString(svgText, 'image/svg+xml');
    } catch (e) { return reject(e); }
    if (!doc || doc.querySelector('parsererror') || !doc.documentElement) {
      return reject(new Error('svg parse failed'));
    }
    var root = doc.documentElement;
    var vb = root.getAttribute('viewBox');
    var vw = 0, vh = 0;
    if (vb) {
      var p = vb.trim().split(/[\s,]+/).map(Number);
      if (p.length >= 4) { vw = p[2]; vh = p[3]; }
    }
    if (!(vw > 0 && vh > 0)) {
      vw = parseFloat(root.getAttribute('width')) || 85.6;
      vh = parseFloat(root.getAttribute('height')) || 54;
    }
    var scale = (maxPx || TEX) / Math.max(vw, vh);
    var pw = Math.max(2, Math.round(vw * scale));
    var ph = Math.max(2, Math.round(vh * scale));
    root.setAttribute('width', pw);
    root.setAttribute('height', ph);
    var out;
    try {
      out = new XMLSerializer().serializeToString(doc);
    } catch (e) { return reject(e); }

    var blob = new Blob([out], { type: 'image/svg+xml' });
    var url = URL.createObjectURL(blob);
    var img = new Image();
    img.onload = function () {
      try {
        var canvas = document.createElement('canvas');
        canvas.width = pw;
        canvas.height = ph;
        var ctx = canvas.getContext('2d');
        ctx.drawImage(img, 0, 0, pw, ph);
        URL.revokeObjectURL(url);
        resolve(canvas);
      } catch (e) {
        URL.revokeObjectURL(url);
        reject(e);
      }
    };
    img.onerror = function () {
      URL.revokeObjectURL(url);
      reject(new Error('svg raster failed'));
    };
    img.src = url;
  });
}

async function rasterRetry(svgText, tries) {
  tries = tries || 2;
  var err = null;
  for (var i = 0; i < tries; i++) {
    try {
      if (i > 0) await new Promise(function (r) { setTimeout(r, 90); });
      return await normalizeAndRasterize(svgText, TEX);
    } catch (e) { err = e; }
  }
  throw err;
}

function getCanvas(key, svgText) {
  if (canvasCache[key]) return Promise.resolve(canvasCache[key]);
  if (pendingCanvases[key]) return pendingCanvases[key];
  pendingCanvases[key] = rasterRetry(svgText).then(function (canvas) {
    canvasCache[key] = canvas;
    delete pendingCanvases[key];
    var keys = Object.keys(canvasCache);
    while (keys.length > CACHE_MAX) {
      var gone = canvasCache[keys[0]];
      delete canvasCache[keys[0]];
      keys.shift();
    }
    return canvas;
  }, function (err) {
    delete pendingCanvases[key];
    throw err;
  });
  return pendingCanvases[key];
}

function texFromCanvas(canvas, label) {
  var tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = Math.min(16, renderer ? renderer.capabilities.getMaxAnisotropy() : 8);
  /* proper mipmap chain so distant / angled faces stay crisp */
  tex.minFilter = THREE.LinearMipmapLinearFilter;
  tex.magFilter = THREE.LinearFilter;
  tex.generateMipmaps = true;
  tex.name = label;
  return tex;
}

function buildCardGroup() {
  var dims = cardDims();
  var W = dims.w, H = dims.h, T = CARD_T;

  /* real-world PVC card: the body (faces + thickness edge) is white plastic,
     not dark metal */
  var edgeMat = new THREE.MeshStandardMaterial({
    color: 0xf4f4f5, roughness: 0.42, metalness: 0.02
  });
  var frontMat = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.30, metalness: 0.02 });
  var backMat = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.30, metalness: 0.02 });

  /* box material order: [+x, -x, +y, -y, +z, -z] => 4=front(+z), 5=back(-z) */
  var RADIUS = 3; /* realistic physical card corner radius (ID-1) */
  var geo = new RoundedBoxGeometry(W, H, T, 3, RADIUS);
  var mesh = new THREE.Mesh(geo, [edgeMat, edgeMat, edgeMat, edgeMat, frontMat, backMat]);
  mesh.position.y = 0;
  mesh.castShadow = true;
  mesh.receiveShadow = true;

  mesh.mats = { front: frontMat, back: backMat };
  /* true world centre of the card, used for perfect centroid framing */
  mesh.worldCenter = mesh.position.clone();
  mesh.center = { x: mesh.position.x, y: mesh.position.y, z: mesh.position.z };
  return mesh;
}

function faceMat(color) {
  return new THREE.MeshStandardMaterial({ color: color, roughness: 0.4, metalness: 0.02 });
}

function makeEnvironment() {
  var pmrem = new THREE.PMREMGenerator(renderer);
  var envScene = new THREE.Scene();
  envScene.background = new THREE.Color(0x0a0e1a);

  function panel(color, pos, size, lookAtOrigin) {
    var m = new THREE.MeshBasicMaterial({ color: color });
    var p = new THREE.Mesh(new THREE.PlaneGeometry(size, size), m);
    p.position.copy(pos);
    if (lookAtOrigin !== false) p.lookAt(0, 0, 0);
    envScene.add(p);
  }
  panel(0xffffff, new THREE.Vector3(0, 0, 0), 220, true);       /* big soft frontal fill */
  panel(0xdfe8ff, new THREE.Vector3(160, 120, -140), 130);      /* very soft cool key from top-right */
  panel(0xffe9dd, new THREE.Vector3(-170, 90, -120), 120);      /* very soft warm accent from top-left */
  panel(0xf2f3f5, new THREE.Vector3(0, -150, 40), 200);         /* neutral floor bounce */
  panel(0xcfd3da, new THREE.Vector3(0, 0, -220), 260);          /* light neutral backdrop */

  var tex = pmrem.fromScene(envScene, 0.03).texture;
  scene.environment = tex;
  return tex;
}

function buildScene() {
  renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  stage.appendChild(renderer.domElement);

  scene = new THREE.Scene();
  scene.fog = new THREE.Fog(0x0a0e1a, 900, 1600);

  camera = new THREE.PerspectiveCamera(40, 1, 1, 2000);

  controls = new OrbitControls(camera, renderer.domElement);
  controls.enablePan = false;
  controls.enableDamping = true;
  controls.dampingFactor = 0.05;
  controls.autoRotate = false;   /* stay still so the card sits front-on, centred */
  /* disable hard distance clamps so frameCard's exact fit is always honoured
     (a fixed clamp can push the camera too close on short/wide screens and
     make the card overflow the stage) */
  controls.minDistance = 1;
  controls.maxDistance = 4000;
  controls.maxPolarAngle = Math.PI / 2;
  controls.addEventListener('start', function () { controls.autoRotate = false; userMoved = true; });

  /* ---- lights ---- */
  scene.add(new THREE.AmbientLight(0xffffff, 0.38));
  scene.add(new THREE.HemisphereLight(0xffffff, 0xdfe3ea, 0.55));

  var sun = new THREE.DirectionalLight(0xfff3e0, 2.6);
  sun.position.set(170, 260, 220);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  sun.shadow.camera.near = 1;
  sun.shadow.camera.far = 900;
  sun.shadow.camera.left = -200;
  sun.shadow.camera.right = 200;
  sun.shadow.camera.top = 200;
  sun.shadow.camera.bottom = -200;
  sun.shadow.bias = -0.0004;
  sun.shadow.normalBias = 0.6;
  scene.add(sun);

  var fill = new THREE.DirectionalLight(0xcfe0ff, 0.7);
  fill.position.set(-200, 60, -140);
  scene.add(fill);

  var rimR = new THREE.DirectionalLight(0xff5a4e, 1.15);
  rimR.position.set(-330, 50, 120);
  scene.add(rimR);
  var rimB = new THREE.DirectionalLight(0x4e8dff, 1.15);
  rimB.position.set(330, 50, 120);
  scene.add(rimB);

  var topKick = new THREE.DirectionalLight(0xffffff, 0.5);
  topKick.position.set(0, 300, 0);
  scene.add(topKick);

  makeEnvironment();

  card = buildCardGroup();
  scene.add(card);

  /* remember the card centre so auto-framing can keep it dead-centre */
  cardCenterY = card.center ? card.center.y : 0;
  controls.target.set(0, cardCenterY, 0);
  framedInit = false;
  userMoved = false;
  frameCard();

  /* ---- realistic studio floor ---- */
  var floor = new THREE.Mesh(
    new THREE.CircleGeometry(360, 96),
    new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, opacity: 0 })
  );
  floor.rotation.x = -Math.PI / 2;
  floor.position.y = -100;
  scene.add(floor);

  resizeObs = new ResizeObserver(function () { scheduleResize(); });
  resizeObs.observe(stage);
  window.addEventListener('resize', function () { scheduleResize(); });
}

function scheduleResize() {
  if (frame) return;
  frame = requestAnimationFrame(function () {
    frame = null;
    resize();
  });
}

function resize() {
  if (!renderer) return;
  var w = stage.clientWidth;
  var h = stage.clientHeight;
  if (w < 2 || h < 2) return;
  /* Always render at least 2x the CSS size so the card stays crisp even on
     laptops/external monitors whose devicePixelRatio is 1 (the backing buffer
     is supersampled → sharp text and edges). */
  var pr = Math.min(2.5, Math.max(2, window.devicePixelRatio || 1));
  renderer.setPixelRatio(pr);
  /* CRITICAL: updateStyle=true keeps the <canvas> CSS box exactly equal to the
     stage. With updateStyle=false the canvas would keep its attribute (=buffer
     w*pr × h*pr) CSS size, overflow the overflow:hidden stage and push the
     centered card into a corner (e.g. bottom-left) on high-pixel-ratio screens. */
  renderer.setSize(w, h, true);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  /* keep the card fitted to whatever window size (laptop or big screen) */
  if (card && !userMoved) frameCard();
}

/* fit the whole card dead-centre on the current stage, for any screen.
   targets a comfortable fraction of the tightest viewport axis so the card is
   big enough to read on short laptop windows while always staying fully visible
   and perfectly centred. */
function frameCard() {
  if (!renderer || !camera || !card) return;
  var w = Math.max(1, stage.clientWidth || 1);
  var h = Math.max(1, stage.clientHeight || 1);
  var aspect = w / h;
  var dims = cardDims();
  /* include the card thickness + a little breathing room */
  var halfW = (dims.w / 2) + 16;
  var halfH = (dims.h / 2) + CARD_T + 14;
  /* the card fills most of the stage so it is clearly visible and readable;
     slightly bigger on wide window shapes so it is never tiny on laptops */
  var fill = aspect > 1.3 ? 0.78 : 0.68;
  var halfFov = (camera.fov / 2) * Math.PI / 180;
  var t = Math.tan(halfFov);
  var distW = halfW / (fill * t * aspect);
  var distH = halfH / (fill * t);
  var dist = Math.max(distW, distH);
  /* allow a near plane but never crop the card — the fit math above already
     guarantees full visibility, so only guard against the camera touching it */
  dist = Math.min(520, Math.max(dist, 40));
  var cc = (card && card.worldCenter) ? card.worldCenter : new THREE.Vector3(0, cardCenterY, 0);
  camera.up.set(0, 1, 0);
  controls.target.set(cc.x, cc.y, cc.z);
  camera.position.set(cc.x, cc.y, dist);
  controls.update();
  framedInit = true;
}

function startLoop() {
  if (running) return;
  running = true;
  var last = performance.now();
  function tick() {
    if (!running) return;
    rafId = requestAnimationFrame(tick);
    var now = performance.now();
    var dt = (now - last) / 1000;
    last = now;
    controls.update(dt);
    renderer.render(scene, camera);
  }
  rafId = requestAnimationFrame(tick);
}

function stopLoop() {
  running = false;
  if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
}

function applyTexture(mat, canvas, fallback) {
  if (canvas) {
    var old = mat.map;
    mat.map = texFromCanvas(canvas, 'cardface');
    mat.color = new THREE.Color(0xffffff);
    mat.needsUpdate = true;
    if (old) old.dispose();
  } else {
    if (mat.map) { mat.map.dispose(); mat.map = null; }
    mat.color.setHex(fallback);
    mat.needsUpdate = true;
  }
}

async function refreshCard() {
  var c = getC();
  if (!c) return;
  for (;;) {
    var key = c.filledDocString ? c.filledDocString() : null;
    if (!key) return;
    if (key === lastKey) return;
    var backP = (c.backDesignSvg ? c.backDesignSvg() : key) || key;

    var fc = null, bc = null;
    try {
      var fcanvas = await getCanvas(hashStr(key), key);
      if (card) fc = fcanvas;
    } catch (e) { fc = null; }
    if (!card) return;
    applyTexture(card.mats.front, fc, 0xf2c94c);

    try {
      var bcanvas = await getCanvas(hashStr(backP), backP);
      if (card) bc = bcanvas;
    } catch (e) { bc = null; }
    if (!card) return;
    applyTexture(card.mats.back, bc, 0x3d6fe8);

    lastKey = key;
  }
}

function show() {
  var c = getC();
  if (!c || !c.state || !c.state.doc) {
    if (c && c.toast) c.toast('Load a card template first.');
    return;
  }
  if (!renderer) {
    try { buildScene(); } catch (e) {
      if (c.toast) c.toast('3D preview not available in this browser.');
      return;
    }
    restoreBg();
    setBackground(bgKey);
  }
  wrap.classList.remove('hidden');
  if (wrap.parentElement) wrap.parentElement.classList.add('three-open');
  opened = true;
  var base = c.state.templateName || c.state.fileName || 'untitled card';
  nameEl.textContent = base + ' · front + back · live values';
  lastKey = null;
  if (refreshing) return;
  refreshing = refreshCard().catch(function () { }).then(function () { refreshing = null; });
  frame = null;
  frame = requestAnimationFrame(function () {
    frame = null;
    resize();
  });
  startLoop();
}

function hide() {
  opened = false;
  wrap.classList.add('hidden');
  if (wrap.parentElement) wrap.parentElement.classList.remove('three-open');
  stopLoop();
}

/* ---- background UI wiring ---- */
var swatches = wrap ? wrap.querySelectorAll('.bg-swatch') : [];
for (var si = 0; si < swatches.length; si++) {
  swatches[si].addEventListener('click', function () {
    setBackground(this.getAttribute('data-bg'));
  });
}
if (bgInput) {
  bgInput.addEventListener('input', function () {
    customColor = bgInput.value;
    bgKey = 'custom';
    setBackground('custom');
  });
}

btnOpen.addEventListener('click', show);
btnClose.addEventListener('click', hide);

var autoOpen = new URLSearchParams(location.search).has('3d');

document.addEventListener('cardfly:render', function () {
  var c = getC();
  if (autoOpen) {
    if (c && c.state && c.state.doc) {
      autoOpen = false;
      show();
    }
    return;
  }
  if (!opened) return;
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(function () {
    if (refreshing) return;
    refreshing = refreshCard().catch(function () { }).then(function () { refreshing = null; });
  }, 260);
});

if (autoOpen) {
  setTimeout(show, 250);
}
