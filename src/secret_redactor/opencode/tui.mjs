import { createRevealController } from "./ui-reveal.mjs";
import { toggleMasking } from "./masking-toggle.mjs";
import * as vault from "./vault.mjs";

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
        finish(true);
        api.ui.dialog.clear();
      },
      onCancel: () => {
        finish(false);
        api.ui.dialog.clear();
      },
    }), () => finish(false));
  });
}

function confirmMaskingOff(api) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    api.ui.dialog.replace(() => api.ui.DialogConfirm({
      title: "Turn off secret masking?",
      message: "Messages and tool output in this session will reach the model unmasked, "
        + "and OpenCode will save them unmasked. Turn it back on with the same command.",
      onConfirm: () => {
        finish(true);
        api.ui.dialog.clear();
      },
      onCancel: () => {
        finish(false);
        api.ui.dialog.clear();
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
  // No default shortcut for switching masking off; it lives in the palette
  // unless the user binds it in tui.json.
  const maskingBindings = api.tuiConfig?.keybinds?.get?.("secret-redactor.masking.toggle") || [];

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
      {
        name: "secret-redactor.masking.toggle",
        title: "Turn secret masking off or on for this session",
        category: "Secret Redactor",
        namespace: "palette",
        suggested: true,
        run: async () => {
          const sessionID = currentSessionID(api);
          if (!sessionID) {
            api.ui.toast({ message: "Open a session before changing secret masking", variant: "warning" });
            return;
          }
          try {
            await toggleMasking({
              sessionID,
              status: vault.maskingEnabled,
              setMasking: vault.setMasking,
              confirm: () => confirmMaskingOff(api),
              notify: (message, variant) => api.ui.toast({ message, variant }),
            });
          } catch (error) {
            api.ui.toast({ message: `Secret masking unchanged: ${error.message}`, variant: "error" });
          }
        },
      },
    ],
    bindings: [
      ...(configuredBindings.length ? configuredBindings : [{
        key: "ctrl+shift+r",
        cmd: "secret-redactor.ui.toggle",
        desc: "Reveal or hide secrets",
      }]),
      ...maskingBindings,
    ],
  });

  api.lifecycle.onDispose(() => controller.hide());
};

export default {
  id: "secret-redactor.ui",
  tui,
};
