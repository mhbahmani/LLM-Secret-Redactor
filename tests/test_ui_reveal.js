const test = require("node:test");
const assert = require("node:assert/strict");
const { pathToFileURL } = require("node:url");
const path = require("node:path");

const { createRevealController } = require("../src/secret_redactor/opencode/ui-reveal.js");

const TOKEN_A = "__MASKED_TOKEN_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA__";
const TOKEN_B = "__MASKED_SECRET_BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB__";

function harness() {
  let timer;
  let cleared = false;
  const shown = [];
  const requests = [];
  const mappings = { [TOKEN_A]: "secret-a", [TOKEN_B]: "secret-b" };
  const controller = createRevealController({
    request: async (request) => {
      requests.push(request);
      if (request.operation === "tokens") return { tokens: [TOKEN_A, TOKEN_B] };
      if (request.operation === "reveal") {
        return {
          mappings: Object.fromEntries(request.tokens.map((token) => [token, mappings[token]])),
        };
      }
      throw new Error(`Unexpected operation ${request.operation}`);
    },
    setTimer: (callback, milliseconds) => {
      timer = { callback, milliseconds };
      return timer;
    },
    clearTimer: () => { cleared = true; },
  });
  return { controller, requests, shown, getTimer: () => timer, wasCleared: () => cleared };
}

test("reveal requires confirmation and reveals all when nothing is selected", async () => {
  const state = harness();
  const cancelled = await state.controller.toggle({
    sessionID: "session-a",
    selectedText: "",
    confirm: async () => false,
    show: (value) => state.shown.push(value),
  });
  assert.equal(cancelled.status, "cancelled");
  assert.equal(state.requests.some((request) => request.operation === "reveal"), false);

  const revealed = await state.controller.toggle({
    sessionID: "session-a",
    selectedText: "",
    confirm: async (selected, total) => {
      assert.equal(selected, 2);
      assert.equal(total, 2);
      return true;
    },
    show: (value) => state.shown.push(value),
  });
  assert.deepEqual(revealed, { status: "revealed", count: 2 });
  assert.deepEqual(state.shown.at(-1), { [TOKEN_A]: "secret-a", [TOKEN_B]: "secret-b" });
  assert.equal(state.getTimer().milliseconds, 10_000);
});

test("selection reveals only tokens present in selected text", async () => {
  const state = harness();
  await state.controller.toggle({
    sessionID: "session-b",
    selectedText: `only ${TOKEN_B} is selected`,
    confirm: async (selected, total) => {
      assert.equal(selected, 1);
      assert.equal(total, 2);
      return true;
    },
    show: (value) => state.shown.push(value),
  });
  const revealRequest = state.requests.find((request) => request.operation === "reveal");
  assert.deepEqual(revealRequest.tokens, [TOKEN_B]);
  assert.deepEqual(state.shown.at(-1), { [TOKEN_B]: "secret-b" });
});

test("selection without a mask does not fall back to revealing everything", async () => {
  const state = harness();
  let notified = false;
  const result = await state.controller.toggle({
    sessionID: "session-c",
    selectedText: "ordinary selected text",
    confirm: async () => true,
    show: (value) => state.shown.push(value),
    onSelectionEmpty: () => { notified = true; },
  });
  assert.equal(result.status, "selection-empty");
  assert.equal(notified, true);
  assert.equal(state.requests.some((request) => request.operation === "reveal"), false);
});

test("revealed secrets hide on second toggle and after ten seconds", async () => {
  const state = harness();
  const options = {
    sessionID: "session-d",
    selectedText: "",
    confirm: async () => true,
    show: (value) => state.shown.push(value),
  };
  await state.controller.toggle(options);
  assert.equal(state.controller.isVisible(), true);

  const hidden = await state.controller.toggle(options);
  assert.equal(hidden.status, "hidden");
  assert.equal(state.controller.isVisible(), false);
  assert.equal(state.shown.at(-1), null);
  assert.equal(state.wasCleared(), true);

  await state.controller.toggle(options);
  state.getTimer().callback();
  assert.equal(state.controller.isVisible(), false);
  assert.equal(state.shown.at(-1), null);
});

test("TUI module registers the user-configured command binding", async () => {
  const modulePath = path.resolve(__dirname, "../src/secret_redactor/opencode/tui.mjs");
  const { default: plugin } = await import(pathToFileURL(modulePath));
  let layer;
  let disposed;
  const customBinding = {
    key: "ctrl+alt+r",
    cmd: "secret-redactor.ui.toggle",
    desc: "custom reveal",
  };
  const api = {
    keymap: { registerLayer: (value) => { layer = value; } },
    tuiConfig: {
      keybinds: {
        get: (command) => command === "secret-redactor.ui.toggle" ? [customBinding] : [],
      },
    },
    lifecycle: { onDispose: (callback) => { disposed = callback; } },
  };

  await plugin.tui(api);
  assert.equal(plugin.id, "secret-redactor.ui");
  assert.deepEqual(layer.bindings, [customBinding]);
  assert.equal(layer.commands[0].name, "secret-redactor.ui.toggle");
  assert.equal(typeof disposed, "function");
});
