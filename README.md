# llm-secret-redactor

Zero-leak secret redactor for **Claude Code**, **opencode**, and **OpenAI Codex CLI**.

Prevents sensitive credentials (API keys, passwords, database URIs, bearer tokens) from reaching LLMs when read from files or logs, while restoring them seamlessly on your terminal output.

## Architecture

One core, thin adapters. All detection and masking logic lives in a single place (`src/secret_redactor/vault.py`) — no tool reimplements patterns or token logic:

```
src/secret_redactor/
├── vault.py                 # Core: patterns, mask/unmask, session vault (single source of truth)
├── cli.py                   # Generic core CLI: mask | unmask | check
├── user_prompt_submit.py    # ┐
├── pre_tool_use.py          # │ Claude Code & Codex adapters (JSON on stdin -> JSON on stdout)
├── post_tool_use.py         # │
└── message_display.py       # ┘
opencode/
└── secret-redactor.ts       # opencode adapter (thin plugin that shells out to cli.py)
```

Every adapter exposes the same five behaviors:

| Behavior | Claude Code | Codex CLI | opencode |
|---|---|---|---|
| Block raw secrets in prompts | `UserPromptSubmit` | `UserPromptSubmit` | `chat.message` |
| Mask tool output before the LLM sees it | `PostToolUse` (output rewrite) | `PostToolUse` (block + redacted feedback)¹ | `tool.execute.after` |
| Unmask tokens in command execution so commands keep working | `PreToolUse` (Bash only) | `PreToolUse` (Bash only) | `tool.execute.before` (bash only) |
| Reveal secrets to the human after the turn | `MessageDisplay` (in-stream) | `Stop` hook (systemMessage with the revealed lines) | `session.idle` event (toast with the revealed lines) |
| Session vault (token → secret) | `vault.py` | `vault.py` (shared) | `cli.py` (shared) |

¹ Codex cannot rewrite tool output in place. Instead, its `PostToolUse` hook (installed with `--host codex`) returns `decision: block` with the **masked output as the feedback reason** — the model is fed only the redacted text, while the raw output still shows in your local TUI (Codex additionally collapses token-like strings there).

² Codex and opencode cannot rewrite the rendered assistant message itself (one channel feeds both model and TUI, and neither exposes a display-replacement hook), so the reveal is surfaced as a separate post-turn element: a system message in Codex, a toast in opencode — showing only the lines that contained secrets.

Unmasking is deliberately restricted to **command execution tools** (Bash). Read/write/edit tools are never unmasked, so if the model copies a masked token into a new file, the file receives the masked token — raw secrets can never be re-spread by the LLM because they never reach it.

## Revealing masked output

Claude Code reveals secrets automatically in assistant replies (via `MessageDisplay`). Codex and opencode have **no display hook** — a tool result or assistant message is a single channel that feeds both the model and the TUI, so a hook can never show the raw secret there without leaking it to the model.

For those platforms, use the bundled CLI to reveal masked text locally:

```bash
echo "token is __MASKED_GITHUB_TOKEN_5D8E3EA9__" | python3 ~/.codex/hooks/secret-redactor/cli.py reveal          # Codex install
echo "token is __MASKED_GITHUB_TOKEN_5D8E3EA9__" | python3 ~/.config/opencode/secret-redactor/cli.py reveal  # opencode install
```

`reveal` reads any text on stdin, restores every `__MASKED_*__` token it finds using the local vault (all sessions, or one with `--session ID`), and prints the original secrets. It is a local convenience for you — the model never sees the result.

Masked tokens are deterministic (`__MASKED_<TYPE>_<sha256[:8]>__`), and the token→secret vault is stored per session under `/tmp/claude_secret_vault/` (override with the `SECRET_REDACTOR_VAULT_DIR` environment variable).

## How it works

1. **Prompt Guard (`UserPromptSubmit`)**: Blocks prompt submission if raw secrets are typed directly.
2. **Data Redaction (`PostToolUse`)**: Replaces secrets in tool outputs (files, commands, logs) with deterministic tokens (`__MASKED_SECRET_<hash>__`) before sending to the model.
3. **Session Vault (`vault.py`)**: Stores token-to-secret mappings locally.
4. **Terminal Unmasking (`MessageDisplay`)**: Restores original secrets in streamed responses so you read plain text (Claude Code only).
5. **Tool Unmasking (`PreToolUse`, Bash only)**: Unmasks tokens before subsequent shell executions so scripts and curl commands don't break. Write/edit tools are never unmasked (see the table above).

## Installation

Run interactive installer via curl:

```bash
curl -fsSL https://raw.githubusercontent.com/mhbahmani/llm-secret-redactor/master/install.sh | bash
```

The installer is **fully idempotent** (running it multiple times updates files safely without duplicating hook entries) and interactively guides you through:
1. **Action selection**: `Install` or `Uninstall`.
2. **Target tool**: `Claude Code`, `opencode`, or `Codex`.
3. **Scope selection**:
   - **Local**: Project-level (`.claude/`, `.opencode/`, or `.codex/`) — applies only to the current repository.
   - **Global**: User-level (`~/.claude/`, `~/.config/opencode/`, or `~/.codex/`) — applies across all sessions on your system.
4. **Custom destination path**:
   - Displays the default target directory for the chosen tool.
   - Press **Enter** to accept default, or type a custom path.

Per-tool notes:
- **Claude Code**: copies the hook scripts and registers them in `<dir>/settings.json`.
- **opencode**: copies the Python core to `<dir>/secret-redactor/` and the plugin to `<dir>/plugins/secret-redactor.ts`. opencode auto-discovers plugins, so no settings file is touched. Requires `python3` on PATH. **Restart opencode** after installing.
- **Codex**: copies the hook scripts to `<dir>/hooks/secret-redactor/` and registers `UserPromptSubmit`, `PostToolUse` (block + redacted feedback mode), and `PreToolUse` (Bash-matched) in `<dir>/hooks.json`. Codex requires one extra step: open Codex, run `/hooks`, and **trust the new hooks** — untrusted hooks are skipped by design.

Or run directly from repo:

```bash
git clone https://github.com/mhbahmani/llm-secret-redactor.git
./llm-secret-redactor/install.sh
```

Flags can also be passed directly to bypass the action prompt:
- `./install.sh --install`
- `./install.sh --uninstall`

## Uninstallation

To cleanly remove redactor hooks and files without affecting any of your custom settings or other hooks:

Via curl:
```bash
curl -fsSL https://raw.githubusercontent.com/mhbahmani/llm-secret-redactor/master/install.sh | bash -s -- --uninstall
```

Or run the script and select `Uninstall`:
```bash
./install.sh --uninstall
```

What uninstallation does:
- Removes the isolated redactor directory (`hooks/secret-redactor/` or `secret-redactor/`), preventing file conflicts with any other hooks or tools.
- Creates a timestamped backup of the settings/hooks file.
- Removes only redactor hook definitions from `settings.json` / `hooks.json` while keeping your other settings, tools, env vars, and user-defined hooks completely intact.
- Removes the parent `hooks/` directory only if it is empty.

## Testing

Run unit and integration tests (includes tests for the core CLI and a TypeScript smoke test for the opencode plugin; the latter requires Node >= 22.6 and python3 on PATH):

```bash
python3 -m unittest discover tests
```
