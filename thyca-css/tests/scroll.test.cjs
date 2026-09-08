const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { test } = require("node:test");
const vm = require("node:vm");

async function setup({ height = 700, client = 640, reducedMotion = false } = {}) {
  const events = {};
  const observed = [];
  const calls = [];
  let resize;
  const scroll = {
    scrollHeight: height,
    clientHeight: client,
    scrollTop: 0,
    addEventListener: (type, callback) => { events[type] = callback; },
    scrollTo(options) {
      calls.push(options);
      this.scrollTop = Math.max(0, this.scrollHeight - this.clientHeight);
      events.scroll();
    },
  };
  const button = {
    hidden: true,
    addEventListener: (type, callback) => { events[type] = callback; },
  };
  const content = {};
  const nodes = {
    "#conversation-scroll": scroll,
    "#to-bottom": button,
    ".conversation-content": content,
  };
  vm.runInNewContext(readFileSync(`${__dirname}/../app.js`, "utf8"), {
    document: {
      querySelector: selector => nodes[selector] ?? null,
      fonts: { ready: Promise.resolve() },
    },
    window: { matchMedia: () => ({ matches: reducedMotion }) },
    ResizeObserver: class {
      constructor(callback) { resize = callback; }
      observe(node) { observed.push(node); }
    },
  });
  await Promise.resolve();
  return { scroll, button, content, events, observed, calls, resize };
}

test("initial position is the bottom without animation", async () => {
  const { scroll, button, calls } = await setup();
  assert.equal(scroll.scrollTop, 60);
  assert.equal(button.hidden, true);
  assert.equal(calls[0].behavior, "instant");
});

test("short overflow still offers a down button; click hides it at bottom", async () => {
  const { scroll, button, events } = await setup({ height: 660 });
  scroll.scrollTop = 0;
  events.scroll();
  assert.equal(button.hidden, false);
  events.click();
  assert.equal(scroll.scrollTop, 20);
  assert.equal(button.hidden, true);
});

test("disclosure and viewport resizing refresh button visibility", async () => {
  const { scroll, content, button, events, resize, observed } = await setup();
  assert.ok(observed.includes(scroll));
  assert.ok(observed.includes(content));
  scroll.scrollTop = 0;
  events.scroll();
  assert.equal(button.hidden, false);
  scroll.scrollHeight = 640;
  resize();
  assert.equal(button.hidden, true);
  scroll.scrollHeight = 700;
  resize();
  assert.equal(button.hidden, false);
  scroll.clientHeight = 720;
  resize();
  assert.equal(button.hidden, true);
});

test("reduced motion disables smooth button scrolling", async () => {
  const { events, calls } = await setup({ reducedMotion: true });
  events.click();
  assert.equal(calls.at(-1).behavior, "instant");
});

test("content that fits never shows a down button", async () => {
  const { button, resize } = await setup({ height: 640 });
  resize();
  assert.equal(button.hidden, true);
});
