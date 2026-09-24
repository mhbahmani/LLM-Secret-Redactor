# Current Problems

Status as of: 2026-09-15.

## 1. User's own prompt is masked in the UI

When a secret is typed into the opencode input box, the plugin masks the user
message at ingestion (`chat.message` hook). Consequence:

- The model sees the mask token (good).
- But the message echoed back to the user in the terminal / UI also shows the
  mask token, e.g.:
  `DB_PASSWORD=__MASKED_SECRET_0B6FA859__`
  instead of the actual secret the user typed.

User preference: the user's own message on screen should show the real secret,
not the mask. Only the model should see the masked form. The echo to the user
does not appear to be restored by the display/restore hooks
(`experimental.text.complete` only restores assistant output, not the user's
own message echo).

## 2. litellm does not pass `__MASKED_*__` through — it sends `REDACTED`

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

## 4. Two masking layers fight each other

There are (at least) two redaction layers acting on the same message:

1. Our plugin (`chat.message` → `maskText`, `__MASKED_*__` tokens).
2. The upstream provider (litellm) redacting on its own.

They do not coordinate. Our token is designed to be reversible per-session via
the vault; litellm's `REDACTED` is not. Net effect: the model sometimes sees
`REDACTED` (opaque) instead of a reversible mask token, and the user UI shows
mask tokens instead of the original.

## Open design questions

- Should the plugin mask the user's own message at all, or only the copy that
  goes to the model (keep the DB copy / UI echo unmasked)?
- Should the mask token format be made opaque enough to survive the upstream
  provider's redaction heuristics (e.g. not shaped like a secret at all)?
- How should the mock + real provider interplay terminate the tool call loop?
