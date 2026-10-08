import { maskText, maskRecursive } from "./vault.mjs";

/**
 * Recursively masks sensitive content inside assembled model messages.
 * Supports both OpenCode V1 ({ info, parts }) and V2 ({ role, content }) formats.
 *
 * @param {Array<any>} messages - Assembled messages to mask before dispatching to model.
 * @param {string} [fallbackSessionID="default"] - Default session ID if not present on message.
 * @returns {Array<any>} The masked messages (modified in-place).
 */
async function maskModelMessages(messages, fallbackSessionID = "default") {
  if (!Array.isArray(messages)) {
    return messages;
  }

  for (const msg of messages) {
    if (!msg || typeof msg !== "object") {
      continue;
    }

    const sessionID =
      msg.info?.sessionID ||
      msg.sessionID ||
      fallbackSessionID;

    // OpenCode V1 structure: msg.parts is an array of Part objects
    if (Array.isArray(msg.parts)) {
      for (const part of msg.parts) {
        if (!part || typeof part !== "object") {
          continue;
        }

        // Text & reasoning parts
        if ((part.type === "text" || part.type === "reasoning") && typeof part.text === "string") {
          const [masked] = await maskText(part.text, sessionID);
          part.text = masked;
        }

        // Tool execution parts (previous tool results, file reads, bash outputs)
        if (part.type === "tool" && part.state) {
          if (typeof part.state.output === "string") {
            const [masked] = await maskText(part.state.output, sessionID);
            part.state.output = masked;
          }
          if (part.state.input) {
            const [masked] = await maskRecursive(part.state.input, sessionID);
            part.state.input = masked;
          }
          if (Array.isArray(part.state.attachments)) {
            for (const att of part.state.attachments) {
              if (att && typeof att.text === "string") {
                const [masked] = await maskText(att.text, sessionID);
                att.text = masked;
              }
            }
          }
          // Metadata previews (e.g. display.text, preview) also carry raw content
          if (part.state.metadata && typeof part.state.metadata === "object") {
            const [maskedMetadata] = await maskRecursive(part.state.metadata, sessionID);
            part.state.metadata = maskedMetadata;
          }
        }

        // Generic fallback for any other string properties on a part
        for (const [key, value] of Object.entries(part)) {
          if (key !== "id" && key !== "type" && key !== "sessionID" && key !== "messageID" && typeof value === "string") {
            const [masked] = await maskText(value, sessionID);
            part[key] = masked;
          }
        }
      }
    }

    // OpenCode V2 / Standard model message structure: msg.content
    if (typeof msg.content === "string") {
      const [masked] = await maskText(msg.content, sessionID);
      msg.content = masked;
    } else if (Array.isArray(msg.content)) {
      for (let i = 0; i < msg.content.length; i++) {
        const item = msg.content[i];
        if (typeof item === "string") {
          const [masked] = await maskText(item, sessionID);
          msg.content[i] = masked;
        } else if (item && typeof item === "object") {
          if (item.type === "text" && typeof item.text === "string") {
            const [masked] = await maskText(item.text, sessionID);
            item.text = masked;
          } else if (item.type === "tool-call" && item.args) {
            const [masked] = await maskRecursive(item.args, sessionID);
            item.args = masked;
          } else if (item.type === "tool-result") {
            if (typeof item.result === "string") {
              const [masked] = await maskText(item.result, sessionID);
              item.result = masked;
            } else if (item.result) {
              const [masked] = await maskRecursive(item.result, sessionID);
              item.result = masked;
            }
            if (typeof item.content === "string") {
              const [masked] = await maskText(item.content, sessionID);
              item.content = masked;
            }
          }
        }
      }
    }
  }

  return messages;
}

export {
  maskModelMessages
};
