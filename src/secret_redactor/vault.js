const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

const DEFAULT_PATTERNS = [
  { name: "Anthropic key", pattern: "sk-ant-[a-zA-Z0-9_\\-]{20,}", kind: "TOKEN" },
  { name: "OpenAI key", pattern: "sk-[a-zA-Z0-9_\\-]{20,}", kind: "TOKEN" },
  { name: "GitHub Token", pattern: "gh[pousr]_[a-zA-Z0-9]{36,}", kind: "TOKEN" },
  { name: "AWS Access Key ID", pattern: "(?:A3T[A-Z0-9]|AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}", kind: "AWS_KEY" },
  { name: "Generic bearer token", pattern: "bearer\\s+([a-zA-Z0-9_\\-.]{20,})", flags: "i", kind: "BEARER_TOKEN" },
  { name: "URI with credentials", pattern: "([a-z0-9+.\\-]+://[^:\\s@/]+:)([^@\\s/]+)(@)", flags: "i", kind: "URI_PASS" },
  { name: "Key-value secrets", pattern: "([\"']?(?:password|passwd|secret|api[_-]?key|token|auth_token)[\"']?\\s*[:=]\\s*[\"']?)([^\\s\"',;}{]{6,})([\"']?)", flags: "i", kind: "KV_SECRET" },
  { name: "JWT token pattern", pattern: "eyJ[a-zA-Z0-9_\\-]{10,}\\.eyJ[a-zA-Z0-9_\\-]{10,}\\.[a-zA-Z0-9_\\-]{10,}", kind: "JWT_TOKEN" }
];

const VAULT_DIR = process.env.VAULT_DIR || "/tmp/claude_secret_vault";

function loadPatterns() {
  const candidates = [
    path.join(__dirname, "patterns.json"),
    path.join(__dirname, "..", "patterns.json"),
    path.join(process.cwd(), "patterns.json"),
  ];

  let rawPatterns = null;
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      try {
        const content = fs.readFileSync(candidate, "utf-8");
        rawPatterns = JSON.parse(content);
        break;
      } catch {
        // Ignore read/parse error and try next
      }
    }
  }

  return rawPatterns || DEFAULT_PATTERNS;
}

function getCompiledPatterns() {
  const raw = loadPatterns();
  return raw.map((item) => {
    let flags = "g";
    if (item.flags && item.flags.includes("i")) {
      flags += "i";
    }
    return {
      regex: new RegExp(item.pattern, flags),
      kind: item.kind,
      name: item.name
    };
  });
}

function getVaultPath(sessionID) {
  fs.mkdirSync(VAULT_DIR, { recursive: true, mode: 0o700 });
  fs.chmodSync(VAULT_DIR, 0o700);
  const safeSession = String(sessionID || "default").replace(/[^a-zA-Z0-9_\-]/g, "_");
  return path.join(VAULT_DIR, `vault_${safeSession}.json`);
}

function loadVault(sessionID) {
  const vaultPath = getVaultPath(sessionID);
  if (!fs.existsSync(vaultPath)) {
    return {};
  }
  try {
    const data = fs.readFileSync(vaultPath, "utf-8");
    const parsed = JSON.parse(data);
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
      throw new Error("vault must contain a JSON object");
    }
    return parsed;
  } catch (error) {
    throw new Error(`Unable to read secret vault ${vaultPath}: ${error.message}`);
  }
}

function saveVault(sessionID, vault) {
  const vaultPath = getVaultPath(sessionID);
  const tempPath = `${vaultPath}.${process.pid}.${crypto.randomBytes(6).toString("hex")}.tmp`;
  try {
    fs.writeFileSync(tempPath, JSON.stringify(vault, null, 2), {
      encoding: "utf-8",
      mode: 0o600,
      flag: "wx"
    });
    fs.renameSync(tempPath, vaultPath);
    fs.chmodSync(vaultPath, 0o600);
  } catch (error) {
    try {
      fs.unlinkSync(tempPath);
    } catch {
      // The temporary file may not have been created.
    }
    throw new Error(`Unable to persist secret vault ${vaultPath}: ${error.message}`);
  }
}

function makeToken(prefix, secret) {
  const digest = crypto
    .createHash("sha256")
    .update(secret, "utf-8")
    .digest("hex")
    .slice(0, 8)
    .toUpperCase();
  return `__MASKED_${prefix}_${digest}__`;
}

