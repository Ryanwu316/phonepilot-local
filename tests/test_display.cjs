const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

// Load the actual console with an inert DOM: no browser, network or real device.
function consoleFixture() {
  const elements = new Map();
  const node = (id) => {
    if (!elements.has(id))
      elements.set(id, {
        style: {
          setProperty(name, value) {
            this[name] = value;
          },
        },
        dataset: {},
        value: "",
        width: 1,
        height: 1,
        clientWidth: 500,
        clientHeight: 600,
        firstChild: {},
        classList: { toggle() {}, add() {}, remove() {} },
        getContext() {
          return { drawImage() {} };
        },
        addEventListener() {},
        setAttribute() {},
        replaceChildren() {},
        append() {},
        getBoundingClientRect() {
          return this.rect;
        },
      });
    return elements.get(id);
  };
  const sandbox = vm.createContext({
    document: {
      getElementById: node,
      documentElement: node("root"),
      body: node("body"),
      querySelector: node,
      querySelectorAll: () => [],
      createElement: () => node(Symbol("element")),
    },
    window: { addEventListener() {} },
    localStorage: {
      getItem() {
        return null;
      },
    },
    getComputedStyle: (element) =>
      element === node("phone")
        ? {
            paddingLeft: "4px",
            paddingRight: "4px",
            paddingTop: "4px",
            paddingBottom: "4px",
            borderLeftWidth: "2px",
            borderRightWidth: "2px",
            borderTopWidth: "2px",
            borderBottomWidth: "2px",
          }
        : { paddingLeft: "20px", paddingRight: "20px", paddingTop: "10px", paddingBottom: "10px" },
    ResizeObserver: class {
      observe() {}
    },
    setInterval() {},
    clearInterval() {},
    setTimeout() {},
    clearTimeout() {},
    fetch: () => new Promise(() => {}),
    AbortController,
    AbortSignal,
  });
  vm.runInContext(
    fs.readFileSync(path.join(__dirname, "../phonepilot/static/app.js"), "utf8"),
    sandbox,
  );
  vm.runInContext('status = {phase:"connected"}', sandbox);
  return { sandbox, node, run: (code) => vm.runInContext(code, sandbox) };
}

for (const [width, height] of [
  [1320, 2868],
  [1328, 2896],
  [1170, 2532],
  [2532, 1170],
  [2048, 1536],
  [1000, 1000],
]) {
  test(`full frame ${width}x${height} fits both narrow and wide containers`, () => {
    const { sandbox } = consoleFixture();
    for (const [maxWidth, maxHeight] of [
      [280, 450],
      [500, 600],
      [900, 340],
      [180, 120],
    ]) {
      const fit = sandbox.fitDisplay(width, height, maxWidth, maxHeight);
      assert.ok(fit.width <= maxWidth + 1e-8 && fit.height <= maxHeight + 1e-8);
      assert.ok(Math.abs(fit.width / fit.height - width / height) < 1e-10);
      assert.ok(Math.abs(fit.width - maxWidth) < 1e-8 || Math.abs(fit.height - maxHeight) < 1e-8);
    }
  });
}

test("invalid or temporarily collapsed areas never produce invalid styles", () => {
  const { sandbox } = consoleFixture();
  for (const values of [
    [0, 1, 200, 300],
    [1, -1, 200, 300],
    [1, 1, 0, 300],
    [1, 1, 200, NaN],
    [Infinity, 1, 200, 300],
  ]) {
    assert.equal(sandbox.fitDisplay(...values), null);
  }
});

test("rotation and sidebar resizing refit the displayed frame and cancel an old gesture", () => {
  const { sandbox, node, run } = consoleFixture();
  run("gesture = {x:.5,y:.5}; drawFrame({}, 1320, 2868)");
  assert.equal(run("gesture"), null);
  let w = parseFloat(node("phone").style.width),
    h = parseFloat(node("phone").style.height);
  assert.ok(Math.abs(w / h - 1320 / 2868) < 1e-10);
  sandbox.drawFrame({}, 2868, 1320);
  w = parseFloat(node("phone").style.width);
  h = parseFloat(node("phone").style.height);
  assert.equal(w, 448); // The frame's 12px stay outside the 448px image.
  assert.ok(Math.abs(w / h - 2868 / 1320) < 1e-10);
  assert.equal(node("phone").dataset.orientation, "landscape");
  assert.ok(parseFloat(node("phone").style["--screen-radius"]) > 0);
  node("phone-area").clientWidth = 220;
  sandbox.layoutDisplay();
  assert.ok(parseFloat(node("phone").style.width) <= 178);
  assert.deepEqual([node("screen").width, node("screen").height], [2868, 1320]);
});

