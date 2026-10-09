# Future work

The memory broker and confirmed OpenCode reveal flow are implemented. Remaining improvements are intentionally separate changes:

- Add a Claude Code confirmation UI if its hook API gains a local interactive display surface. Claude currently restores values through `MessageDisplay`.
- Add native Windows IPC support. The current broker requires Unix-domain sockets; WSL is supported through its Linux environment.
- Add an optional allowlist for tools that may receive restored secrets. Today, masked values are restored for any local tool invocation that references an exact token from the same session.
- Investigate inline OpenCode transcript reveal if the public TUI API gains a supported transcript-render override. The current API supports local dialogs and selection access, but not rewriting built-in message rendering.
- Add OS-keyring-backed encrypted persistence only as an opt-in recovery feature. The default should remain memory-only.
