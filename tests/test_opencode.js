const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");

const TEST_VAULT_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "llm-redactor-opencode-"));
process.env.VAULT_DIR = TEST_VAULT_DIR;

const vault = require("../src/secret_redactor/vault.js");
const plugin = require("../src/secret_redactor/opencode/index.js");
const { maskModelMessages } = require("../src/secret_redactor/opencode/transformer.js");
const { createV1Hooks } = require("../src/secret_redactor/opencode/v1.js");
const { registerV2Hooks } = require("../src/secret_redactor/opencode/v2.js");

function cleanTestVaults() {
  try {
    if (fs.existsSync(TEST_VAULT_DIR)) {
      const files = fs.readdirSync(TEST_VAULT_DIR);
      for (const f of files) {
        fs.unlinkSync(path.join(TEST_VAULT_DIR, f));
      }
    }
  } catch {
    // Ignore
  }
}

test.beforeEach(cleanTestVaults);
test.afterEach(cleanTestVaults);
test.after(() => fs.rmSync(TEST_VAULT_DIR, { recursive: true, force: true }));

test("Outbound message masking: original -> mask before model request", async () => {
  const sessionID = "test-outbound";
  const hooks = await plugin({});

  const originalText = "Connecting with key sk-proj-1234567890abcdef1234567890 to backend.";
  const messages = [
    {
      info: { id: "msg-1", role: "user", sessionID },
      parts: [
        { type: "text", text: originalText }
      ]
    }
  ];

  await hooks["experimental.chat.messages.transform"]({}, { messages });

  assert.ok(!messages[0].parts[0].text.includes("sk-proj-1234567890abcdef1234567890"));
  assert.ok(messages[0].parts[0].text.includes("__MASKED_TOKEN_"));
});

test("File and tool-result masking in model context", async () => {
  const sessionID = "test-tool-result";
  const hooks = await plugin({});

  const secretUri = "postgres://admin:SuperSecretPass123!@localhost:5432/mydb";
  const secretGh = "ghp_123456789012345678901234567890123456";

  const messages = [
    {
      info: { id: "msg-2", role: "assistant", sessionID },
      parts: [
        {
          type: "tool",
          tool: "Bash",
          state: {
            status: "completed",
            output: `Config output:\nDB_URL=${secretUri}\nTOKEN=${secretGh}`,
            input: { command: "cat .env" }
          }
        }
      ]
    }
  ];

  await hooks["experimental.chat.messages.transform"]({}, { messages });

  const output = messages[0].parts[0].state.output;
  assert.ok(!output.includes("SuperSecretPass123!"));
  assert.ok(!output.includes("ghp_123456789012345678901234567890123456"));
  assert.ok(output.includes("__MASKED_URIPASS_"));
  assert.ok(output.includes("__MASKED_TOKEN_"));
});

test("User content in model context vs canonical input", async () => {
  const sessionID = "test-user-context";
  const hooks = await plugin({});

  // Canonical user input retained locally
  const canonicalUserInput = "Database credentials: password='SuperSecretPassword123'";

  // Assembled model message
  const assembledMessage = {
    info: { id: "msg-3", role: "user", sessionID },
    parts: [{ type: "text", text: canonicalUserInput }]
  };

  await hooks["experimental.chat.messages.transform"]({}, { messages: [assembledMessage] });

  // Assembled message is masked
  assert.ok(!assembledMessage.parts[0].text.includes("SuperSecretPassword123"));
  assert.ok(assembledMessage.parts[0].text.includes("__MASKED_SECRET_"));

  // Canonical original string is untouched
  assert.ok(canonicalUserInput.includes("SuperSecretPassword123"));
});

