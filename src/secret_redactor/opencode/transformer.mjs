import { maskText, maskRecursive } from "./vault.mjs";

const PART_IDENTITY_KEYS = new Set(["id", "type", "sessionID", "messageID"]);

async function maskString(value, sessionID) {
  const [masked] = await maskText(value, sessionID);
  return masked;
}

async function maskValue(value, sessionID) {
  const [masked] = await maskRecursive(value, sessionID);
  return masked;
}

async function maskToolState(state, sessionID) {
  if (typeof state.output === "string") {
    state.output = await maskString(state.output, sessionID);
  }
  if (state.input) {
    state.input = await maskValue(state.input, sessionID);
  }
  for (const attachment of Array.isArray(state.attachments) ? state.attachments : []) {
    if (attachment && typeof attachment.text === "string") {
      attachment.text = await maskString(attachment.text, sessionID);
    }
  }
  // Metadata previews (e.g. display.text, preview) also carry raw content.
  if (state.metadata && typeof state.metadata === "object") {
    state.metadata = await maskValue(state.metadata, sessionID);
  }
}

async function maskPart(part, sessionID) {
  if (part.type === "tool" && part.state) {
    await maskToolState(part.state, sessionID);
  }
  // Text, reasoning, and any other string fields a part carries.
  for (const [key, value] of Object.entries(part)) {
    if (!PART_IDENTITY_KEYS.has(key) && typeof value === "string") {
      part[key] = await maskString(value, sessionID);
    }
  }
}

/**
 * Masks assembled model messages in place before they are dispatched.
 * OpenCode passes messages as { info, parts } pairs.
 */
async function maskModelMessages(messages, fallbackSessionID) {
  if (!Array.isArray(messages)) {
    return messages;
  }

  for (const msg of messages) {
    if (!msg || typeof msg !== "object" || !Array.isArray(msg.parts)) {
      continue;
    }
    const sessionID = msg.info?.sessionID || msg.sessionID || fallbackSessionID;
    if (!sessionID) {
      throw new Error("secret-redactor: model message has no session ID");
    }
    for (const part of msg.parts) {
      if (part && typeof part === "object") {
        await maskPart(part, sessionID);
      }
    }
  }

  return messages;
}

export {
  maskModelMessages
};
