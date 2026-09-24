const fs = require("node:fs");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");
const { spawn } = require("node:child_process");

const BROKER_SCRIPT = path.join(__dirname, "broker.py");
const MAX_RESPONSE_BYTES = 16 * 1024 * 1024;
let startPromise = null;

function getRuntimeDir() {
  if (process.env.SECRET_REDACTOR_RUNTIME_DIR) {
    return path.resolve(process.env.SECRET_REDACTOR_RUNTIME_DIR);
  }
  if (process.env.XDG_RUNTIME_DIR) {
    return path.join(process.env.XDG_RUNTIME_DIR, "llm-secret-redactor");
  }
  const uid = typeof process.getuid === "function" ? process.getuid() : "user";
  return path.join(os.tmpdir(), `llm-secret-redactor-${uid}`);
}

function getSocketPath() {
  return path.join(getRuntimeDir(), "broker.sock");
}

function ensureRuntimeDir() {
  const directory = getRuntimeDir();
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  const info = fs.lstatSync(directory);
  if (info.isSymbolicLink() || !info.isDirectory()) {
    throw new Error(`Unsafe broker runtime directory: ${directory}`);
  }
  if (typeof process.getuid === "function" && info.uid !== process.getuid()) {
    throw new Error(`Broker runtime directory is not owned by the current user: ${directory}`);
  }
  fs.chmodSync(directory, 0o700);
}

function sendRequest(request) {
  return new Promise((resolve, reject) => {
    const client = net.createConnection(getSocketPath());
    let response = "";
    let settled = false;

    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      client.destroy();
      if (error) reject(error);
      else resolve(value);
    };

    client.setTimeout(3000, () => finish(new Error("Secret broker request timed out")));
    client.on("error", (error) => finish(error));
    client.on("connect", () => {
      client.write(`${JSON.stringify(request)}\n`);
    });
    client.on("data", (chunk) => {
      response += chunk.toString("utf-8");
      if (Buffer.byteLength(response) > MAX_RESPONSE_BYTES) {
        finish(new Error("Secret broker response exceeded size limit"));
        return;
      }
      const newline = response.indexOf("\n");
      if (newline === -1) return;
      try {
        const parsed = JSON.parse(response.slice(0, newline));
        if (!parsed.ok) {
          finish(new Error(parsed.error || "Secret broker request failed"));
        } else {
          finish(null, parsed);
        }
      } catch (error) {
        finish(error);
      }
    });
    client.on("end", () => {
      if (!settled) finish(new Error("Secret broker returned an empty response"));
    });
  });
}

async function startBroker() {
  if (startPromise) return startPromise;
  startPromise = (async () => {
    if (!fs.existsSync(BROKER_SCRIPT)) {
      throw new Error(`Secret broker executable is missing: ${BROKER_SCRIPT}`);
    }
    ensureRuntimeDir();
    const python = process.env.SECRET_REDACTOR_PYTHON || "python3";
    const child = spawn(python, [BROKER_SCRIPT, "--serve"], {
      detached: true,
      stdio: "ignore",
    });
    child.unref();

    const deadline = Date.now() + 3000;
    let lastError;
    while (Date.now() < deadline) {
      try {
        await sendRequest({ operation: "ping" });
        return;
      } catch (error) {
        lastError = error;
        await new Promise((resolve) => setTimeout(resolve, 25));
      }
    }
    throw new Error(`Secret broker did not start: ${lastError?.message || "unknown error"}`);
  })();
  try {
    await startPromise;
  } finally {
    startPromise = null;
  }
}

async function brokerRequest(operation, sessionID = "default", value) {
  const request = { operation, session: sessionID || "default" };
  if (value !== undefined) request.value = value;
  try {
    return await sendRequest(request);
  } catch (error) {
    if (!["ENOENT", "ECONNREFUSED", "ECONNRESET"].includes(error.code)) throw error;
    await startBroker();
    return sendRequest(request);
  }
}

async function rawBrokerRequest(request) {
  try {
    return await sendRequest(request);
  } catch (error) {
    if (!["ENOENT", "ECONNREFUSED", "ECONNRESET"].includes(error.code)) throw error;
    await startBroker();
    return sendRequest(request);
  }
}

async function maskText(text, sessionID) {
  if (typeof text !== "string" || !text) return [text, {}];
  const response = await brokerRequest("mask", sessionID, text);
  return [response.value, response.mappings || {}];
}

async function unmaskText(text, sessionID) {
  if (typeof text !== "string" || !text) return text;
  const response = await brokerRequest("unmask", sessionID, text);
  return response.value;
}

async function maskRecursive(value, sessionID) {
  const response = await brokerRequest("mask", sessionID, value);
  return [response.value, Boolean(response.changed)];
}

async function unmaskRecursive(value, sessionID) {
  const response = await brokerRequest("unmask", sessionID, value);
  return [response.value, Boolean(response.changed)];
}

async function clearSession(sessionID) {
  return brokerRequest("clear", sessionID);
}

async function brokerStats() {
  return brokerRequest("stats");
}

async function shutdownBroker() {
  let response = { ok: true };
  try {
    response = await sendRequest({ operation: "shutdown" });
  } catch {
    return response;
  }
  const deadline = Date.now() + 2000;
  while (fs.existsSync(getSocketPath()) && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  return response;
}

module.exports = {
  getRuntimeDir,
  getSocketPath,
  brokerRequest,
  rawBrokerRequest,
  maskText,
  unmaskText,
  maskRecursive,
  unmaskRecursive,
  clearSession,
  brokerStats,
  shutdownBroker,
};
