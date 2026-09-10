# llm-secret-redactor

Zero-leak secret redactor for Claude Code using native hooks.

Prevents sensitive credentials (API keys, passwords, database URIs, bearer tokens) from reaching LLMs when read from files or logs, while restoring them seamlessly on your terminal output.

## How it works

1. **Prompt Guard (`UserPromptSubmit`)**: Blocks prompt submission if raw secrets are typed directly.
2. **Data Redaction (`PostToolUse`)**: Replaces secrets in tool outputs (files, commands, logs) with deterministic tokens (`__MASKED_SECRET_<hash>__`) before sending to Claude.
3. **Session Vault (`vault.py`)**: Stores token-to-secret mappings locally in `/tmp/claude_secret_vault/`.
4. **Terminal Unmasking (`MessageDisplay`)**: Restores original secrets in streamed responses so you read plain text.
5. **Tool Unmasking (`PreToolUse`)**: Unmasks tokens before subsequent tool executions so scripts and curl commands don't break.

## Installation

Run interactive installer via curl:

```bash
curl -fsSL https://raw.githubusercontent.com/mhbahmani/llm-secret-redactor/master/install.sh | bash
```

The installer is **fully idempotent** (running it multiple times updates files safely without duplicating hook entries) and interactively guides you through:
1. **Action selection**: `Install` or `Uninstall`.
2. **Scope selection**:
   - **Local**: Project-level (`.claude/`) — applies only to the current repository.
   - **Global**: User-level (`~/.claude/`) — applies across all Claude Code sessions on your system.
3. **Custom destination path**:
   - Displays default target directory (`$PWD/.claude` or `~/.claude`).
   - Press **Enter** to accept default, or type a custom path.

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
- Removes the isolated redactor directory (`hooks/secret-redactor/`), preventing file conflicts with any other hooks or tools.
- Creates a timestamped backup of `settings.json`.
- Removes only redactor hook definitions from `settings.json` while keeping your other settings, tools, env vars, and user-defined hooks completely intact.
- Removes the parent `hooks/` directory only if it is empty.

## Testing

Run integration tests:

```bash
python3 test_hooks.py
```
