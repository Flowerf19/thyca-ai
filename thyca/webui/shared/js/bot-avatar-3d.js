const THREE_MODULE = "../../vendor/three-r128.min.js";
const MODEL_MODULE = "./bot-avatar-3d-model.js";
const THUMBNAIL_CACHE_LIMIT = 128;
const thumbnailCache = new Map();
let thumbnailQueue = Promise.resolve();

function canonicalize(value) {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonicalize(value[key])]));
}

// Wooden turntable pedestal from the v3.5 reference: the disc that the
// carved note stands on. Scene-level (it does not rotate with the model).
function makePedestal(THREE) {
  const geo = new THREE.CylinderGeometry(1.85, 2.0, 0.24, 48);
  const mat = new THREE.MeshStandardMaterial({ color: 0xdcc5a5, roughness: 0.75, metalness: 0.05 });
  const pedestal = new THREE.Mesh(geo, mat);
  pedestal.position.y = -1.78;
  pedestal.receiveShadow = true;
  return pedestal;
}

export function createAvatar3DController() {
  let renderer = null;
  let scene = null;
  let camera = null;
  let model = null;
  let slot = null;
  let resizeObserver = null;
  let visibilityObserver = null;
  let frame = 0;
  let disposed = false;
  let generation = 0;
  let canvas = null;
  let importPromise = null;
  let reducedMotion = false;
  let visible = true;
  let onscreen = true;
  let motionQuery = null;
  let preferenceObserver = null;
  let gazeX = 0;
  let gazeY = 0;
  let liveKey = null;
  let targetRotY = 0.38;
  let targetRotX = 0.02;
  const followers = new Map();
  let threeRef = null;
  let modelRef = null;

  const motionIsReduced = () => motionQuery?.matches === true
    || document.documentElement.dataset.reduceMotion === "on";

  const refreshMotion = () => {
    reducedMotion = motionIsReduced();
    if (reducedMotion || !visible || !onscreen) stop();
    else if (renderer && model && !frame) frame = requestAnimationFrame(render);
    if (renderer && reducedMotion && visible && onscreen) {
      paintFollowers(0);
      if (model) {
        model.animate(0, { reducedMotion: true, gazeX, gazeY });
        renderer.render(scene, camera);
      }
    }
  };

  const onVisibilityChange = () => {
    visible = !document.hidden;
    if (visible) refreshMotion();
    else stop();
  };

  const onMotionChange = () => refreshMotion();

  const watchSlot = target => {
    onscreen = false;
    visibilityObserver = new IntersectionObserver(entries => {
      onscreen = entries.some(entry => entry.isIntersecting && entry.intersectionRatio > 0);
      if (onscreen) refreshMotion();
      else stop();
    });
    visibilityObserver.observe(target);
  };

  const startWatching = target => {
    visible = !document.hidden;
    watchSlot(target);
    motionQuery = matchMedia("(prefers-reduced-motion: reduce)");
    motionQuery.addEventListener?.("change", onMotionChange);
    document.addEventListener("visibilitychange", onVisibilityChange);
    preferenceObserver = new MutationObserver(onMotionChange);
    preferenceObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-reduce-motion"] });
    reducedMotion = motionIsReduced();
  };

  const stop = () => {
    if (frame) cancelAnimationFrame(frame);
    frame = 0;
  };

  const resize = () => {
    if (!renderer || !slot || !camera) return;
    const { width, height } = slot.getBoundingClientRect();
    if (width <= 0 || height <= 0) return;
    const ratio = Math.min(globalThis.devicePixelRatio || 1, 2);
    renderer.setPixelRatio(ratio);
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  };

  // Inactive slots: 2D canvases repainted from the same renderer each frame.
  // One WebGL context, one RAF loop — followers just snapshot their own
  // model scene into the framebuffer and blit it out before the live render.
  const paintFollowers = (elapsed) => {
    if (!renderer || !followers.size) return;
    let size = null;
    let ratio = 1;
    try {
      size = renderer.getSize(new THREE.Vector2());
      ratio = renderer.getPixelRatio();
      renderer.setPixelRatio(1);
      renderer.setSize(128, 128, false);
    } catch { return; }
    for (const f of followers.values()) {
      try {
        f.model.animate(elapsed, { reducedMotion });
        renderer.render(f.scene, f.camera);
        f.ctx.drawImage(canvas, 0, 0, 128, 128);
      } catch { /* drop the follower frame, keep painting the rest */ }
    }
    try {
      renderer.setPixelRatio(ratio);
      renderer.setSize(size.x, size.y, false);
    } catch { /* restore best-effort */ }
  };

  const render = (time = 0) => {
    frame = 0;
    if (disposed || !renderer || !scene || !camera) return;
    paintFollowers(time / 1000);
    if (model) {
      model.group.rotation.y += (targetRotY - model.group.rotation.y) * 0.1;
      model.group.rotation.x += (targetRotX - model.group.rotation.x) * 0.1;
      model.animate(time / 1000, { reducedMotion, gazeX, gazeY });
      renderer.render(scene, camera);
    }
    if (!reducedMotion && visible && onscreen) frame = requestAnimationFrame(render);
  };

  const restoreSVG = element => {
    element?.querySelectorAll('svg[data-avatar3d-hidden="true"]').forEach(svg => {
      svg.style.visibility = "";
      delete svg.dataset.avatar3dHidden;
    });
  };

  const clearModel = () => {
    if (!model) return;
    scene?.remove(model.group);
    model.dispose();
    model = null;
  };

  const loadThree = () => {
    if (!importPromise) importPromise = import(THREE_MODULE).then(module => {
      const THREE = globalThis.THREE || module.default || module;
      if (!THREE?.WebGLRenderer) throw new Error("Three.js WebGLRenderer unavailable");
      return THREE;
    });
    return importPromise;
  };

  const paletteFrom = element => {
    const style = getComputedStyle(element);
    const names = ["color-bg-selected", "color-ink", "color-accent", "note-do", "note-re", "note-mi", "note-fa", "note-sol", "note-la", "note-si", "note-cream", "note-ink", "note-gold", "status-error", "status-waiting", "status-done"];
    const palette = {};
    names.forEach(name => {
      const value = style.getPropertyValue(`--${name}`).trim();
      if (value) palette[name] = value;
    });
    ["star", "hollow", "single-flag", "double-flag", "dotted", "fermata", "plain"].forEach(variant => {
      ["light", "side"].forEach(tone => {
        const value = style.getPropertyValue(`--wood-${variant}-${tone}`).trim();
        if (value) palette[`wood-${variant}-${tone}`] = value;
      });
    });
    return palette;
  };

  const thumbnailKey = (input, palette) => JSON.stringify({
    input: canonicalize(input),
    palette: canonicalize(palette),
  });

  const hideSVG = element => {
    element?.querySelectorAll("svg").forEach(svg => {
      svg.dataset.avatar3dHidden = "true";
      svg.style.visibility = "hidden";
    });
  };

  const mount = async (target, input = {}) => {
    if (disposed || !target) return false;
    const request = ++generation;
    stop();
    resizeObserver?.disconnect(); resizeObserver = null;
    visibilityObserver?.disconnect(); visibilityObserver = null;
    let key = null;
    try { key = thumbnailKey(input, paletteFrom(target)); } catch { key = null; }
    // Same input + palette: keep the live model, only reparent the one canvas
    // into the freshly rendered slot instead of rebuilding geometry/observers.
    if (model && renderer && scene && camera && canvas && key && key === liveKey) {
      const previous = slot;
      slot = target;
      restoreSVG(previous);
      slot.appendChild(canvas);
      hideSVG(slot);
      resizeObserver = new ResizeObserver(resize);
      resizeObserver.observe(slot);
      watchSlot(slot);
      resize();
      refreshMotion();
      return true;
    }
    document.removeEventListener("visibilitychange", onVisibilityChange);
    motionQuery?.removeEventListener?.("change", onMotionChange);
    motionQuery = null;
    preferenceObserver?.disconnect(); preferenceObserver = null;
    clearModel();
    restoreSVG(slot);
    if (slot && canvas?.parentNode === slot) slot.removeChild(canvas);
    slot = target;
    liveKey = null;
    try {
      const [THREE, modelModule] = await Promise.all([loadThree(), import(MODEL_MODULE)]);
      if (disposed || request !== generation) return false;
      threeRef = THREE;
      modelRef = modelModule;
      if (!renderer) {
        renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, powerPreference: "low-power" });
        // v3.5 verbatim: raw output (no encoding/tone-mapping) + shadow maps.
        renderer.setClearColor(0x000000, 0);
        renderer.shadowMap.enabled = true;
        canvas = renderer.domElement;
        canvas.setAttribute("aria-hidden", "true");
        scene = new THREE.Scene();
        camera = new THREE.PerspectiveCamera(36, 1, 0.1, 100);
        camera.position.set(0, 0.4, 7.2);
        scene.add(new THREE.AmbientLight(0xfff3de, 0.78));
        const key = new THREE.DirectionalLight(0xfff7e8, 0.95);
        key.position.set(4, 6, 5); key.castShadow = true; scene.add(key);
        const rim = new THREE.DirectionalLight(0xe8a86b, 0.45);
        rim.position.set(-5, -2, -3); scene.add(rim);
        scene.add(makePedestal(THREE));
        canvas.addEventListener("webglcontextlost", onContextLost);
      }
      model = modelModule.createWoodAvatarModel(THREE, {
        ...input,
        palette: paletteFrom(target),
        canvasFactory: () => document.createElement("canvas"),
      });
      // Keep the current rotation targets across rebuilds so a model swap
      // on selection change doesn't visibly snap back to the base pose.
      model.group.rotation.set(targetRotX, targetRotY, 0);
      scene.add(model.group);
      slot.appendChild(canvas);
      canvas.style.width = "100%";
      canvas.style.height = "100%";
      canvas.style.display = "block";
      hideSVG(slot);
      resizeObserver = new ResizeObserver(resize);
      resizeObserver.observe(slot);
      startWatching(slot);
      resize();
      refreshMotion();
      liveKey = key;
      return true;
    } catch {
      if (request === generation) {
        clearModel();
        resizeObserver?.disconnect(); resizeObserver = null;
        visibilityObserver?.disconnect(); visibilityObserver = null;
        document.removeEventListener("visibilitychange", onVisibilityChange);
        motionQuery?.removeEventListener?.("change", onMotionChange);
        motionQuery = null;
        preferenceObserver?.disconnect(); preferenceObserver = null;
        if (canvas?.parentNode) canvas.parentNode.removeChild(canvas);
        restoreSVG(slot);
        renderer?.dispose(); renderer = null; scene = null; camera = null; canvas = null;
      }
      return false;
    }
  };

  function onContextLost(event) {
    event.preventDefault();
    stop();
    clearModel();
    for (const f of followers.values()) disposeFollower(f);
    followers.clear();
    liveKey = null;
    canvas?.remove();
    restoreSVG(slot);
  }

  const update = async input => {
    if (!slot || disposed) return false;
    return mount(slot, input);
  };

  const captureThumbnail = async input => {
    if (disposed || !renderer || !model || !scene || !camera || !canvas || !slot || canvas.parentNode !== slot) return null;
    const request = generation;
    let THREE;
    let modelModule;
    try {
      [THREE, modelModule] = await Promise.all([loadThree(), import(MODEL_MODULE)]);
    } catch { return null; }
    if (disposed || request !== generation || !renderer || !model || !scene || !camera || !canvas || !slot || canvas.parentNode !== slot) return null;
    let palette;
    let key;
    try {
      palette = paletteFrom(slot);
      key = thumbnailKey(input, palette);
    } catch { return null; }
    if (thumbnailCache.has(key)) return thumbnailCache.get(key);

    let image = null;
    let temporaryModel = null;
    let size;
    let pixelRatio;
    try {
      size = renderer.getSize(new THREE.Vector2());
      pixelRatio = renderer.getPixelRatio();
    } catch { return null; }
    stop();
    try {
      const thumbnailScene = new THREE.Scene();
      thumbnailScene.add(new THREE.AmbientLight(0xfff3de, 0.78));
      const keyLight = new THREE.DirectionalLight(0xfff7e8, 0.95);
      keyLight.position.set(4, 6, 5);
      thumbnailScene.add(keyLight);
      const rimLight = new THREE.DirectionalLight(0xe8a86b, 0.45);
      rimLight.position.set(-5, -2, -3);
      thumbnailScene.add(rimLight);
      thumbnailScene.add(makePedestal(THREE));
      const thumbnailCamera = new THREE.PerspectiveCamera(36, 1, 0.1, 100);
      thumbnailCamera.position.set(0, 0.12, 6.1);
      temporaryModel = modelModule.createWoodAvatarModel(THREE, {
        ...input,
        palette,
        canvasFactory: () => document.createElement("canvas"),
      });
      temporaryModel.group.rotation.set(0.02, 0.38, 0);
      thumbnailScene.add(temporaryModel.group);
      renderer.setPixelRatio(1);
      renderer.setSize(128, 128, false);
      temporaryModel.animate(0, { reducedMotion: true });
      renderer.render(thumbnailScene, thumbnailCamera);
      image = canvas.toDataURL("image/png") || null;
    } catch {
      image = null;
    } finally {
      try { temporaryModel?.dispose(); } catch { image = null; }
      try { renderer.setPixelRatio(pixelRatio); } catch { image = null; }
      try { renderer.setSize(size.x, size.y, false); } catch { image = null; }
      try { renderer.render(scene, camera); } catch { image = null; }
      try { refreshMotion(); } catch { image = null; }
    }
    if (!image) return null;
    thumbnailCache.set(key, image);
    if (thumbnailCache.size > THUMBNAIL_CACHE_LIMIT) thumbnailCache.delete(thumbnailCache.keys().next().value);
    return image;
  };

  const thumbnail = input => {
    const result = thumbnailQueue.then(() => captureThumbnail(input));
    thumbnailQueue = result.catch(() => null);
    return result;
  };

  const disposeFollower = (f) => {
    try { f.model?.dispose(); } catch { /* best-effort */ }
    f.canvas?.remove();
  };

  /* Attach a per-slot 2D canvas animated by the shared renderer. `key` is a
     stable id (bot id / session id) so re-renders repoint the same follower
     instead of rebuilding its model; returns false → caller keeps SVG. */
  const follow = async (key, target, input = {}) => {
    if (disposed || !renderer || !model || !target) return false;
    // Modules are cached after the first mount: this branch runs fully
    // synchronous, so canvases reparent in the same task — no empty flash.
    let THREE = threeRef;
    let modelModule = modelRef;
    if (!THREE || !modelModule) {
      try {
        [THREE, modelModule] = await Promise.all([loadThree(), import(MODEL_MODULE)]);
        threeRef = THREE;
        modelRef = modelModule;
      } catch { return false; }
      if (disposed || !renderer || !model) return false;
    }
    let palette;
    let inputKey;
    try {
      palette = paletteFrom(target);
      inputKey = thumbnailKey(input, palette);
    } catch { return false; }
    const existing = followers.get(key);
    if (existing && existing.inputKey === inputKey) {
      existing.slot = target;
      if (existing.canvas.parentNode !== target) target.appendChild(existing.canvas);
      hideSVG(target);
      return true;
    }
    if (existing) disposeFollower(existing);
    const cnv = document.createElement("canvas");
    cnv.width = 128; cnv.height = 128;
    cnv.className = "avatar-3d-live";
    cnv.setAttribute("aria-hidden", "true");
    const ctx = cnv.getContext("2d");
    if (!ctx) return false;
    let follower;
    try {
      const followerScene = new THREE.Scene();
      followerScene.add(new THREE.AmbientLight(0xfff3de, 0.78));
      const followerKey = new THREE.DirectionalLight(0xfff7e8, 0.95);
      followerKey.position.set(4, 6, 5);
      followerScene.add(followerKey);
      const followerRim = new THREE.DirectionalLight(0xe8a86b, 0.45);
      followerRim.position.set(-5, -2, -3);
      followerScene.add(followerRim);
      followerScene.add(makePedestal(THREE));
      const followerModel = modelModule.createWoodAvatarModel(THREE, {
        ...input,
        palette,
        canvasFactory: () => document.createElement("canvas"),
      });
      followerModel.group.rotation.set(0.02, 0.38, 0);
      followerScene.add(followerModel.group);
      const followerCamera = new THREE.PerspectiveCamera(36, 1, 0.1, 100);
      followerCamera.position.set(0, 0.12, 6.1);
      follower = { slot: target, canvas: cnv, ctx, scene: followerScene, camera: followerCamera, model: followerModel, inputKey };
    } catch { return false; }
    followers.set(key, follower);
    target.appendChild(cnv);
    hideSVG(target);
    paintFollowers(0);
    return true;
  };

  /* Drop followers whose keys are no longer on screen. `prefix` scopes the
     sweep: sidebar passes "sb:" so chat-surface followers stay untouched. */
  const pruneFollowers = (keys, prefix = "") => {
    for (const [key, f] of followers) {
      if (key.startsWith(prefix) && !keys.has(key)) {
        followers.delete(key);
        disposeFollower(f);
      }
    }
  };

  // Pointer gaze for the live model: eyes drift toward the cursor. Sidebar
  // owns the pointer source; clamps keep the pupils inside their rims.
  const gaze = (x = 0, y = 0) => {
    const gx = Number(x);
    const gy = Number(y);
    gazeX = Number.isFinite(gx) ? Math.max(-0.15, Math.min(0.15, gx)) : 0;
    gazeY = Number.isFinite(gy) ? Math.max(-0.15, Math.min(0.15, gy)) : 0;
  };

  // Drag-to-rotate (v3.5): deltas in px steer the model's yaw/pitch toward a
  // lerped target; pitch stays clamped so the note never flips over.
  const rotate = (dx = 0, dy = 0) => {
    const rx = Number(dx);
    const ry = Number(dy);
    if (Number.isFinite(rx)) targetRotY += rx * 0.012;
    if (Number.isFinite(ry)) targetRotX = Math.max(-0.55, Math.min(0.65, targetRotX + ry * 0.008));
    if (renderer && model && (reducedMotion || !frame)) {
      model.group.rotation.y = targetRotY;
      model.group.rotation.x = targetRotX;
      model.animate(0, { reducedMotion: true, gazeX, gazeY });
      renderer.render(scene, camera);
    }
  };

  const dispose = () => {
    if (disposed) return;
    disposed = true; generation++;
    stop(); resizeObserver?.disconnect(); resizeObserver = null;
    visibilityObserver?.disconnect(); visibilityObserver = null;
    document.removeEventListener("visibilitychange", onVisibilityChange);
    motionQuery?.removeEventListener?.("change", onMotionChange);
    motionQuery = null;
    preferenceObserver?.disconnect(); preferenceObserver = null;
    clearModel();
    if (canvas) {
      canvas.removeEventListener("webglcontextlost", onContextLost);
      canvas.remove();
    }
    for (const f of followers.values()) disposeFollower(f);
    followers.clear();
    renderer?.dispose();
    renderer?.forceContextLoss?.();
    renderer = null; scene = null; camera = null; canvas = null; slot = null;
    liveKey = null; gazeX = 0; gazeY = 0;
  };

  return { mount, update, thumbnail, gaze, rotate, follow, pruneFollowers, dispose };
}