test("pointer mapping remains correct after rescaling and rotation", () => {
  const { sandbox, node } = consoleFixture();
  const canvas = node("screen");
  for (const [w, h, rw, rh] of [
    [1320, 2868, 220, 478],
    [2868, 1320, 440, 202.5],
    [2048, 1536, 400, 300],
  ]) {
    canvas.width = w;
    canvas.height = h;
    canvas.rect = { left: 20, top: 40, width: rw, height: rh };
    for (const [x, y] of [
      [0.1, 0.2],
      [0.5, 0.5],
      [0.9, 0.8],
    ]) {
      const scale = Math.min(rw / w, rh / h);
      const result = sandbox.point({
        clientX: 20 + (rw - w * scale) / 2 + x * w * scale,
        clientY: 40 + (rh - h * scale) / 2 + y * h * scale,
      });
      assert.ok(Math.abs(result.x - x) < 1e-9 && Math.abs(result.y - y) < 1e-9);
    }
    assert.equal(sandbox.point({ clientX: 19, clientY: 40 }), null);
  }
});

test("disconnect clears dimensions and the empty state uses a neutral panel", () => {
  const { sandbox, node, run } = consoleFixture();
  sandbox.drawFrame({}, 2048, 1536);
  sandbox.stopPicture();
  assert.equal(run("displaySize"), null);
  assert.deepEqual([node("screen").width, node("screen").height], [1, 1]);
  assert.equal(node("display-info").textContent, "连接后自动适配画面比例");
});

test("a recovered live picture replaces an obsolete unavailable caption", () => {
  const { sandbox, node, run } = consoleFixture();
  run('status = {phase:"connected"}; mode = "live"; frameProblem = true; syncView()');
  assert.match(node("stream-caption").textContent, /不可用/);
  sandbox.drawFrame({}, 1320, 2868);
  assert.match(node("stream-caption").textContent, /实时画面.*原始比例/);
});

test("connection recovery immediately hides obsolete frames and disables input", () => {
  const { sandbox, node, run } = consoleFixture();
  sandbox.drawFrame({}, 1320, 2868);
  run(
    'render({phase:"reconnecting",device:{id:"one",name:"iPhone"},recovery_attempt:1,recovery_stage:"waiting_device"})',
  );
  assert.equal(sandbox.canControl(), false);
  assert.equal(run("lastFrame"), 0);
  assert.match(node("status-label").textContent, /恢复/);
  assert.match(node("empty-description").textContent, /不会自动重试/);
  assert.equal(node("devices").disabled, true);
});

test("a new session while still connected also discards the previous picture", () => {
  const { sandbox, run } = consoleFixture();
  run('status = {phase:"connected",session_epoch:1}');
  sandbox.drawFrame({}, 1320, 2868);
  run('render({phase:"connected",session_epoch:2,events:[]})');
  assert.equal(run("lastFrame"), 0);
  assert.equal(sandbox.canControl(), false);
});

test("a safe status GET reauthorizes after server restart", async () => {
  const { sandbox } = consoleFixture();
  const paths = [];
  sandbox.fetch = async (path) => {
    paths.push(path);
    return { status: paths.length === 1 ? 401 : 200, ok: paths.length !== 1 };
  };
  assert.equal((await sandbox.api("/api/status")).status, 200);
  assert.deepEqual(paths, ["/api/status", "/", "/api/status"]);
});

test("an input POST is never retried after authorization loss", async () => {
  const { sandbox } = consoleFixture();
  const paths = [];
  sandbox.fetch = async (path) => {
    paths.push(path);
    return { status: 401, ok: false, json: async () => ({ detail: "需要重新观察" }) };
  };
  await assert.rejects(sandbox.api("/api/action", { kind: "type", text: "private" }), /重新观察/);
  assert.deepEqual(paths, ["/api/action"]);
});

test("wireless selection is retained before connection and live transport is authoritative", () => {
  const { node, run } = consoleFixture();
  run('connectionMethod = "wifi"; render({phase:"disconnected",transport:"usb",events:[]})');
  assert.equal(run("connectionMethod"), "wifi");
  assert.match(node("transport-hint").textContent, /同一 Wi-Fi/);
  run('render({phase:"connecting",transport:"usb",events:[]})');
  assert.equal(run("connectionMethod"), "usb");
  assert.match(node("transport-hint").textContent, /数据线/);
});