function tokenForSecret(prefix, secret, vault) {
  const digest = crypto.createHash("sha256").update(secret, "utf-8").digest("hex").toUpperCase();
  for (let length = 8; length <= digest.length; length += 4) {
    const token = `__MASKED_${prefix}_${digest.slice(0, length)}__`;
    if (vault[token] === undefined || vault[token] === secret) {
      return token;
    }
  }
  throw new Error("Unable to generate a collision-free mask token");
}

function maskText(text, sessionID) {
  if (typeof text !== "string" || !text) {
    return [text, {}];
  }

  const vault = loadVault(sessionID);
  let updated = false;
  let result = text;

  const patterns = getCompiledPatterns();

  for (const { regex, kind } of patterns) {
    if (kind === "KV_SECRET") {
      result = result.replace(regex, (match, prefix, secret, suffix) => {
        if (secret.startsWith("__MASKED_") && secret.endsWith("__")) {
          return match;
        }
        const token = tokenForSecret("SECRET", secret, vault);
        if (vault[token] !== secret) {
          vault[token] = secret;
          updated = true;
        }
        return `${prefix}${token}${suffix}`;
      });
    } else if (kind === "URI_PASS") {
      result = result.replace(regex, (match, prefix, secret, suffix) => {
        const token = tokenForSecret("URIPASS", secret, vault);
        if (vault[token] !== secret) {
          vault[token] = secret;
          updated = true;
        }
        return `${prefix}${token}${suffix}`;
      });
    } else if (kind === "BEARER_TOKEN") {
      result = result.replace(regex, (match, secret) => {
        const token = tokenForSecret("BEARER", secret, vault);
        if (vault[token] !== secret) {
          vault[token] = secret;
          updated = true;
        }
        return `Bearer ${token}`;
      });
    } else {
      result = result.replace(regex, (secret) => {
        if (secret.startsWith("__MASKED_") && secret.endsWith("__")) {
          return secret;
        }
        const token = tokenForSecret(kind, secret, vault);
        if (vault[token] !== secret) {
          vault[token] = secret;
          updated = true;
        }
        return token;
      });
    }
  }

  if (updated) {
    saveVault(sessionID, vault);
  }

  return [result, vault];
}

function unmaskText(text, sessionID) {
  if (typeof text !== "string" || !text) {
    return text;
  }

  const vault = loadVault(sessionID);
  const tokens = Object.keys(vault);
  if (tokens.length === 0) {
    return text;
  }

  // Sort tokens longest first to avoid partial collision
  const sortedTokens = Object.entries(vault).sort(
    ([a], [b]) => b.length - a.length
  );

  let result = text;
  for (const [token, secret] of sortedTokens) {
    if (result.includes(token)) {
      result = result.split(token).join(secret);
    }
  }

  return result;
}

function maskRecursive(obj, sessionID) {
  if (typeof obj === "string") {
    const [masked] = maskText(obj, sessionID);
    return [masked, masked !== obj];
  }

  if (obj === null || typeof obj !== "object") {
    return [obj, false];
  }

  if (Array.isArray(obj)) {
    let changed = false;
    const newArr = [];
    for (const item of obj) {
      const [newItem, ch] = maskRecursive(item, sessionID);
      newArr.push(newItem);
      if (ch) changed = true;
    }
    return [newArr, changed];
  }

  let changed = false;
  const newObj = {};
  for (const [k, v] of Object.entries(obj)) {
    const [newV, ch] = maskRecursive(v, sessionID);
    newObj[k] = newV;
    if (ch) changed = true;
  }
  return [newObj, changed];
}

function unmaskRecursive(obj, sessionID) {
  if (typeof obj === "string") {
    const unmasked = unmaskText(obj, sessionID);
    return [unmasked, unmasked !== obj];
  }

  if (obj === null || typeof obj !== "object") {
    return [obj, false];
  }

  if (Array.isArray(obj)) {
    let changed = false;
    const newArr = [];
    for (const item of obj) {
      const [newItem, ch] = unmaskRecursive(item, sessionID);
      newArr.push(newItem);
      if (ch) changed = true;
    }
    return [newArr, changed];
  }

  let changed = false;
  const newObj = {};
  for (const [k, v] of Object.entries(obj)) {
    const [newV, ch] = unmaskRecursive(v, sessionID);
    newObj[k] = newV;
    if (ch) changed = true;
  }
  return [newObj, changed];
}

module.exports = {
  VAULT_DIR,
  loadPatterns,
  getVaultPath,
  loadVault,
  saveVault,
  makeToken,
  maskText,
  unmaskText,
  maskRecursive,
  unmaskRecursive
};
