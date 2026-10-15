/* Shared handle to the single 3D avatar controller (owned by the chat
   sidebar). Other surfaces — message brand heads, mention popup, bot drawer —
   request cached snapshots through it instead of creating their own
   renderer. Without a live controller every call resolves null and callers
   keep their SVG fallback. */

let controller = null;
let dead = false;

export function registerAvatar3D(value) {
  controller = value;
}

export function markAvatar3DDead() {
  dead = true;
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
   decorative overlay — slots are already aria-hidden. */
export function upgradeAvatarImg(slot, input) {
  if (!slot || !controller || dead) return;
  void avatar3dThumb(input).then((url) => {
    if (!url || !slot.isConnected) return;
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
