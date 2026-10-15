import { avatarVariantFor, avatarStateFor, teamBadgeState } from "./bot-avatar.js";

const WOOD = {
  star: ["#BE5530", "#D97A52", "#7A3318", "do"],
  hollow: ["#865580", "#A6749F", "#563152", "re"],
  "single-flag": ["#2B7A72", "#479E94", "#16473F", "mi"],
  "double-flag": ["#386288", "#5A86AC", "#1E3A55", "fa"],
  dotted: ["#537E56", "#73A576", "#2E4E30", "sol"],
  fermata: ["#C48324", "#DFA44A", "#7D5010", "la"],
  plain: ["#735238", "#95745A", "#452F1E", "si"],
};

function woodTexture(THREE, base, light, side, canvasFactory) {
  const canvas = canvasFactory();
  canvas.width = canvas.height = 256;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas 2D context unavailable for avatar texture");
  ctx.fillStyle = base;
  ctx.fillRect(0, 0, 256, 256);
  for (let i = 0; i < 48; i++) {
    ctx.strokeStyle = i % 2 === 0 ? side : light;
    ctx.globalAlpha = i % 2 === 0 ? 0.16 : 0.08;
    ctx.lineWidth = 1 + (i % 3);
    ctx.beginPath();
    const y = i / 48 * 256;
    ctx.moveTo(0, y);
    ctx.bezierCurveTo(85, y + Math.sin(i) * 10, 170, y - Math.cos(i) * 10, 256, y);
    ctx.stroke();
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  return texture;
}

export function createWoodAvatarModel(THREE, { bot, members = null, palette = {}, canvasFactory } = {}) {
  if (!THREE || !canvasFactory) throw new TypeError("THREE and canvasFactory are required");
  const groupMembers = Array.isArray(members) ? members.filter(member => member && typeof member === "object") : [];
  if (groupMembers.length > 1) {
    const visible = groupMembers.slice(0, 3);
    const extraCount = groupMembers.length - visible.length;
    const pair = visible.length === 2;
    const slots = pair
      ? [[-0.72, -0.1, 0, 0.68], [0.72, 0.04, 0, 0.68]]
      : [[-0.72, 0.18, 0, 0.48], [0.72, 0.18, 0, 0.48], [0, -0.66, 0.12, 0.54]];
    const models = visible.map(member => createWoodAvatarModel(THREE, { bot: member, palette, canvasFactory }));
    const team = new THREE.Group();
    models.forEach((model, index) => {
      const [x, y, z, scale] = slots[index];
      model.group.position.set(x, y, z);
      model.group.scale.setScalar(scale);
      team.add(model.group);
    });
    let badgeTexture = null;
    let badgeMaterial = null;
    if (extraCount > 0) {
      const canvas = canvasFactory();
      canvas.width = 128; canvas.height = 64;
      const ctx = canvas.getContext("2d");
      if (!ctx) { models.forEach(model => model.dispose()); throw new Error("Canvas 2D context unavailable for group badge"); }
      ctx.fillStyle = palette["color-bg-selected"] || "#EEE5D7";
      ctx.beginPath(); ctx.moveTo(32, 4); ctx.lineTo(96, 4); ctx.arcTo(124, 4, 124, 32, 28);
      ctx.arcTo(124, 60, 96, 60, 28); ctx.lineTo(32, 60); ctx.arcTo(4, 60, 4, 32, 28);
      ctx.arcTo(4, 4, 32, 4, 28); ctx.fill();
      ctx.fillStyle = palette["color-ink"] || "#3B332A";
      ctx.font = "bold 34px sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText(`+${extraCount}`, 64, 33);
      badgeTexture = new THREE.CanvasTexture(canvas);
      badgeMaterial = new THREE.SpriteMaterial({ map: badgeTexture, transparent: true });
      const badge = new THREE.Sprite(badgeMaterial);
      badge.position.set(-0.95, 1.0, 0.4); badge.scale.set(0.78, 0.39, 1);
      team.add(badge);
    }
    const badgeState = teamBadgeState(groupMembers);
    const statusColors = { error: palette["status-error"] || "#B84239", waiting: palette["status-waiting"] || "#3E6B94", done: palette["status-done"] || "#4A8759" };
    let statusGeometry = null;
    let statusMaterial = null;
    if (statusColors[badgeState]) {
      statusGeometry = new THREE.SphereGeometry(0.13, 16, 16);
      statusMaterial = new THREE.MeshBasicMaterial({ color: statusColors[badgeState] });
      const indicator = new THREE.Mesh(statusGeometry, statusMaterial);
      indicator.position.set(1.0, 0.92, 0.45);
      team.add(indicator);
    }
    return {
      group: team,
      badgeState,
      animate(elapsed, options) { models.forEach(model => model.animate(elapsed, options)); },
      dispose() {
        models.forEach(model => model.dispose());
        if (badgeMaterial) badgeMaterial.dispose();
        if (badgeTexture) badgeTexture.dispose();
        if (statusGeometry) statusGeometry.dispose();
        if (statusMaterial) statusMaterial.dispose();
      },
    };
  }
  if (groupMembers.length === 1) bot = groupMembers[0];
  const variant = avatarVariantFor(bot);
  const state = avatarStateFor(bot);
  const [defaultBase, light, side, note] = WOOD[variant];
  const colors = { ...palette };
  const offline = state === "offline";
  const base = offline
    ? colors["wood-offline-base"] || "#9E9080"
    : colors[`note-${note}`] || colors["color-accent"] || defaultBase;
  const woodSide = offline
    ? colors["wood-offline-side"] || "#5E5348"
    : colors[`wood-${variant}-side`] || side;
  const woodMap = woodTexture(THREE, base, colors[`wood-${variant}-light`] || light, woodSide, canvasFactory);
  const materials = [];
  const material = (options) => { const value = new THREE.MeshStandardMaterial(options); materials.push(value); return value; };
  const wood = material({ map: woodMap, roughness: 0.9, metalness: 0 });
  const cream = material({ color: colors.noteCream || colors["note-cream"] || "#FAF3E6", roughness: 0.85 });
  const dark = material({ color: colors.noteInk || colors["note-ink"] || "#26160D", roughness: 0.8 });
  const gold = material({ color: colors.noteGold || colors["note-gold"] || "#B79355", roughness: 0.85 });
  const white = material({ color: 0xffffff });
  const group = new THREE.Group();
  const trackedGeometries = new Set();
  let disposed = false;
  const mesh = (geometry, mat, parent = group) => {
    trackedGeometries.add(geometry);
    const obj = new THREE.Mesh(geometry, mat);
    obj.castShadow = true;
    parent.add(obj);
    return obj;
  };
  const head = new THREE.Group();
  head.position.set(-0.38, -0.78, 0);
  head.rotation.z = THREE.MathUtils.degToRad(20);
  group.add(head);
  const headGeo = new THREE.SphereGeometry(0.95, 40, 32);
  headGeo.scale(1.22, 0.88, 0.68);
  mesh(headGeo, wood, head);
  if (variant === "hollow") {
    const hollow = new THREE.SphereGeometry(0.62, 32, 24);
    hollow.scale(1.2, 0.82, 0.76);
    mesh(hollow, cream, head).position.set(0, 0, 0.08);
  }

  const eyes = new THREE.Group();
  eyes.position.set(0, 0, 0.62);
  head.add(eyes);
  if (state === "done") {
    const arc = new THREE.TorusGeometry(0.19, 0.055, 12, 24, Math.PI);
    for (const x of [-0.34, 0.34]) mesh(arc, cream, eyes).position.set(x, 0.02, 0);
  } else if (state === "error" || state === "offline") {
    const bar = new THREE.CylinderGeometry(0.045, 0.045, 0.36, 12);
    for (const x of [-0.34, 0.34]) {
      if (state === "offline") {
        const line = mesh(bar, cream, eyes); line.rotation.z = Math.PI / 2; line.position.set(x, 0.05, 0);
      } else for (const angle of [Math.PI / 4, -Math.PI / 4]) {
        const line = mesh(bar, cream, eyes); line.rotation.z = angle; line.position.set(x, 0.08, 0);
      }
    }
  } else {
    for (const x of [-0.34, 0.34]) {
      const rimGeo = new THREE.SphereGeometry(0.21, 20, 20); rimGeo.scale(0.9, 1.1, 0.35);
      mesh(rimGeo, cream, eyes).position.set(x, 0.1, -0.02);
      const pupilGeo = new THREE.SphereGeometry(0.16, 20, 20); pupilGeo.scale(0.88, 1.08, 0.55);
      mesh(pupilGeo, dark, eyes).position.set(x, 0.1, 0.03);
      mesh(new THREE.SphereGeometry(0.05, 12, 12), white, eyes).position.set(x - 0.05, 0.17, 0.11);
    }
    const smile = mesh(new THREE.TorusGeometry(0.13, 0.036, 10, 20, Math.PI), cream, eyes);
    smile.rotation.z = Math.PI; smile.position.set(0, -0.14, 0.02);
  }

  const stemHeight = variant === "fermata" ? 1.45 : 2.25;
  const stemY = variant === "fermata" ? 0.12 : 0.52;
  mesh(new THREE.CylinderGeometry(0.16, 0.17, stemHeight, 24), wood).position.set(0.55, stemY, 0);
  mesh(new THREE.SphereGeometry(0.16, 20, 20), wood).position.set(0.55, stemY + stemHeight / 2, 0);
  if (variant === "star") {
    const star = new THREE.OctahedronGeometry(0.32, 0); star.scale(1, 1.25, 0.45);
    mesh(star, gold).position.set(1.42, 1.58, 0.15);
  }
  if (variant === "star" || variant === "single-flag" || variant === "double-flag") {
    const curve = new THREE.CatmullRomCurve3([
      new THREE.Vector3(0.55, 1.55, 0), new THREE.Vector3(1.12, 1.25, 0.08),
      new THREE.Vector3(1.32, 0.72, 0.05), new THREE.Vector3(1.18, 0.35, 0),
    ]);
    const flag = mesh(new THREE.TubeGeometry(curve, 24, 0.12, 16, false), wood);
    if (variant === "double-flag") {
      const second = flag.clone(); second.position.y = -0.58; group.add(second);
    }
  } else if (variant === "dotted") {
    mesh(new THREE.SphereGeometry(0.24, 24, 24), wood).position.set(1.25, 0.1, 0.1);
  } else if (variant === "fermata") {
    mesh(new THREE.SphereGeometry(0.20, 20, 20), wood).position.set(0.55, 1.22, 0);
    mesh(new THREE.TorusGeometry(0.65, 0.11, 16, 32, Math.PI), wood).position.set(0.55, 1.18, 0);
  }
  const floating = new THREE.Group();
  const floatHead = mesh(new THREE.SphereGeometry(0.18, 16, 16), gold, floating);
  floatHead.scale.set(1.2, 0.85, 0.7);
  const floatStem = mesh(new THREE.CylinderGeometry(0.04, 0.04, 0.45, 10), gold, floating);
  floatStem.position.set(0.15, 0.22, 0);
  floating.position.set(1.55, 1.3, 0.2); floating.visible = state === "running"; group.add(floating);
  const thinking = new THREE.Group();
  [0, 1, 2].forEach(i => {
    const dot = mesh(new THREE.SphereGeometry(0.11 + i * 0.025, 16, 16), gold, thinking);
    dot.position.set(-1.35 + i * 0.32, 0.4 + i * 0.32, 0.25);
  });
  thinking.visible = state === "thinking"; group.add(thinking);

  // The base renderer consumes one bot at a time; group membership is retained
  // as input for future team composition, never inferred from bot identifiers.
  void members;
  return {
    group,
    animate(elapsed, { reducedMotion = false, gazeX = 0, gazeY = 0 } = {}) {
      if (disposed) return;
      eyes.position.x = gazeX; eyes.position.y = gazeY;
      if (reducedMotion) {
        floating.position.y = 1.3; floating.rotation.y = 0;
        thinking.children.forEach((dot, i) => { dot.position.y = 0.4 + i * 0.32; });
        group.position.y = state === "offline" ? -0.08 : 0;
        group.rotation.z = state === "offline" ? -0.05 : (state === "thinking" ? 0.08 : 0);
        return;
      }
      floating.position.y = 1.25 + ((elapsed * 0.9) % 0.8);
      floating.rotation.y = elapsed * 2.2;
      thinking.children.forEach((dot, i) => { dot.position.y = 0.4 + i * 0.32 + Math.sin(elapsed * 5 - i) * 0.07; });
      // Per-state body motion from the reference (scaled for small slots);
      // rotation.x/y belong to the controller's drag lerp, untouched here.
      if (state === "running") {
        group.position.y = Math.abs(Math.sin(elapsed * 6.5)) * 0.14;
        group.rotation.z = Math.sin(elapsed * 6.5) * 0.03;
      } else if (state === "thinking") {
        group.position.y = Math.sin(elapsed * 2.2) * 0.04;
        group.rotation.z = 0.08 + Math.sin(elapsed * 2.0) * 0.02;
      } else if (state === "offline") {
        group.position.y = -0.08;
        group.rotation.z = -0.05;
      } else {
        group.position.y = Math.sin(elapsed * 2.4) * 0.04;
        group.rotation.z = 0;
      }
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      trackedGeometries.forEach(geometry => geometry.dispose());
      const textures = new Set();
      materials.forEach(value => { if (value.map) textures.add(value.map); value.dispose(); });
      textures.forEach(texture => texture.dispose());
    },
  };
}
