const { createV1Hooks } = require("./v1.js");
const { registerV2Hooks } = require("./v2.js");
const { maskModelMessages } = require("./transformer.js");
const fs = require("fs");
const path = require("path");
const os = require("os");

function isTruthyEnv(val) {
  return ["1", "true", "yes"].includes(String(val || "").toLowerCase().trim());
}

function isFalsyEnv(val) {
  return ["0", "false", "no"].includes(String(val || "").toLowerCase().trim());
}

function resolveMaskUi(input) {
  const envVal = process.env.SECRET_REDACTOR_MASK_UI;
  if (envVal !== undefined) {
    if (isTruthyEnv(envVal)) return true;
    if (isFalsyEnv(envVal)) return false;
  }

  const projectDir = input?.directory || input?.worktree || process.cwd();
  const candidates = [
    path.join(projectDir, ".opencode", "secret-redactor.json"),
    path.join(os.homedir(), ".config", "opencode", "secret-redactor.json"),
  ];
  for (const p of candidates) {
    try {
      if (fs.existsSync(p)) {
        const cfg = JSON.parse(fs.readFileSync(p, "utf-8"));
        if (typeof cfg.maskUi === "boolean") return cfg.maskUi;
      }
    } catch {
      // ignore unreadable config
    }
  }
  return false;
}

async function plugin(input, options) {
  const maskUi = resolveMaskUi(input);

  if (input && (input.session?.hook || input.tool?.hook)) {
    registerV2Hooks(input, maskUi);
  }

  return createV1Hooks(maskUi);
}

module.exports = plugin;
