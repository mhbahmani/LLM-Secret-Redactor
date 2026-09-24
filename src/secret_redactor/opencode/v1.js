const { unmaskText, unmaskRecursive, maskText } = require("./vault.js");
const { maskModelMessages } = require("./transformer.js");

function createV1Hooks(maskUi = false) {
  const hooks = {
    "experimental.chat.messages.transform": async (input, output) => {
      if (output && Array.isArray(output.messages)) {
        maskModelMessages(output.messages);
      }
    },

    "tool.execute.before": async (input, output) => {
      const sessionID = input?.sessionID || "default";
      if (output && output.args !== undefined) {
        const [unmasked, changed] = unmaskRecursive(output.args, sessionID);
        if (changed) {
          output.args = unmasked;
        }
      }
    },

    "experimental.text.complete": async (input, output) => {
      if (maskUi) return;
      const sessionID = input?.sessionID || "default";
      if (output && typeof output.text === "string") {
        output.text = unmaskText(output.text, sessionID);
      }
    },

    "experimental.session.compacting": async (input, output) => {
      const sessionID = input?.sessionID || "default";
      if (output && Array.isArray(output.context)) {
        output.context = output.context.map((c) => {
          if (typeof c === "string") {
            const [masked] = maskText(c, sessionID);
            return masked;
          }
          return c;
        });
      }
      if (output && typeof output.prompt === "string") {
        const [masked] = maskText(output.prompt, sessionID);
        output.prompt = masked;
      }
    },
  };

  if (maskUi) {
    hooks["chat.message"] = async (input, output) => {
      const sessionID = input?.sessionID || "default";
      for (const part of (output?.parts || [])) {
        if (part && typeof part === "object" && typeof part.text === "string") {
          const [masked] = maskText(part.text, sessionID);
          part.text = masked;
        }
      }
    };
  }

  return hooks;
}

module.exports = {
  createV1Hooks
};