test("Multiple patterns in a single request", async () => {
  const sessionID = "test-multi-patterns";
  const hooks = await plugin({});

  const multiText = [
    "OpenAI: sk-proj-1234567890abcdef1234567890",
    "GitHub: ghp_123456789012345678901234567890123456",
    "Anthropic: sk-ant-1234567890abcdef1234567890",
    "AWS: AKIAIOSFODNN7EXAMPLE",
    "Bearer: Bearer testsecrettoken1234567890123456",
    "URI: postgres://usr:SecretPass1@host:5432/db",
    "KV: api_key='SecretApiKey123'",
    "JWT: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
  ].join("\n");

  const messages = [
    {
      info: { id: "msg-4", role: "user", sessionID },
      parts: [{ type: "text", text: multiText }]
    }
  ];

  await hooks["experimental.chat.messages.transform"]({}, { messages });
  const masked = messages[0].parts[0].text;

  assert.ok(masked.includes("__MASKED_TOKEN_"));
  assert.ok(masked.includes("__MASKED_AWS_KEY_"));
  assert.ok(masked.includes("__MASKED_BEARER_"));
  assert.ok(masked.includes("__MASKED_URIPASS_"));
  assert.ok(masked.includes("__MASKED_SECRET_"));
  assert.ok(masked.includes("__MASKED_JWT_TOKEN_"));
});

test("Repeated values receive deterministic identical mask token", async () => {
  const sessionID = "test-repeated";
  const hooks = await plugin({});

  const text = "First: sk-proj-1234567890abcdef1234567890 and Second: sk-proj-1234567890abcdef1234567890";
  const messages = [
    {
      info: { id: "msg-5", role: "user", sessionID },
      parts: [{ type: "text", text }]
    }
  ];

  await hooks["experimental.chat.messages.transform"]({}, { messages });
  const masked = messages[0].parts[0].text;

  const matches = masked.match(/__MASKED_TOKEN_[A-F0-9]+__/g);
  assert.equal(matches.length, 2);
  assert.equal(matches[0], matches[1]);
});

test("Assistant response restoration: mask token -> real secret", async () => {
  const sessionID = "test-response-restore";
  const hooks = await plugin({});

  // Mask a secret first to populate vault
  const [_, v] = vault.maskText("secret token ghp_123456789012345678901234567890123456", sessionID);
  const token = Object.keys(v)[0];
  assert.ok(token);

  // Model returns the token
  const modelResponse = { text: `Found token ${token}. Authentication succeeded.` };
  await hooks["experimental.text.complete"]({ sessionID }, modelResponse);

  assert.ok(!modelResponse.text.includes(token));
  assert.ok(modelResponse.text.includes("ghp_123456789012345678901234567890123456"));
});

test("Unknown mask tokens remain untouched (exact match only)", async () => {
  const sessionID = "test-unknown-token";
  const hooks = await plugin({});

  const unknownToken = "__MASKED_UNKNOWN_99999999__";
  const modelResponse = { text: `Connecting with ${unknownToken}` };
  await hooks["experimental.text.complete"]({ sessionID }, modelResponse);

  // Token remains untouched because it is not in vault
  assert.equal(modelResponse.text, `Connecting with ${unknownToken}`);
});

test("Corrupt vault state fails closed instead of losing restoration data", () => {
  const sessionID = "test-corrupt-vault";
  fs.writeFileSync(vault.getVaultPath(sessionID), "not-json", "utf-8");

  assert.throws(
    () => vault.maskText("key sk-proj-1234567890abcdef1234567890", sessionID),
    /Unable to read secret vault/
  );
});

test("Vault directory and files are private to the current user", () => {
  const sessionID = "test-private-vault";
  vault.maskText("key sk-proj-1234567890abcdef1234567890", sessionID);

  assert.equal(fs.statSync(TEST_VAULT_DIR).mode & 0o777, 0o700);
  assert.equal(fs.statSync(vault.getVaultPath(sessionID)).mode & 0o777, 0o600);
});

