// Switches secret masking for the current session from the TUI. Only the
// user can reach the command palette, so the model cannot do this itself.
async function toggleMasking({ sessionID, status, setMasking, confirm, notify }) {
  if (await status(sessionID)) {
    const approved = await confirm();
    if (!approved) return { status: "cancelled" };
    await setMasking(sessionID, false);
    notify("Secret masking is OFF for this session. Run the command again to turn it back on.", "warning");
    return { status: "off" };
  }
  await setMasking(sessionID, true);
  notify("Secret masking is ON for this session.", "success");
  return { status: "on" };
}

export { toggleMasking };
