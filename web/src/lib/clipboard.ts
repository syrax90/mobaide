/**
 * Best-effort clipboard write.
 *
 * Never rejects. Chromium grants `clipboard-write` to the focused document, but
 * Firefox and Safari refuse a write with no user gesture behind it, and an
 * unhandled rejection would surface somewhere unhelpful — inside xterm's parser
 * promise chain for the OSC 52 caller, or as an uncaught error in a click
 * handler for the copy buttons on the agent-credentials panel.
 *
 * Lives here rather than in `src/terminal/` because both consumers are real:
 * the terminal is the SPA's only lazy route, and the pairing screens must not
 * drag it into the main chunk to copy a token.
 */
export async function writeClipboardText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
