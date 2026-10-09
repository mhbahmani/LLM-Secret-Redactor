import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { createRevealController } = require("./ui-reveal.js");

let vault;
try {
  vault = require("./vault.js");
} catch {
  vault = require("../vault.js");
}

function currentSessionID(context) {
  const route = context.route.current;
  return route?.name === "session" ? route.params?.sessionID : undefined;
}

function confirmDialog(api, selected, total) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    api.ui.dialog.replace(() => api.ui.DialogConfirm({
      title: "Reveal secrets?",
      message: selected === total
        ? `Reveal all ${total} session secrets for 10 seconds?`
        : `Reveal ${selected} secret${selected === 1 ? "" : "s"} from the selected text for 10 seconds?`,
      onConfirm: () => {
        api.ui.dialog.clear();
        finish(true);
      },
      onCancel: () => {
        api.ui.dialog.clear();
        finish(false);
      },
    }), () => finish(false));
  });
}

function showRevealDialog(api, mappings, state) {
  if (!mappings) {
    if (state.open) api.ui.dialog.clear();
    state.open = false;
    return;
  }
  const message = Object.entries(mappings)
    .map(([token, secret]) => `${token}\n${secret}`)
    .join("\n\n");
  state.open = true;
  api.ui.dialog.replace(() => api.ui.DialogAlert({
    title: "Secrets visible for 10 seconds",
    message,
    onConfirm: () => {
      state.open = false;
      api.ui.dialog.clear();
    },
  }), () => { state.open = false; });
}

const tui = async (api) => {
  const controller = createRevealController({ request: vault.rawBrokerRequest, timeoutMs: 10_000 });
  const revealDialog = { open: false };
  const configuredBindings = api.tuiConfig?.keybinds?.get?.("secret-redactor.ui.toggle") || [];

  api.keymap.registerLayer({
    commands: [
      {
        name: "secret-redactor.ui.toggle",
        title: "Reveal or hide session secrets",
        category: "Secret Redactor",
        namespace: "palette",
        suggested: true,
        run: async () => {
          const sessionID = currentSessionID(api);
          if (!sessionID) {
            api.ui.toast({ message: "Open a session before revealing secrets", variant: "warning" });
            return;
          }

          const selectedText = api.renderer.getSelection()?.getSelectedText() || "";
          await controller.toggle({
            sessionID,
            selectedText,
            confirm: (selected, total) => confirmDialog(api, selected, total),
            show: (mappings) => showRevealDialog(api, mappings, revealDialog),
            onEmpty: () => api.ui.toast({ message: "No secrets in this session", variant: "info" }),
            onSelectionEmpty: () => api.ui.toast({
              message: "The selected text contains no redacted secrets",
              variant: "info",
            }),
          });
        },
      },
    ],
    bindings: configuredBindings.length ? configuredBindings : [{
        key: "ctrl+shift+r",
        cmd: "secret-redactor.ui.toggle",
        desc: "Reveal or hide secrets",
      }],
  });

  api.lifecycle.onDispose(() => controller.hide());
};

export default {
  id: "secret-redactor.ui",
  tui,
};
