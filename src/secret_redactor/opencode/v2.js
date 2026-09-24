const { unmaskText, unmaskRecursive, maskText } = require("./vault.js");
const { maskModelMessages } = require("./transformer.js");

// TODO: Consider optional prompt-admission masking in the future.
// Do not enable it now because OpenCode's prompt hook modifies the
// canonical persisted user input.

// TODO: Consider adding a minimal system instruction asking the model
// to preserve mask tokens exactly. Keep system prompts unchanged for now.

function registerV2Hooks(ctx, maskUi = false) {
  if (!ctx) return;
  // ponytail: V2 has no chat.message or text.complete equivalent hooks.
  // maskUi accepted for API consistency; V2 assistant text stays masked regardless.

  const handleModelRequest = async (event) => {
    const sessionID = event?.sessionID || ctx?.sessionID || "default";
    if (event?.messages) {
      maskModelMessages(event.messages, sessionID);
    }
  };

  // Normal agent context requests
  if (ctx.session?.hook) {
    ctx.session.hook("context", handleModelRequest);
    ctx.session.hook("compaction", handleModelRequest);
    ctx.session.hook("generate", handleModelRequest);
    ctx.session.hook("title", handleModelRequest);
  }

  // Pre-tool execution argument unmasking
  if (ctx.tool?.hook) {
    ctx.tool.hook("execute.before", async (event) => {
      const sessionID = event?.sessionID || ctx?.sessionID || "default";
      if (event && event.args !== undefined) {
        const [unmasked, changed] = unmaskRecursive(event.args, sessionID);
        if (changed) {
          event.args = unmasked;
        }
      }
    });
  }
}

module.exports = {
  registerV2Hooks
};
