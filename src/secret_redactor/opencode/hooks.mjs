import { rawBrokerRequest, maskText, maskRecursive, clearSession } from "./vault.mjs";
import { maskModelMessages } from "./transformer.mjs";

function replaceContents(target, source) {
  for (const key of Object.keys(target)) {
    if (!(key in source)) delete target[key];
  }
  Object.assign(target, source);
}

function requireSession(input) {
  if (!input?.sessionID) {
    throw new Error("secret-redactor: hook input has no session ID");
  }
  return input.sessionID;
}

function createHooks() {
  const hooks = {
    event: async ({ event }) => {
      const sessionID = event?.type === "session.deleted" ? event.properties?.info?.id : undefined;
      if (sessionID) await clearSession(sessionID);
    },

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
      if (!output || output.args === undefined) return;
      const response = await rawBrokerRequest({
        operation: "unmask",
        session: sessionID,
        value: output.args,
        tool: input.tool,
      });
      if (!response.changed) return;
      // OpenCode server hooks cannot prompt, so "ask" refuses like "deny".
      if (response.decision !== "allow") {
        throw new Error(
          `secret-redactor: ${input.tool} may not receive real secret values. ` +
          "Set it to \"allow\" in the restore policy to permit this.",
        );
      }
      // OpenCode runs the tool with the args object it passed in, so replacing
      // output.args has no effect; the original object must be updated.
      replaceContents(output.args, response.value);
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
