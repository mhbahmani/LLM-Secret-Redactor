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

Run this one-liner inside your project root:

```bash
curl -fsSL https://raw.githubusercontent.com/mhbahmani/llm-secret-redactor/main/install.sh | bash
```

Or clone and install locally:

```bash
git clone https://github.com/mhbahmani/llm-secret-redactor.git
./llm-secret-redactor/install.sh .
```

## Testing

Run integration tests:

```bash
python3 test_hooks.py
```
