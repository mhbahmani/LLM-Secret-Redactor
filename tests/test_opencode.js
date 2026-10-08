const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");

const TEST_RUNTIME_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "llm-redactor-opencode-"));
process.env.SECRET_REDACTOR_RUNTIME_DIR = TEST_RUNTIME_DIR;
process.env.SECRET_REDACTOR_BROKER_IDLE = "10";

let vault, plugin, createHooks;
test.before(async () => {
  vault = await import("../src/secret_redactor/vault.mjs");
  ({ default: plugin } = await import("../src/secret_redactor/opencode/plugin.mjs"));
  ({ createHooks } = await import("../src/secret_redactor/opencode/hooks.mjs"));
});

test.after(async () => {
  await vault.shutdownBroker();
  fs.rmSync(TEST_RUNTIME_DIR, { recursive: true, force: true });
});

test("Outbound message masking: original -> mask before model request", async () => {
  const sessionID = "test-outbound";
  const hooks = await plugin.server({});

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
  const hooks = await plugin.server({});

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
  const hooks = await plugin.server({});

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
  const hooks = await plugin.server({});

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
  const hooks = await plugin.server({});

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

test("Assistant responses remain redacted until explicit local reveal", async () => {
  const hooks = await plugin.server({});
  assert.equal(hooks["experimental.text.complete"], undefined);
});

test("Broker socket is private and no plaintext vault file is created", async () => {
  const sessionID = "test-private-vault";
  await vault.maskText("key sk-proj-1234567890abcdef1234567890", sessionID);

  assert.equal(fs.statSync(TEST_RUNTIME_DIR).mode & 0o777, 0o700);
  assert.equal(fs.statSync(vault.getSocketPath()).mode & 0o777, 0o600);
  assert.deepEqual(fs.readdirSync(TEST_RUNTIME_DIR), ["broker.sock"]);
});

test("Concurrent clients reuse one broker process", async () => {
  const stats = await Promise.all(Array.from({ length: 8 }, () => vault.brokerStats()));
  assert.equal(new Set(stats.map((item) => item.pid)).size, 1);
});

test("Concurrent OS processes converge on one cold-start broker", async () => {
  const { spawn } = require("node:child_process");
  const isolatedRuntime = fs.mkdtempSync(path.join(os.tmpdir(), "llm-redactor-race-"));
  const vaultPath = path.resolve(__dirname, "../src/secret_redactor/vault.mjs");
  const runClient = (expression) => new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ["-e", expression], {
      env: {
        ...process.env,
        SECRET_REDACTOR_RUNTIME_DIR: isolatedRuntime,
        SECRET_REDACTOR_BROKER_IDLE: "10",
        TEST_VAULT_PATH: vaultPath,
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("error", reject);
    child.on("exit", (code) => {
      if (code !== 0) reject(new Error(stderr || `client exited ${code}`));
      else resolve(stdout.trim());
    });
  });

  try {
    const expression = "import(process.env.TEST_VAULT_PATH).then(v => v.brokerStats()).then(x => console.log(x.pid))";
    const pids = await Promise.all(Array.from({ length: 8 }, () => runClient(expression)));
    assert.equal(new Set(pids).size, 1);
  } finally {
    await runClient("import(process.env.TEST_VAULT_PATH).then(v => v.shutdownBroker())")
      .catch(() => {});
    fs.rmSync(isolatedRuntime, { recursive: true, force: true });
  }
});

test("Tool argument restoration before local execution", async () => {
  const sessionID = "test-tool-arg";
  const hooks = await plugin.server({});

  // Populate vault
  const [_, v] = await vault.maskText("token ghp_123456789012345678901234567890123456", sessionID);
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
  const hooks = await plugin.server({});

  const [, v1] = await vault.maskText("url postgres://admin:Pass123!@localhost/db", sessionID);
  const token1 = Object.keys(v1).find(k => k.includes("URIPASS"));

  const [, v2] = await vault.maskText("api_key='SuperSecret456'", sessionID);
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
  const hooks = await plugin.server({});

  // Secret in session A
  const [_, vaultA] = await vault.maskText("key sk-proj-1111111111111111111111111111", sessionA);
  const tokenA = Object.keys(vaultA)[0];

  // Try unmasking in session B
  const toolInputB = {
    args: { key: tokenA }
  };

  await hooks["tool.execute.before"]({ tool: "test", sessionID: sessionB, callID: "c-3" }, toolInputB);

  // Session B does not have tokenA in its vault, so it remains unchanged
  assert.equal(toolInputB.args.key, tokenA);
});

test("Canonical messages are always masked and never auto-revealed", async () => {
  const sessionID = "test-secure-ui";
  const hooks = createHooks();

  assert.equal(typeof hooks["chat.message"], "function");
  assert.equal(hooks["experimental.text.complete"], undefined);

  const secret = "sk-proj-1234567890abcdef1234567890";
  const promptOutput = { parts: [{ type: "text", text: `Use ${secret} now` }] };
  await hooks["chat.message"]({ sessionID }, promptOutput);

  assert.ok(!promptOutput.parts[0].text.includes(secret));
  assert.ok(promptOutput.parts[0].text.includes("__MASKED_"));

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

  const jsUnmasked = await vault.unmaskText(pyToken, sessionID);
  assert.equal(jsUnmasked, secret);

  // 2. JS masks, Python unmasks
  const secretGh = "ghp_123456789012345678901234567890123456";
  const [, jsVault] = await vault.maskText(`gh: ${secretGh}`, sessionID);
  const jsToken = Object.keys(jsVault).find(k => jsVault[k] === secretGh);

  const pyCode2 = `
from src.secret_redactor.vault import unmask_text
print(unmask_text("${jsToken}", "${sessionID}"))
`;
  const pyProc2 = spawnSync("python3", ["-c", pyCode2], { encoding: "utf-8" });
  assert.equal(pyProc2.status, 0);
  assert.equal(pyProc2.stdout.trim(), secretGh);
});

test("Node and Python clients resolve the same broker socket", async () => {
  const { spawnSync } = require("node:child_process");
  const vaultPath = path.resolve(__dirname, "../src/secret_redactor/vault.mjs");
  const brokerDir = path.resolve(__dirname, "../src/secret_redactor");
  const cases = [
    { SECRET_REDACTOR_RUNTIME_DIR: "~/redactor-test-runtime" },
    { XDG_RUNTIME_DIR: "/run/user/test-xdg" },
    { TMPDIR: "/var/folders/test-tmp" },
  ];
  for (const overrides of cases) {
    const env = { ...process.env, ...overrides };
    if (!("SECRET_REDACTOR_RUNTIME_DIR" in overrides)) delete env.SECRET_REDACTOR_RUNTIME_DIR;
    if (!("XDG_RUNTIME_DIR" in overrides)) delete env.XDG_RUNTIME_DIR;
    const node = spawnSync(process.execPath, [
      "--input-type=module", "-e",
      `const v = await import(${JSON.stringify(vaultPath)}); console.log(v.getSocketPath());`,
    ], { env, encoding: "utf-8" });
    const python = spawnSync("python3", [
      "-c", "import sys; sys.path.insert(0, sys.argv[1]); import broker; print(broker.socket_path())", brokerDir,
    ], { env, encoding: "utf-8" });
    assert.equal(node.status, 0, node.stderr);
    assert.equal(python.status, 0, python.stderr);
    assert.equal(node.stdout.trim(), python.stdout.trim(), JSON.stringify(overrides));
  }
});
