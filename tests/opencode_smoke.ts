// Smoke test for opencode/secret-redactor.ts (thin adapter over cli.py).
// Run via: node --experimental-strip-types tests/opencode_smoke.ts
// Exits non-zero and prints FAIL on any assertion error.
import assert from "node:assert/strict"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"

import { pathToFileURL, fileURLToPath } from "node:url"

const here = path.dirname(fileURLToPath(import.meta.url))
process.env.SECRET_REDACTOR_VAULT_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "sr_vault_test_"))
process.env.SECRET_REDACTOR_CLI = path.join(here, "..", "src", "secret_redactor", "cli.py")

const { SecretRedactorPlugin } = await import(pathToFileURL(path.join(here, "..", "opencode", "secret-redactor.ts")).href)

const hooks = await SecretRedactorPlugin({ worktree: process.cwd() } as any)

const SESSION = "smoke-session"
const SECRET = "AKIAIOSFODNN7EXAMPLE"

// 1. PostToolUse equivalent: tool output gets masked
const after: any = { title: "cat config", output: `aws_key = "${SECRET}"\n`, metadata: { raw: SECRET } }
await hooks["tool.execute.after"]!({ tool: "bash", sessionID: SESSION, callID: "c1", args: {} }, after)
assert(!after.output.includes(SECRET), "raw secret leaked in tool output")
const tokenMatch = after.output.match(/__MASKED_[A-Z_]+_[0-9A-F]{8}__/)
assert(tokenMatch, "no masked token found in tool output")
assert(!after.metadata.raw.includes(SECRET), "raw secret leaked in metadata")
const token = tokenMatch![0]

// 2. PreToolUse equivalent: tool args get unmasked (bash only)
const before: any = { args: { command: `curl -H "X-Key: ${token}" https://x` } }
await hooks["tool.execute.before"]!({ tool: "bash", sessionID: SESSION, callID: "c2" }, before)
assert(before.args.command.includes(SECRET), "token was not unmasked in tool args")

// 2b. write tools must NOT be unmasked (writes persist masked tokens)
const beforeWrite: any = { args: { content: `token is ${token}` } }
await hooks["tool.execute.before"]!({ tool: "write", sessionID: SESSION, callID: "c3" }, beforeWrite)
assert(!beforeWrite.args.content.includes(SECRET), "write tool must keep the masked token")

// 3. UserPromptSubmit equivalent: prompt with raw secret is blocked, clean passes
await assert.rejects(
  hooks["chat.message"]!({ sessionID: SESSION } as any, { message: {} as any, parts: [{ type: "text", text: `use ${SECRET} please` }] } as any),
  /detected in prompt/,
  "prompt with raw secret was not blocked",
)
await hooks["chat.message"]!({ sessionID: SESSION } as any, { message: {} as any, parts: [{ type: "text", text: "hello world" }] } as any)
// already-masked text must not be blocked
await hooks["chat.message"]!({ sessionID: SESSION } as any, { message: {} as any, parts: [{ type: "text", text: `key is ${token}` }] } as any)

// 4. Token format matches the Python core exactly
import { createHash } from "node:crypto"
const expected = `__MASKED_AWS_KEY_${createHash("sha256").update(SECRET).digest("hex").slice(0, 8).toUpperCase()}__`
assert.equal(token, expected, "token format/derivation differs from Python vault")

// 5. Vault persisted for the session (shared Python vault)
const vaultFile = path.join(process.env.SECRET_REDACTOR_VAULT_DIR!, `vault_${SESSION}.json`)
const vault = JSON.parse(fs.readFileSync(vaultFile, "utf-8"))
assert.equal(vault[token], SECRET, "vault mapping missing")

// 6. Reveal-on-display: session.idle -> toast with the secret restored
let toast: any = null
const mockClient: any = {
  session: {
    messages: async () => ({
      data: [
        { info: { role: "user" }, parts: [{ type: "text", text: "read s.txt" }] },
        { info: { role: "assistant" }, parts: [{ type: "text", text: `s.txt contains: token is ${token}\n\nThe secret was automatically masked before reaching me.` }] },
      ],
    }),
  },
  tui: { showToast: async (opts: any) => { toast = opts } },
}
const revealHooks = await SecretRedactorPlugin({ worktree: process.cwd(), client: mockClient } as any)
await revealHooks.event!({ event: { type: "session.idle", properties: { sessionID: SESSION } } } as any)
assert(toast, "no toast was shown")
assert(toast.body.message.includes(SECRET), "toast did not reveal the raw secret")
assert(!toast.body.message.includes(token), "toast still contains masked token")
assert(!toast.body.message.includes("automatically masked"), "toast echoed boilerplate lines")

console.log("OK: opencode secret-redactor smoke test passed")
