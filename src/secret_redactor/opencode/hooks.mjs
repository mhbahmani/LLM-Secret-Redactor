import { unmaskRecursive, maskText, maskRecursive } from "./vault.mjs";
import { maskModelMessages } from "./transformer.mjs";

function requireSession(input) {
  if (!input?.sessionID) {
    throw new Error("secret-redactor: hook input has no session ID");
  }
  return input.sessionID;
}

function createHooks() {
  const hooks = {
    "chat.message": async (input, output) => {
      const sessionID = requireSession(input);
      for (const part of (output?.parts || [])) {
        if (part && typeof part === "object" && typeof part.text === "string") {
          const [masked] = await maskText(part.text, sessionID);
          part.text = masked;
        }
      }
    },

    "experimental.chat.messages.transform": async (input, output) => {
      if (output && Array.isArray(output.messages)) {
        await maskModelMessages(output.messages);
      }
    },

    "tool.execute.before": async (input, output) => {
      const sessionID = requireSession(input);
      if (output && output.args !== undefined) {
        const [unmasked, changed] = await unmaskRecursive(output.args, sessionID);
        if (changed) {
          output.args = unmasked;
        }
      }
    },

    "tool.execute.after": async (input, output) => {
      const sessionID = requireSession(input);
      if (output && typeof output.output === "string") {
        const [masked] = await maskText(output.output, sessionID);
        output.output = masked;
      }
      if (output?.metadata && typeof output.metadata === "object") {
        const [masked] = await maskRecursive(output.metadata, sessionID);
        output.metadata = masked;
      }
    },

    "experimental.session.compacting": async (input, output) => {
      const sessionID = requireSession(input);
      if (output && Array.isArray(output.context)) {
        output.context = await Promise.all(output.context.map(async (c) => {
          if (typeof c === "string") {
            const [masked] = await maskText(c, sessionID);
            return masked;
          }
          return c;
        }));
      }
      if (output && typeof output.prompt === "string") {
        const [masked] = await maskText(output.prompt, sessionID);
        output.prompt = masked;
      }
    },
  };

  return hooks;
}

export {
  createHooks
};
