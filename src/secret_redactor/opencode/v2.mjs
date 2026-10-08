import { unmaskRecursive, maskText, maskRecursive, clearSession } from "./vault.mjs";
import { maskModelMessages } from "./transformer.mjs";

// TODO: Consider optional prompt-admission masking in the future.
// Do not enable it now because OpenCode's prompt hook modifies the
// canonical persisted user input.

// TODO: Consider adding a minimal system instruction asking the model
// to preserve mask tokens exactly. Keep system prompts unchanged for now.

function registerV2Hooks(ctx) {
  if (!ctx) return;

  const handleModelRequest = async (event) => {
    const sessionID = event?.sessionID || ctx?.sessionID || "default";
    if (event?.messages) {
      await maskModelMessages(event.messages, sessionID);
    }
  };

  // Normal agent context requests
  if (ctx.session?.hook) {
    ctx.session.hook("prompt", async (event) => {
      const sessionID = event?.sessionID || ctx?.sessionID || "default";
      for (const part of (event?.parts || [])) {
        if (part && typeof part.text === "string") {
          const [masked] = await maskText(part.text, sessionID);
          part.text = masked;
        }
      }
    });
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
        const [unmasked, changed] = await unmaskRecursive(event.args, sessionID);
        if (changed) {
          event.args = unmasked;
        }
      }
    });
    ctx.tool.hook("execute.after", async (event) => {
      const sessionID = event?.sessionID || ctx?.sessionID || "default";
      if (typeof event?.output === "string") {
        const [masked] = await maskText(event.output, sessionID);
        event.output = masked;
      } else if (event?.output && typeof event.output === "object") {
        const [masked] = await maskRecursive(event.output, sessionID);
        event.output = masked;
      }
    });
  }

  if (ctx.event?.subscribe) {
    ctx.event.subscribe("session.deleted", async (event) => {
      if (event?.sessionID) await clearSession(event.sessionID);
    });
  }
}

export {
  registerV2Hooks
};
