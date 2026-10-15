"""Behavioral checks for the locally vendored 3D avatar contract."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "thyca" / "webui"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_3d_avatar_contract_with_local_three_runtime() -> None:
    script = r'''
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import { readFile } from "node:fs/promises";

const root = process.env.THYCA_WEBUI;
const modelUrl = pathToFileURL(`${root}/shared/js/bot-avatar-3d-model.js`);
const controllerUrl = pathToFileURL(`${root}/shared/js/bot-avatar-3d.js`);
const modelModule = await import(modelUrl);
const controllerModule = await import(controllerUrl);
assert.equal(typeof modelModule.createWoodAvatarModel, "function");
assert.equal(typeof controllerModule.createAvatar3DController, "function");
assert.equal(globalThis.THREE, undefined, "imports must not install global THREE");

const vendorPath = `${root}/vendor/three-r128.min.js`;
const require = createRequire(import.meta.url);
const THREE = require(vendorPath);
assert.equal(THREE.REVISION, "128");
const license = await readFile(`${root}/vendor/three-r128.LICENSE.txt`, "utf8");
assert.match(license, /MIT/i);

function canvasFactory(capture = null) {
  const ctx = new Proxy({}, { get: (target, key) => {
    if (key === "fillRect") return () => capture?.fills.push(target.fillStyle);
    if (key === "stroke") return () => capture?.strokes.push(target.strokeStyle);
    return target[key] ?? (() => {});
  }, set: (target, key, value) => (target[key] = value, true) });
  return { width: 0, height: 0, getContext: kind => kind === "2d" ? ctx : null };
}
const variants = ["star", "hollow", "single-flag", "double-flag", "dotted", "fermata", "plain"];
const states = ["offline", "idle", "thinking", "running", "waiting", "done", "error"];
const noteColors = {
  "note-do": "#110001", "note-re": "#220002", "note-mi": "#330003", "note-fa": "#440004",
  "note-sol": "#550005", "note-la": "#660006", "note-si": "#770007", "note-star": "#880008",
  "color-accent": "#990009",
};
const noteForVariant = {
  star: "note-do", hollow: "note-re", "single-flag": "note-mi", "double-flag": "note-fa",
  dotted: "note-sol", fermata: "note-la", plain: "note-si",
};
for (const avatar of variants) {
  const capture = { fills: [], strokes: [] };
  const model = modelModule.createWoodAvatarModel(THREE, {
    bot: { id: `palette-${avatar}`, avatar }, palette: noteColors, canvasFactory: () => canvasFactory(capture),
  });
  assert.equal(capture.fills[0], noteColors[noteForVariant[avatar]], `${avatar} must use its explicit note color`);
  model.dispose();
}
for (const [palette, expectedBase, expectedSide] of [
  [{ "note-si": "#123456", "wood-offline-base": "#ABCDEF", "wood-offline-side": "#FEDCBA" }, "#ABCDEF", "#FEDCBA"],
  [{ "note-si": "#123456" }, "#9E9080", "#5E5348"],
]) {
  const capture = { fills: [], strokes: [] };
  const model = modelModule.createWoodAvatarModel(THREE, {
    bot: { id: "offline-palette", avatar: "plain", status: "paused", agentStatus: { state: "running" } },
    palette, canvasFactory: () => canvasFactory(capture),
  });
  assert.equal(capture.fills[0], expectedBase, "offline state must use its muted base, ahead of active note colors");
  assert.equal(capture.strokes[0], expectedSide, "offline state must use its muted side color");
  model.dispose();
}
function collectResources(group) {
  const geometries = new Set();
  const materials = new Set();
  const textures = new Set();
  group.traverse(object => {
    if (object.geometry) geometries.add(object.geometry);
    const list = Array.isArray(object.material) ? object.material : object.material ? [object.material] : [];
    for (const material of list) {
      materials.add(material);
      if (material.map) textures.add(material.map);
    }
  });
  return [...geometries, ...materials, ...textures];
}
for (const avatar of variants) {
  const model = modelModule.createWoodAvatarModel(THREE, { bot: { id: `fixture-${avatar}`, avatar }, canvasFactory });
  assert.ok(model.group.children.length > 0, `${avatar} must create a nonempty group`);
  model.animate(1, { reducedMotion: true });
  const resources = collectResources(model.group);
  assert.ok(resources.length > 0, `${avatar} should own Three resources`);
  const disposed = new Map(resources.map(resource => {
    let count = 0;
    resource.addEventListener("dispose", () => count++);
    return [resource, () => count];
  }));
  model.dispose();
  for (const [resource, count] of disposed) assert.ok(count() > 0, "owned resource must emit dispose");
  assert.doesNotThrow(() => model.dispose(), "dispose should be idempotent");
}
const thyca = modelModule.createWoodAvatarModel(THREE, { bot: { id: "thyca" }, canvasFactory });
const thycaGeometryTypes = [];
thyca.group.traverse(object => { if (object.geometry) thycaGeometryTypes.push(object.geometry.type); });
assert.ok(thycaGeometryTypes.includes("OctahedronGeometry"), "Thyca's implicit star variant must retain its star geometry");
assert.ok(thycaGeometryTypes.includes("TubeGeometry"), "Thyca's implicit star variant must retain its flag tube");
thyca.dispose();
const doubleFlag = modelModule.createWoodAvatarModel(THREE, { bot: { id: "double-flag", avatar: "double-flag" }, canvasFactory });
let doubleFlagTubeCount = 0;
doubleFlag.group.traverse(object => { if (object.geometry?.type === "TubeGeometry") doubleFlagTubeCount++; });
assert.equal(doubleFlagTubeCount, 2, "double-flag must retain two flag tubes");
doubleFlag.dispose();
for (const state of states) {
  const model = modelModule.createWoodAvatarModel(THREE, { bot: { id: `state-${state}`, avatar: "plain", agentStatus: { state } }, canvasFactory });
  assert.ok(model.group.children.length > 0, `${state} must create a nonempty group`);
  model.dispose();
}
for (const state of ["running", "thinking"]) {
  const model = modelModule.createWoodAvatarModel(THREE, { bot: { id: `motion-${state}`, avatar: "plain", agentStatus: { state } }, canvasFactory });
  const { position, rotation, scale } = model.group;
  model.animate(0.25, { reducedMotion: true, gazeX: 0, gazeY: 0 });
  const first = [...position.toArray(), ...rotation.toArray(), ...scale.toArray()];
  model.animate(8.5, { reducedMotion: true, gazeX: 0, gazeY: 0 });
  const second = [...position.toArray(), ...rotation.toArray(), ...scale.toArray()];
  assert.deepEqual(second, first, `${state} group transform should not animate with reduced motion`);
  model.dispose();
}
for (let count = 0; count <= 5; count++) {
  const members = Array.from({ length: count }, (_, i) => ({ id: `member-${i}`, avatar: variants[i % variants.length] }));
  const before = JSON.stringify(members);
  const model = modelModule.createWoodAvatarModel(THREE, { bot: { id: "arbitrary team name" }, members, canvasFactory });
  assert.ok(model.group.children.length > 0);
  assert.equal(JSON.stringify(members), before, "member records must remain unchanged");
  model.dispose();
}
const arbitrary = { id: "custom-bot-77", name: "not a variant", avatar: "plain" };
const arbitraryBefore = JSON.stringify(arbitrary);
const model = modelModule.createWoodAvatarModel(THREE, { bot: arbitrary, canvasFactory });
assert.equal(JSON.stringify(arbitrary), arbitraryBefore);
model.dispose();
'''
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        env={**os.environ, "THYCA_WEBUI": str(WEBUI)},
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
