# Current limitations

Status as of: 2026-09-24.

## 1. OpenCode reveal uses a dialog

OpenCode exposes terminal selection and local dialogs to TUI plugins, but does
not expose a supported override for built-in transcript rendering. Revealed
values therefore appear in a temporary dialog for 10 seconds. The stored
transcript remains redacted.

## 2. LiteLLM may replace `__MASKED_*__` with `REDACTED`

In the litellm provider logs, the user message arrives redacted at the wording
level:

```
{'role': 'user', 'content': 'Read .env.test and REDACTED'}
```

Expected/wanted on the wire instead:

```
{'role': 'user', 'content': '... DB_PASSWORD=__MASKED_SECRET_0B6FA859__ ...'}
```

- The `DB_PASSWORD=<value>` part gets summarised to the single word `REDACTED`
  by litellm (its own redaction), instead of being passed through as a
  `__MASKED_*__` token that our plugin intentionally placed there.
- Our masking regex runs first (real secret → `__MASKED_SECRET_*__`), but then
  litellm performs a second redaction pass that swallows even the mask token,
  destroying the secret-kind information and the per-session unmask reference.

## 3. Multiple masking layers can still interact

There are (at least) two redaction layers acting on the same message:

1. This plugin (`chat.message` and dispatch hooks, `__MASKED_*__` tokens).
2. The upstream provider (litellm) redacting on its own.

They do not coordinate. Our token is designed to be reversible per-session via
the memory broker; litellm's `REDACTED` is not. Net effect: the model sometimes sees
`REDACTED` (opaque) instead of a reversible mask token, and the user UI shows
mask tokens instead of the original.

## 4. Same-user compromise is out of scope

The socket directory and socket are private to the current OS user and mappings
are never written to disk. A process already running as that user can
still inspect process memory or attempt to communicate with local IPC, so this
is not a defense against a fully compromised account.
