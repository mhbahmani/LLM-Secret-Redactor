# llm-secret-redactor

Zero-leak secret redactor for **Claude Code** and **OpenCode**.

Prevents sensitive credentials (API keys, passwords, database URIs, bearer tokens, JWTs) from reaching LLMs when read from files, logs, or command output, while seamlessly restoring them on your local terminal and before local tool execution.

## Supported Clients

- **Claude Code**: Uses native lifecycle hooks (`UserPromptSubmit`, `PostToolUse`, `PreToolUse`, `MessageDisplay`).
- **OpenCode**: Uses native OpenCode plugin hooks (`experimental.chat.messages.transform`, `tool.execute.before`, `experimental.text.complete` in V1; `context`, `compaction`, `generate`, `title`, and `tool.execute.before` in V2).

---

## How It Works

### OpenCode Integration

In OpenCode, masking occurs immediately before assembled model context is dispatched to the LLM. OpenCode's canonical conversation history and local file state remain intact with original values.

```text
User / files / tools
        |
        v
OpenCode local context
        |
        | original (unmodified)
        v
model request hook (messages.transform / context)
        |
        | mask regex matches -> __MASKED_<id>__
        v
       LLM
        |
        | masked tokens in response / tool calls
        +-----------------------+
        |                       |
        v                       v
response restoration     tool argument restoration
(text.complete)          (tool.execute.before)
        |                       |
        v                       v
Terminal / User          Local tool execution
(original plain text)    (original plain values)
```

1. **Outbound Model-Context Masking**: Assembled context (user prompts, previous tool outputs, file content, grep results) is inspected immediately before dispatch. Matches are replaced with deterministic tokens (e.g. `__MASKED_TOKEN_<hash>__`).
2. **Session Vault (`vault.json`)**: Token-to-secret mappings are stored locally in `/tmp/claude_secret_vault/vault_<session>.json`.
3. **Tool Argument Restoration**: When the model issues a tool call containing masked values (e.g. `read("/home/__MASKED_USER_...__/config.json")`), arguments are recursively unmasked before local execution so scripts and file reads work transparently.
4. **Response Restoration**: Completed model output text is unmasked so the user reads real values in the terminal.
5. **Exact-Match Only**: Only tokens present in the current session's mapping store are restored. Invented or unknown tokens are left untouched.

### Claude Code Integration

1. **Prompt Guard (`UserPromptSubmit`)**: Blocks prompt submission if raw secrets are typed directly.
2. **Data Redaction (`PostToolUse`)**: Replaces secrets in tool outputs (files, commands, logs) with deterministic tokens (`__MASKED_SECRET_<hash>__`) before sending to Claude.
3. **Session Vault (`vault.py`)**: Stores token-to-secret mappings locally in `/tmp/claude_secret_vault/`.
4. **Terminal Unmasking (`MessageDisplay`)**: Restores original secrets in streamed responses so you read plain text.
5. **Tool Unmasking (`PreToolUse`)**: Unmasks tokens before subsequent tool executions so scripts and curl commands don't break.

---

## Configuration

Both Claude Code and OpenCode consume the same shared pattern configuration in `patterns.json`:

```json
[
  {
    "name": "OpenAI key",
    "pattern": "sk-[a-zA-Z0-9_\\-]{20,}",
    "kind": "TOKEN"
  },
  {
    "name": "GitHub Token",
    "pattern": "gh[pousr]_[a-zA-Z0-9]{36,}",
    "kind": "TOKEN"
  },
  {
    "name": "URI with credentials",
    "pattern": "([a-z0-9+.\\-]+://[^:\\s@/]+:)([^@\\s/]+)(@)",
    "flags": "i",
    "kind": "URI_PASS"
  }
]
```

To add custom patterns, modify or extend `patterns.json`. Both Python and JavaScript masking engines automatically load this configuration.

---

## Installation

Run interactive installer via curl:

```bash
curl -fsSL https://raw.githubusercontent.com/mhbahmani/llm-secret-redactor/master/install.sh | bash
```

The installer is **fully idempotent** and interactively guides you through:
1. **Action**: `Install` or `Uninstall`.
2. **Target client**: `Both`, `Claude Code`, or `OpenCode`.
3. **Scope**:
   - **Local**: Project-level (`.claude/` or `.opencode/`) — applies only to the current repository.
   - **Global**: User-level (`~/.claude/` or `~/.config/opencode/`) — applies across all sessions on your machine.
4. **Destination path**: Accept default or specify custom path.

### Command-line Flags

Bypass interactive prompts using flags:

```bash
# Install for both clients globally
./install.sh --install --all --global

# Install only for OpenCode locally
./install.sh --install --opencode --local

# Install only for Claude Code globally
./install.sh --install --claude --global

# Uninstall OpenCode integration
./install.sh --uninstall --opencode --global
```

---

## Uninstallation

To cleanly remove redactor integrations without affecting other settings or hooks:

```bash
# Interactive uninstallation
./install.sh --uninstall

# Or target a specific client
./install.sh --uninstall --opencode
./install.sh --uninstall --claude
```

What uninstallation does:
- Removes the isolated redactor directories (`hooks/secret-redactor/` or `plugins/secret-redactor/`).
- Creates a timestamped backup of your configuration (`settings.json` or `opencode.json` / `opencode.jsonc`).
- Removes only redactor hook/plugin registrations while leaving custom tools, settings, and other plugins completely intact.

---

## Testing

Run all automated tests across Python and JavaScript:

```bash
# Run both JavaScript and Python test suites
npm run test:all

# Run JavaScript OpenCode unit and integration tests
npm test

# Run Python Claude Code tests
python3 -m unittest discover tests
python3 tests/test_lifecycle.py
```

---

## Architecture & Limitations

- **OpenCode V1 vs V2**: OpenCode 1.x uses `experimental.chat.messages.transform` for outbound context masking, `tool.execute.before` for argument restoration, and `experimental.text.complete` for assistant response restoration. The plugin also provides an adapter for OpenCode V2 request hooks (`context`, `compaction`, `generate`, `title`).
- **Prompt Admission**: Masking intentionally occurs when context is assembled for model dispatch, preserving OpenCode's canonical conversation database without mutating the user's stored prompts.