test("Tool argument restoration before local execution", async () => {
  const sessionID = "test-tool-arg";
  const hooks = await plugin({});

  // Populate vault
  const [_, v] = vault.maskText("token ghp_123456789012345678901234567890123456", sessionID);
  const token = Object.keys(v)[0];

  const toolInput = {
    args: {
      command: `curl -H 'Authorization: ${token}' https://api.github.com`
    }
  };

  await hooks["tool.execute.before"]({ tool: "Bash", sessionID, callID: "c-1" }, toolInput);

  assert.ok(!toolInput.args.command.includes(token));
  assert.ok(toolInput.args.command.includes("ghp_123456789012345678901234567890123456"));
});

test("Nested tool arguments (objects and arrays) restoration", async () => {
  const sessionID = "test-nested-args";
  const hooks = await plugin({});

  const [, v1] = vault.maskText("url postgres://admin:Pass123!@localhost/db", sessionID);
  const token1 = Object.keys(v1).find(k => k.includes("URIPASS"));

  const [, v2] = vault.maskText("api_key='SuperSecret456'", sessionID);
  const token2 = Object.keys(v2).find(k => k.includes("SECRET"));

  const nestedArgs = {
    args: {
      dbConfig: {
        primary: `connection://${token1}`,
        replicas: [
          { uri: `replica1://${token1}` },
          { secretKey: token2 }
        ]
      },
      tags: ["tag1", token2]
    }
  };

  await hooks["tool.execute.before"]({ tool: "custom_db", sessionID, callID: "c-2" }, nestedArgs);

  assert.ok(nestedArgs.args.dbConfig.primary.includes("Pass123!"));
  assert.ok(nestedArgs.args.dbConfig.replicas[0].uri.includes("Pass123!"));
  assert.equal(nestedArgs.args.dbConfig.replicas[1].secretKey, "SuperSecret456");
  assert.equal(nestedArgs.args.tags[1], "SuperSecret456");
});

test("Session isolation: sessions do not cross-unmask tokens", async () => {
  const sessionA = "test-session-a";
  const sessionB = "test-session-b";
  const hooks = await plugin({});

  // Secret in session A
  const [_, vaultA] = vault.maskText("key sk-proj-1111111111111111111111111111", sessionA);
  const tokenA = Object.keys(vaultA)[0];

  // Try unmasking in session B
  const toolInputB = {
    args: { key: tokenA }
  };

  await hooks["tool.execute.before"]({ tool: "test", sessionID: sessionB, callID: "c-3" }, toolInputB);

  // Session B does not have tokenA in its vault, so it remains unchanged
  assert.equal(toolInputB.args.key, tokenA);
});

test("OpenCode V2 hook registration and execution", async () => {
  const sessionID = "test-v2";
  const sessionHooks = {};
  const toolHooks = {};

  const mockCtx = {
    sessionID,
    session: {
      hook: (name, fn) => { sessionHooks[name] = fn; }
    },
    tool: {
      hook: (name, fn) => { toolHooks[name] = fn; }
    }
  };

  await plugin(mockCtx);

  assert.ok(typeof sessionHooks["context"] === "function");
  assert.ok(typeof sessionHooks["compaction"] === "function");
  assert.ok(typeof sessionHooks["generate"] === "function");
  assert.ok(typeof sessionHooks["title"] === "function");
  assert.ok(typeof toolHooks["execute.before"] === "function");

  // Test V2 context masking
  const v2Event = {
    sessionID,
    messages: [
      {
        role: "user",
        content: "API_KEY=sk-proj-1234567890abcdef1234567890"
      }
    ]
  };

  await sessionHooks["context"](v2Event);
  assert.ok(!v2Event.messages[0].content.includes("sk-proj-1234567890abcdef1234567890"));
  assert.ok(v2Event.messages[0].content.includes("__MASKED_TOKEN_"));

  // Test V2 tool argument unmasking
  const token = v2Event.messages[0].content.match(/__MASKED_[A-Z_0-9]+__/)[0];
  const toolEvent = {
    sessionID,
    args: { key: token }
  };
  await toolHooks["execute.before"](toolEvent);
  assert.equal(toolEvent.args.key, "sk-proj-1234567890abcdef1234567890");
});

