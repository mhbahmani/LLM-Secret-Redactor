/**
 * llm-secret-redactor plugin for opencode.
 *
 * Thin adapter over the shared Python core (src/secret_redactor/cli.py).
 * All pattern matching, masking, and vault logic lives in vault.py — this
 * file only wires opencode's plugin API onto the CLI:
 *
 *   - chat.message          -> `check`  (blocks prompts containing raw secrets)
 *   - tool.execute.before   -> `unmask` (bash only: restores tokens so commands work)
 *   - tool.execute.after    -> `mask`   (masks secrets before the LLM sees output)
 *   - event (session.idle)  -> `reveal` (toast with the assistant's secrets restored)
 *
 * Requires python3 on PATH. The CLI location is resolved in order:
 *   1. $SECRET_REDACTOR_CLI
 *   2. <worktree>/.opencode/secret-redactor/cli.py   (project install)
 *   3. ~/.config/opencode/secret-redactor/cli.py     (global install)
 *
 * Requires python3. opencode has no display hook, so assistant replies may
 * still show __MASKED_*__ tokens in the TUI (tool calls are unmasked
 * transparently).
 */
import type { Plugin } from "@opencode-ai/plugin"
import { spawnSync } from "node:child_process"
import fs from "node:fs"
import path from "node:path"
import os from "node:os"

const PYTHON = process.env.SECRET_REDACTOR_PYTHON || "python3"

function findCli(worktree?: string): string | null {
  const candidates = [
    process.env.SECRET_REDACTOR_CLI,
    worktree && path.join(worktree, ".opencode", "secret-redactor", "cli.py"),
    path.join(os.homedir(), ".config", "opencode", "secret-redactor", "cli.py"),
  ].filter((p): p is string => Boolean(p))
  return candidates.find((p) => fs.existsSync(p)) ?? null
}

type CliResult = { data: any; changed: boolean; secret_type?: string }

export const SecretRedactorPlugin: Plugin = async ({ client, worktree }) => {
  const cli = findCli(worktree)

  // Fail-open wrapper: redaction must never break the session.
  const call = (mode: "mask" | "unmask" | "check", sessionID: string, data: unknown): CliResult | null => {
    if (!cli) return null
    try {
      const proc = spawnSync(PYTHON, [cli, mode], {
        input: JSON.stringify({ session_id: sessionID, data }),
        encoding: "utf-8",
        timeout: 10_000,
      })
      if (proc.status !== 0 || !proc.stdout) return null
      return JSON.parse(proc.stdout) as CliResult
    } catch {
      return null
    }
  }

  // Human-facing reveal: raw stdin text -> text with tokens restored.
  const reveal = (sessionID: string, text: string): string | null => {
    if (!cli) return null
    try {
      const proc = spawnSync(PYTHON, [cli, "reveal", "--session", sessionID], {
        input: text,
        encoding: "utf-8",
        timeout: 10_000,
      })
      if (proc.status !== 0) return null
      return proc.stdout
    } catch {
      return null
    }
  }

  return {
    // UserPromptSubmit equivalent: block prompts containing raw secrets.
    "chat.message": async (input, output) => {
      for (const part of (output.parts || []) as any[]) {
        if (part && typeof part === "object" && typeof part.text === "string") {
          const res = call("check", input.sessionID, part.text)
          if (res?.secret_type) {
            throw new Error(
              `Secret/token of type [${res.secret_type}] detected in prompt. Please remove it before sending.`,
            )
          }
        }
      }
    },

    // PreToolUse equivalent: restore real secrets only for command execution.
    // Write/edit tools must persist the masked token instead of re-spreading
    // the raw secret into new files.
    "tool.execute.before": async (input, output) => {
      if (input.tool.toLowerCase() !== "bash") return
      const res = call("unmask", input.sessionID, output.args)
      if (res?.changed) output.args = res.data
    },

    // PostToolUse equivalent: mask secrets before output reaches the LLM.
    "tool.execute.after": async (input, output) => {
      const payload = { title: output.title, output: output.output, metadata: output.metadata }
      const res = call("mask", input.sessionID, payload)
      if (res?.changed && res.data) {
        output.title = res.data.title
        output.output = res.data.output
        output.metadata = res.data.metadata
      }
    },
    // Reveal-on-display: when the turn finishes, surface the assistant's
    // reply with tokens restored as a TUI toast. opencode cannot rewrite the
    // rendered message, so this mirrors Codex's stop_reveal approach.
    event: async ({ event }) => {
      if (event.type !== "session.idle" || !cli || !client) return
      try {
        const sessionID = (event as any).properties?.sessionID
        if (!sessionID) return
        const res: any = await (client as any).session.messages({ path: { id: sessionID } })
        const messages = res?.data ?? res
        if (!Array.isArray(messages) || messages.length === 0) return
        const lastAssistant = [...messages].reverse().find((m: any) => m?.info?.role === "assistant")
        if (!lastAssistant) return
        const text = (lastAssistant.parts || [])
          .filter((p: any) => p?.type === "text" && typeof p.text === "string")
          .map((p: any) => p.text)
          .join("\n")
        const revealedLines = text
          .split("\n")
          .map((line) => ({ line, revealed: reveal(sessionID, line) }))
          .filter((x) => x.revealed !== null && x.revealed !== x.line)
          .map((x) => x.revealed)
        if (revealedLines.length === 0) return
        await (client as any).tui.showToast({
          body: {
            title: "llm-secret-redactor",
            message: revealedLines.join("\n"),
            variant: "info",
            duration: 15000,
          },
        })
      } catch {
        // Fail open: never break the session over a toast
      }
    },
  }
}

export default SecretRedactorPlugin
