/* Shared handle to the single 3D avatar controller (owned by the chat
   sidebar). Other surfaces — message brand heads, mention popup, bot drawer,
   settings bot list — request cached snapshots through it instead of creating
   their own renderer. Without a live controller every call resolves null and
   callers keep their SVG fallback. */

let controller = null;
let dead = false;
const pending = [];

export function registerAvatar3D(value) {
  controller = value;
  // Flush slots that asked before the controller was mounted.
  for (const entry of pending.splice(0)) {
    upgradeAvatarImg(entry.slot, entry.input, entry.retries + 1);
  }
}

export function markAvatar3DDead() {
  dead = true;
  pending.length = 0;
}

export function avatar3dThumb(input) {
  if (!controller || dead) return Promise.resolve(null);
  return controller.thumbnail(input).catch(() => null);
}

/* Live animated slot (thinking/running/done effects) driven by the shared
   renderer. Keys with the "chat:" prefix are never pruned by the sidebar's
   own follower bookkeeping. */
export function followAvatar3D(key, slot, input) {
  if (!controller || dead || typeof controller.follow !== "function") {
    return Promise.resolve(false);
  }
  return controller.follow(key, slot, input).catch(() => false);
}

/* Swap a slot's fallback SVG for its cached 3D snapshot once rendered. The
   slot keeps its size (SVG stays in flow, just hidden) and the image is a
   decorative overlay — slots are already aria-hidden. If the controller is
   not up yet (transcript renders before the sidebar mounts) the request is
   queued for the registration flush, then retried briefly. */
export function upgradeAvatarImg(slot, input, retries = 0) {
  if (!slot || dead) return;
  if (!controller) {
    if (retries < 2) pending.push({ slot, input, retries });
    return;
  }
  void avatar3dThumb(input).then((url) => {
    if (!url) {
      // Controller up but renderer still mounting — retry a few beats.
      if (retries < 10) setTimeout(() => upgradeAvatarImg(slot, input, retries + 1), 300);
      return;
    }
    if (!slot.isConnected) return;
    slot.querySelectorAll("svg").forEach((svg) => {
      svg.dataset.avatar3dHidden = "true";
      svg.style.visibility = "hidden";
    });
    let img = slot.querySelector("img.avatar-3d-img");
    if (!img) {
      img = document.createElement("img");
      img.className = "avatar-3d-img";
      img.alt = "";
      img.draggable = false;
      img.setAttribute("aria-hidden", "true");
      slot.appendChild(img);
    }
    img.src = url;
  });
}
