# llm-secret-redactor — Fix Plan

## 1. Configurable UI masking (Problem 1)

Add option `maskUi` (boolean, **default `false`**).

### Semantics

| | `maskUi=false` (default) | `maskUi=true` |
|---|---|---|
| User's own prompt (stored + UI echo) | real | masked at ingestion |
| Assistant text shown to user | unmasked | stays masked |
| Copy sent to model | masked | masked |
| Tool args before local exec | unmasked | unmasked |

### Wire-up

`false` (transparent UI):
- Remove `chat.message` hook (`opencode/v1.js:10-18`) → user echo stays real.
- `experimental.text.complete` unmasks assistant output.
- Keep `experimental.chat.messages.transform` + `tool.execute.before` unchanged.

`true` (full redaction):
- Register `chat.message` hook → mask user prompt.
- `experimental.text.complete` skips unmask (mask persists on screen).
- Same outbound masking + tool unmask.

### Config surface — TO INVESTIGATE

Plan step (deferred): check available config paths and pick one. Candidates:
1. Env var (e.g. `SECRET_REDACTOR_MASK_UI=1`).
2. Plugin-owned JSON file read at init (`secret-redactor.json`), project `.opencode/` then global `~/.config/opencode/`.
3. `opencode.jsonc` `plugin` array — **string array only, no per-plugin options** (verified against OpenCode docs); ruled out unless a custom scheme (e.g. options via a separate key) is confirmed later.

Entry point `index.js` already receives `plugin(input, options)`; derive project dir from `input.directory`/`input.worktree`. Pass resolved flag into `createV1Hooks(flags)` / `registerV2Hooks(ctx, flags)`.

### Installer (`install.py`)

- New interactive prompt "Mask secrets in UI? (No/Yes)" after scope selection.
- CLI flags `--mask-ui` / `--no-mask-ui`.
- Persist chosen value to the selected config surface.

### Tests

- `maskUi=false` → user prompt stays real, assistant text unmasked.
- `maskUi=true` → user prompt masked, assistant text stays masked.
- Adjust existing tests if any assume `chat.message` is always registered (none currently do).

---

## 2. Cross-version parity audit

Diff Claude Code (Python) vs OpenCode (JS) implementations; produce parity matrix; plan backports.

### Known gaps (seeded)

| Capability | Claude Code (py) | OpenCode (js) | Gap |
|---|---|---|---|
| Block raw secret in user prompt | `user_prompt_submit.py` (block) | none (masks only) | OpenCode lacks block guard |
| Mask tool output before model | `post_tool_use.py` (masks → persisted) | `messages.transform` (masks at dispatch) | timing/persistence differs |
| Unmask assistant text | `message_display.py` | `experimental.text.complete` | parity |
| Unmask tool args | `pre_tool_use.py` | `tool.execute.before` | parity |
| Compaction handling | none | `experimental.session.compacting` + V2 compaction | OpenCode ahead |
| V2 session hooks | n/a | `v2.js` | OpenCode-only |

### Plan

1. Build full parity matrix (hook-by-hook + vault/patterns differences).
2. Per gap: backport, add, or document intentional.
3. Open question: should OpenCode gain a `UserPromptSubmit`-style **block** mode (matching Claude Code), independent of `maskUi`? Decide whether to unify.

---

## Open questions

1. Field name `maskUi` acceptable? (default `false`)
2. Config surface: investigate first, pick env var vs config file vs other.
3. `maskUi=true`: assistant output kept masked (no block) — confirmed.