test("maskUi=false (default): chat.message not registered, text.complete unmasks", async () => {
  const sessionID = "test-maskui-false";
  const hooks = createV1Hooks(false);

  assert.equal(hooks["chat.message"], undefined);
  assert.equal(typeof hooks["experimental.text.complete"], "function");

  const [, v] = vault.maskText("token ghp_123456789012345678901234567890123456", sessionID);
  const token = Object.keys(v)[0];

  const response = { text: `Found ${token}` };
  await hooks["experimental.text.complete"]({ sessionID }, response);
  assert.ok(response.text.includes("ghp_123456789012345678901234567890123456"));
});

test("maskUi=true: chat.message masks prompt, text.complete skips unmask", async () => {
  const sessionID = "test-maskui-true";
  const hooks = createV1Hooks(true);

  assert.equal(typeof hooks["chat.message"], "function");
  assert.equal(typeof hooks["experimental.text.complete"], "function");

  const secret = "sk-proj-1234567890abcdef1234567890";
  const promptOutput = { parts: [{ type: "text", text: `Use ${secret} now` }] };
  await hooks["chat.message"]({ sessionID }, promptOutput);

  assert.ok(!promptOutput.parts[0].text.includes(secret));
  assert.ok(promptOutput.parts[0].text.includes("__MASKED_"));

  const token = promptOutput.parts[0].text.match(/__MASKED_[A-Z_0-9]+__/)[0];
  const response = { text: `Result ${token} done` };
  await hooks["experimental.text.complete"]({ sessionID }, response);

  assert.ok(!response.text.includes(secret));
  assert.ok(response.text.includes(token));
});

test("maskUi via env var override", async () => {
  const prev = process.env.SECRET_REDACTOR_MASK_UI;
  process.env.SECRET_REDACTOR_MASK_UI = "1";
  try {
    const hooks = await plugin({});
    assert.equal(typeof hooks["chat.message"], "function");
  } finally {
    if (prev === undefined) delete process.env.SECRET_REDACTOR_MASK_UI;
    else process.env.SECRET_REDACTOR_MASK_UI = prev;
  }
});

test("Cross-engine parity: Python and JavaScript interoperability", async () => {
  const { spawnSync } = require("node:child_process");
  const sessionID = "test-cross-parity";
  const secret = "sk-ant-1234567890abcdef1234567890";

  // 1. Python masks, JS unmasks
  const pyCode = `
from src.secret_redactor.vault import mask_text
masked, v = mask_text("key: ${secret}", "${sessionID}")
import json
print(json.dumps({"masked": masked, "vault": v}))
`;
  const pyProc = spawnSync("python3", ["-c", pyCode], { encoding: "utf-8" });
  assert.equal(pyProc.status, 0);
  const { vault: pyVault } = JSON.parse(pyProc.stdout);
  const pyToken = Object.keys(pyVault)[0];

  const jsUnmasked = vault.unmaskText(pyToken, sessionID);
  assert.equal(jsUnmasked, secret);

  // 2. JS masks, Python unmasks
  const secretGh = "ghp_123456789012345678901234567890123456";
  const [, jsVault] = vault.maskText(`gh: ${secretGh}`, sessionID);
  const jsToken = Object.keys(jsVault).find(k => jsVault[k] === secretGh);

  const pyCode2 = `
from src.secret_redactor.vault import unmask_text
print(unmask_text("${jsToken}", "${sessionID}"))
`;
  const pyProc2 = spawnSync("python3", ["-c", pyCode2], { encoding: "utf-8" });
  assert.equal(pyProc2.status, 0);
  assert.equal(pyProc2.stdout.trim(), secretGh);
});
