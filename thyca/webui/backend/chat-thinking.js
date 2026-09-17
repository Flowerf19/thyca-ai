// Live thinking panel. Wire: llm.thinking { round, delta }. Tools live in the footer.

let serial = 0;

function svgIcon(className, d) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  node.classList.add(className);
  node.setAttribute("viewBox", "0 0 24 24");
  node.setAttribute("aria-hidden", "true");
  node.innerHTML = `<path d="${d}" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>`;
  return node;
}

export function bindThinkingToggle(toggle, thinking) {
  const bodies = thinking?.bodies || (thinking?.body ? [thinking.body] : []);
  const notes = thinking?.notes || (thinking?.note ? [thinking.note] : []);
  if (!toggle || !bodies.length) return;
  toggle.classList.add("thinking-toggle");
  if (!toggle.querySelector(".chevron")) {
    toggle.append(svgIcon("chevron", "M6 9l6 6 6-6"));
  }
  toggle.setAttribute("aria-expanded", "true");
  toggle.setAttribute("aria-controls", bodies.map((body) => body.id).join(" "));
  toggle.setAttribute("aria-label", "Thu gọn phần suy nghĩ");
  toggle.addEventListener("click", () => {
    const open = toggle.getAttribute("aria-expanded") === "true";
    toggle.setAttribute("aria-expanded", open ? "false" : "true");
    for (const body of bodies) body.hidden = open;
    for (const note of notes) note.classList.toggle("is-collapsed", open);
  });
}

export function elapsedLabel(seconds) {
  return `${Math.max(0, seconds)} giây`;
}

export function createThinkingNote({ live = true, onElapsed } = {}) {
  const id = `thought-body-${++serial}`;
  const note = document.createElement("section");
  note.className = "thinking-note";
  note.setAttribute("aria-label", "Suy nghĩ của Thyca");

  const body = document.createElement("div");
  body.className = "thinking-body";
  body.id = id;

  const text = document.createElement("p");
  text.className = "thought-text";
  const output = document.createElement("span");
  output.className = "thought-output";
  text.append(output);
  let caret = null;
  if (live) {
    caret = document.createElement("span");
    caret.className = "streaming-caret";
    caret.setAttribute("aria-hidden", "true");
    text.append(caret);
  }

  const footer = document.createElement("div");
  footer.className = "thought-footer";
  const tool = document.createElement("div");
  tool.className = "search-event";
  tool.hidden = true;
  const toolStatus = document.createElement("span");
  toolStatus.className = "tool-status";
  tool.append(toolStatus);
  footer.append(tool);
  footer.hidden = true;
  body.append(text, footer);
  note.append(body);

  const state = { startedAt: Date.now() };
  let timer = null;
  if (live) {
    const tick = () => {
      if (!note.isConnected) {
        clearInterval(timer);
        return;
      }
      const sec = Math.max(0, Math.floor((Date.now() - state.startedAt) / 1000));
      onElapsed?.(sec);
    };
    timer = setInterval(tick, 1000);
    onElapsed?.(0);
  }

  return {
    note,
    body,
    append(delta) {
      if (typeof delta !== "string" || !delta) return;
      const token = document.createElement("span");
      token.className = "ink-token";
      token.textContent = delta;
      output.append(token);
    },
    setToolLine(line, working = false) {
      if (!line) {
        tool.hidden = true;
        tool.classList.remove("working");
        toolStatus.textContent = "";
      } else {
        tool.hidden = false;
        tool.classList.toggle("working", Boolean(working));
        toolStatus.textContent = line;
      }
      footer.hidden = tool.hidden;
    },
    reset() {
      output.replaceChildren();
      if (caret) caret.hidden = false;
      body.hidden = false;
      note.hidden = false;
      note.classList.remove("is-collapsed");
      tool.hidden = true;
      tool.classList.remove("working");
      toolStatus.textContent = "";
      footer.hidden = true;
      state.startedAt = Date.now();
      onElapsed?.(0);
    },
    settle() {
      if (caret) caret.hidden = true;
      if (timer) clearInterval(timer);
      timer = null;
      footer.hidden = tool.hidden;
      if (!output.textContent && tool.hidden) note.hidden = true;
    },
  };
}

export function settledThinkingNote(text, { toolLine } = {}) {
  const reasoning = typeof text === "string" ? text : "";
  if (!reasoning && !toolLine) return null;
  const thinking = createThinkingNote({ live: false });
  if (reasoning) thinking.append(reasoning);
  thinking.setToolLine(toolLine || "", false);
  thinking.settle();
  return thinking;
}
