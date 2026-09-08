import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

// Use an existing tooling installation; the mock itself has no dependencies.
const modulePath = process.env.PUPPETEER_MODULE;
if (!modulePath) throw new Error("Set PUPPETEER_MODULE to an installed puppeteer-core entry file.");
const { default: puppeteer } = await import(pathToFileURL(modulePath).href);
const base = process.env.PREVIEW_URL || "http://127.0.0.1:4173/";
const output = process.env.SCREENSHOT_DIR || "/tmp/thyca-screens-qa";
const routes = ["index", "dashboard", "settings", "memories", "trace"];
const widths = [320, 375, 414, 768, 1536];
await mkdir(output, { recursive: true });
const browser = await puppeteer.launch({
  executablePath: process.env.CHROME_PATH || "/opt/google/chrome/chrome",
  headless: true,
  args: ["--no-sandbox"],
});
const report = [];
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  for (const route of routes) {
    for (const width of widths) {
      const mobile = width <= 768;
      await page.setViewport({ width, height: width === 1536 ? 1024 : 812, isMobile: mobile, hasTouch: mobile });
      const response = await page.goto(`${base}${route}.html`, { waitUntil: "networkidle0" });
      assert.equal(response.status(), 200, `${route}: HTTP`);
      await page.evaluate(() => document.fonts.ready);
      const layout = await page.evaluate(() => {
        const clipped = [];
        for (const selector of [".workspace", ".screen-surface", ".conversation-scroll", ".composer"]) {
          const element = document.querySelector(selector);
          if (!element) continue;
          const rect = element.getBoundingClientRect();
          if (rect.left < -1 || rect.right > innerWidth + 1) clipped.push(selector);
          if (element.scrollWidth > element.clientWidth + 1) clipped.push(`${selector}:horizontal-scroll`);
        }
        const brokenControls = [...document.querySelectorAll("button, input, select, textarea, a[href]")]
          .filter(element => {
            if (element.closest("dialog:not([open])")) return false;
            const rect = element.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0 && (rect.left < -1 || rect.right > innerWidth + 1);
          }).map(element => element.getAttribute("aria-label") || element.textContent.trim().slice(0, 60));
        const menu = document.querySelector(".menu-button");
        const rect = menu.getBoundingClientRect();
        const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
        return { clipped, brokenControls, menuReachable: menu.contains(hit), width: innerWidth };
      });
      assert.deepEqual(layout.clipped, [], `${route}@${width}: clipped content`);
      assert.deepEqual(layout.brokenControls, [], `${route}@${width}: controls outside viewport`);
      assert.equal(layout.menuReachable, true, `${route}@${width}: menu obscured`);
      await page.click(".menu-button");
      assert.equal(await page.$eval("#screen-navigation", element => element.open), true);
      const links = await page.$$eval("#screen-navigation a", items => items.map(item => new URL(item.href).pathname.split("/").pop()));
      assert.deepEqual(links, routes.map(item => `${item}.html`).sort((a, b) => {
        const order = ["index.html", "memories.html", "trace.html", "dashboard.html", "settings.html"];
        return order.indexOf(a) - order.indexOf(b);
      }));
      await page.keyboard.press("Escape");
      assert.equal(await page.$eval("#screen-navigation", element => element.open), false);
      assert.equal(await page.evaluate(() => document.activeElement.matches(".menu-button")), true);
      if (width === 375 || width === 1536) {
        await page.screenshot({ path: `${output}/${route}-${width}.png` });
      }
      report.push({ route, width, passed: true });
    }
  }
  assert.deepEqual(errors, [], "Browser runtime errors");
  await writeFile(`${output}/report.json`, JSON.stringify({ report, errors }, null, 2));
  console.log(`PASS: ${report.length} route/viewport checks; navigation, focus return, bounds and console. Screenshots: ${output}`);
} finally {
  await browser.close();
}
