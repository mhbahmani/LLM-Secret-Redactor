function createRevealController({ request, timeoutMs = 10_000, setTimer = setTimeout, clearTimer = clearTimeout }) {
  let timer = null;
  let visible = false;
  let hideCurrent = null;

  function hide() {
    if (timer) clearTimer(timer);
    timer = null;
    visible = false;
    const callback = hideCurrent;
    hideCurrent = null;
    if (callback) callback();
  }

  async function toggle({ sessionID, selectedText, confirm, show, onEmpty, onSelectionEmpty }) {
    if (visible) {
      hide();
      return { status: "hidden" };
    }

    const available = await request({ operation: "tokens", session: sessionID });
    if (!available.tokens?.length) {
      if (onEmpty) onEmpty();
      return { status: "empty" };
    }

    const hasSelection = typeof selectedText === "string" && selectedText.length > 0;
    let tokens = hasSelection
      ? available.tokens.filter((token) => selectedText.includes(token))
      : available.tokens;
    if (hasSelection && !tokens.length) {
      if (onSelectionEmpty) onSelectionEmpty();
      return { status: "selection-empty" };
    }

    const approved = await confirm(tokens.length, available.tokens.length);
    if (!approved) return { status: "cancelled" };

    const revealed = await request({ operation: "reveal", session: sessionID, tokens });
    visible = true;
    hideCurrent = () => show(null);
    show(revealed.mappings);
    timer = setTimer(hide, timeoutMs);
    return { status: "revealed", count: Object.keys(revealed.mappings).length };
  }

  return { toggle, hide, isVisible: () => visible };
}

module.exports = { createRevealController };
