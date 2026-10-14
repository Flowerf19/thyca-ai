import { botAvatarSVG } from "../../shared/js/bot-avatar.js";
import { mentionCandidates } from "./team-data.js";

// @ mention popup (design r9 §4.4): ↑/↓ to move, Enter to insert, Esc to
// close. The last row is always "Nhờ Thyca tạo bot mới" — it inserts a plain
// request text, never a fake bot. Pure filtering via mentionCandidates();
// this module owns only the popup DOM + keyboard contract.
let hub = null;
let popup = null;
let input = null;
let items = [];
let active = -1;

const CREATE_ROW = { id: "", create: true, name: "Nhờ Thyca tạo bot mới" };

function roster() {
  try {
    return hub?.teamRoster?.() || [];
  } catch {
    return [];
  }
}

function currentQuery() {
  if (!input) return null;
  const value = input.value;
  const caret = input.selectionStart ?? value.length;
  return mentionQueryAt(value, caret);
}

/* Pure @-query extraction (node-testable): the popup opens only when the @
   starts the text or follows whitespace — an email like name@gmail.com
   must never trigger it. Returns {query, start} with start at the @, or
   null when no popup should open. */
export function mentionQueryAt(value, caret) {
  const text = String(value || "");
  const pos = Math.min(Math.max(Number(caret) || 0, 0), text.length);
  const before = text.slice(0, pos);
  const match = before.match(/(?:^|\s)@([\p{L}\p{N}_-]*)$/u);
  if (!match) return null;
  return { query: match[1], start: pos - match[1].length - 1 };
}

function close() {
  items = [];
  active = -1;
  if (popup) popup.hidden = true;
}

function render() {
  if (!popup) return;
  popup.replaceChildren();
  if (!items.length) {
    popup.hidden = true;
    return;
  }
  popup.hidden = false;
  items.forEach((bot, index) => {
    const row = document.createElement("button");
    row.type = "button";
    row.role = "option";
    row.className = "mention-item";
    row.id = `mention-item-${index}`;
    row.setAttribute("aria-selected", String(index === active));
    if (index === active) row.classList.add("is-active");
    if (bot.create) {
      const plus = document.createElement("span");
      plus.className = "mention-plus";
      plus.setAttribute("aria-hidden", "true");
      plus.textContent = "+";
      const label = document.createElement("span");
      label.textContent = bot.name;
      row.append(plus, label);
    } else {
      const avatar = document.createElement("span");
      avatar.className = "mention-avatar";
      avatar.setAttribute("aria-hidden", "true");
      avatar.style.setProperty("--team-tint", `var(--color-${bot.tint || "accent"})`);
      avatar.innerHTML = botAvatarSVG(bot, { size: 32 });
      row.append(avatar);
      const main = document.createElement("span");
      main.className = "mention-main";
      const name = document.createElement("strong");
      name.textContent = `@${bot.id}`;
      main.append(name);
      const sub = bot.role || (bot.name && bot.name !== bot.id ? bot.name : "");
      if (sub) {
        const role = document.createElement("span");
        role.className = "mention-role";
        role.textContent = sub;
        main.append(role);
      }
      row.append(main);
    }
    row.addEventListener("mousedown", (event) => {
      // Mousedown (not click): the textarea blurs before click fires.
      event.preventDefault();
      choose(index);
    });
    popup.append(row);
  });
  popup.setAttribute("aria-activedescendant", active >= 0 ? `mention-item-${active}` : "");
}

export function refreshMention() {
  if (!popup || !input || document.activeElement !== input) {
    close();
    return;
  }
  const found = currentQuery();
  if (!found) {
    close();
    return;
  }
  items = [...mentionCandidates(roster(), found.query), CREATE_ROW];
  active = items.length ? 0 : -1;
  render();
}

function choose(index) {
  const bot = items[index];
  const found = currentQuery();
  if (!bot || !found || !input) {
    close();
    return;
  }
  const value = input.value;
  const caret = input.selectionStart ?? value.length;
  const insert = bot.create ? "Nhờ Thyca tạo bot mới: " : `@${bot.id} `;
  input.value = value.slice(0, found.start) + insert + value.slice(caret);
  const next = found.start + insert.length;
  input.setSelectionRange(next, next);
  input.focus();
  close();
}

export function mentionKeydown(event) {
  if (!popup || popup.hidden || !items.length) return false;
  // Vietnamese IME: while composing, Enter commits the preedit — the popup
  // must not steal it. app.js sends only after its own isComposing guard,
  // so returning false here hands the key back to the IME untouched.
  if (event.isComposing || event.keyCode === 229) return false;
  if (event.key === "ArrowDown") {
    event.preventDefault();
    active = (active + 1) % items.length;
    render();
    return true;
  }
  if (event.key === "ArrowUp") {
    event.preventDefault();
    active = (active - 1 + items.length) % items.length;
    render();
    return true;
  }
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    choose(active);
    return true;
  }
  if (event.key === "Escape") {
    event.preventDefault();
    close();
    return true;
  }
  return false;
}

export function initMention(entryHub) {
  hub = entryHub;
  popup = document.querySelector("#mention-popup");
  input = document.querySelector("#message");
  if (!popup || !input) return;
  input.addEventListener("input", refreshMention);
  input.addEventListener("click", refreshMention);
  input.addEventListener("blur", () => {
    // Let mousedown on a row win before the blur closes the popup.
    window.setTimeout(close, 120);
  });
}
