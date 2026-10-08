const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");

const TEST_RUNTIME_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "llm-redactor-e2e-"));
process.env.SECRET_REDACTOR_RUNTIME_DIR = TEST_RUNTIME_DIR;

let plugin, vault;
test.before(async () => {
  ({ default: plugin } = await import("../src/secret_redactor/opencode/plugin.mjs"));
  vault = await import("../src/secret_redactor/vault.mjs");
});

test.after(async () => {
  await vault.shutdownBroker();
  fs.rmSync(TEST_RUNTIME_DIR, { recursive: true, force: true });
});

test("End-to-end lifecycle flow for OpenCode", async () => {
  const sessionID = "test-e2e-session";
  const hooks = await plugin.server({});

  // 1. User/file/tool contains sensitive value
  const sensitiveHost = "postgres://user:SecretPass999!@private-test-host.internal.example:5432/db";
  const rawApiKey = "sk-proj-secretprivatekey1234567890";

  const canonicalUserMessage = {
    info: { id: "user-msg-1", role: "user", sessionID },
    parts: [
      {
        type: "text",
        text: `Please connect to ${sensitiveHost} using ${rawApiKey}`
      }
    ]
  };

  // 2. Canonical UI state is masked before OpenCode persists or renders it.
  await hooks["chat.message"]({ sessionID }, canonicalUserMessage);
  assert.ok(!canonicalUserMessage.parts[0].text.includes(sensitiveHost));
  assert.ok(!canonicalUserMessage.parts[0].text.includes(rawApiKey));

  // Clone in-memory model context before dispatch (OpenCode behavior)
  const modelVisibleMessages = JSON.parse(JSON.stringify([canonicalUserMessage]));

  // 3. Outbound model-context masking hook runs
  await hooks["experimental.chat.messages.transform"]({}, { messages: modelVisibleMessages });

  const modelText = modelVisibleMessages[0].parts[0].text;

  // Verify: sensitive values DO NOT cross into model-visible text
  assert.ok(!modelText.includes("SecretPass999!"));
  assert.ok(!modelText.includes("secretprivatekey1234567890"));
  assert.ok(modelText.includes("__MASKED_URIPASS_"));
  assert.ok(modelText.includes("__MASKED_TOKEN_"));

  // 4. Mapping store contains the mappings
  const uriToken = modelText.match(/__MASKED_URIPASS_[A-F0-9]+__/)[0];
  const keyToken = modelText.match(/__MASKED_TOKEN_[A-F0-9]+__/)[0];

  assert.equal(await vault.unmaskText(uriToken, sessionID), "SecretPass999!");
  assert.equal(await vault.unmaskText(keyToken, sessionID), rawApiKey);

  // 5. Model responses stay redacted. Plaintext is only returned to the local
  // reveal controller after its confirmation step.
  const modelAssistantOutput = {
    text: `Connected successfully to database using credentials ${keyToken}.`
  };
  assert.ok(modelAssistantOutput.text.includes(keyToken));
  const revealed = await vault.rawBrokerRequest({
    operation: "reveal",
    session: sessionID,
    tokens: [keyToken],
  });
  assert.equal(revealed.mappings[keyToken], rawApiKey);

  // 7. Model issues a tool call referencing the masked token
  const toolExecution = {
    args: {
      url: `http://${uriToken}/health`,
      headers: { Authorization: `Bearer ${keyToken}` }
    }
  };

  // Pre-tool execution hook
  await hooks["tool.execute.before"]({ tool: "fetch", sessionID, callID: "call-99" }, toolExecution);

  // 8. Actual local tool argument receives restored original values
  assert.equal(toolExecution.args.url, "http://SecretPass999!/health");
  assert.equal(toolExecution.args.headers.Authorization, `Bearer ${rawApiKey}`);
});
