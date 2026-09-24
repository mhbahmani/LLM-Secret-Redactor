const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");

const TEST_VAULT_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "llm-redactor-e2e-"));
process.env.VAULT_DIR = TEST_VAULT_DIR;

const plugin = require("../src/secret_redactor/opencode/index.js");
const vault = require("../src/secret_redactor/vault.js");

test.after(() => fs.rmSync(TEST_VAULT_DIR, { recursive: true, force: true }));

test("End-to-end lifecycle flow for OpenCode", async () => {
  const sessionID = "test-e2e-session";
  const hooks = await plugin({});

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

  // 2. OpenCode local state retains the original sensitive values
  assert.ok(canonicalUserMessage.parts[0].text.includes(sensitiveHost));
  assert.ok(canonicalUserMessage.parts[0].text.includes(rawApiKey));

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
  const vaultData = vault.loadVault(sessionID);
  const uriToken = Object.keys(vaultData).find(k => k.includes("URIPASS"));
  const keyToken = Object.keys(vaultData).find(k => k.includes("TOKEN"));

  assert.equal(vaultData[uriToken], "SecretPass999!");
  assert.equal(vaultData[keyToken], rawApiKey);

  // 5. Model responds referencing the masked tokens
  const modelAssistantOutput = {
    text: `Connected successfully to database using credentials ${keyToken}.`
  };

  // Model response restoration hook
  await hooks["experimental.text.complete"]({ sessionID }, modelAssistantOutput);

  // 6. User/terminal sees original sensitive values restored
  assert.ok(!modelAssistantOutput.text.includes(keyToken));
  assert.ok(modelAssistantOutput.text.includes(rawApiKey));

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
