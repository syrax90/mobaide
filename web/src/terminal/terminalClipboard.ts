/**
 * Clipboard policy for the terminal, including OSC 52.
 *
 * Live writes are allowed, reads are refused — the same asymmetry both mobile
 * clients ship (`TerminalSurfaceView.onCopyToClipboard` /
 * `TerminalHostView.clipboardCopy`; Android never answers the query form, iOS
 * refuses it explicitly in `clipboardRead`). A program that can *read* the
 * clipboard can exfiltrate whatever the user copied last, which on a shared
 * machine is routinely a password.
 *
 * *Replayed* writes are refused too. The agent replays its ring verbatim on every
 * attach, so a single OSC 52 left in that history — one vim/tmux yank — re-copies
 * on every session open, which reads as the app hijacking the clipboard for no
 * reason. All three clients gate this. The attribution rule they share, and the
 * one boundary case it does not cover, are stated once in `docs/web-client.md`'s
 * OSC 52 section — do not restate the guarantee here, where the caveat would be
 * easy to miss.
 *
 * Refusing has to happen ahead of `ClipboardAddon`, not inside the provider:
 * the addon replies to a query unconditionally, so a provider returning `''`
 * still puts an `ESC]52;c;<empty>` answer on the PTY. xterm dispatches OSC
 * handlers **last-registered-first** and stops at the first one returning
 * `true`, so registering this after `loadAddon` swallows the query outright.
 *
 * Web has no analogue of Android's `ClipDescription.EXTRA_IS_SENSITIVE` or iOS's
 * `localOnly` + 60 s expiry, so a terminal clipboard write lands in the ordinary
 * system clipboard and in whatever history the OS keeps. Documented divergence.
 */
import type { IDisposable, Terminal } from '@xterm/xterm';
import type { IClipboardProvider } from '@xterm/addon-clipboard';
import { writeClipboardText } from '../lib/clipboard';

/** OSC 52's `Pc` value for the system clipboard. */
const SYSTEM_SELECTION = 'c';

/**
 * Allows OSC 52 writes, reports nothing back.
 *
 * The selection parameter is typed as a plain `string` rather than the addon's
 * `ClipboardSelectionType`: that is an *ambient* `const enum`, which
 * `isolatedModules` forbids naming a member of. Method parameters are bivariant,
 * so the wider type still satisfies the interface.
 */
export class TerminalClipboardProvider implements IClipboardProvider {
  readText(): string {
    return '';
  }

  writeText(selection: string, text: string): void {
    // A browser has only the system clipboard. X11's primary selection (`p`)
    // has no analogue, so a program targeting it is ignored rather than having
    // its bytes land somewhere the user never asked for.
    if (selection !== SYSTEM_SELECTION) return;
    // Deliberately not returned. `ClipboardAddon` hands a returned promise to
    // xterm's OSC dispatcher, which pauses the parser until it settles — and a
    // paused parser fires no write callbacks, which is what releases
    // `pendingReplayWrites`, which is what opens the send gate. A program that
    // packs the agent's 4 MiB replay ring with OSC 52 writes could therefore
    // freeze both the screen and the keyboard after every reconnect, with no
    // way for the user to type the poisoned bytes back out of the ring.
    // `writeClipboardText` swallows every rejection, so nothing is lost by not
    // awaiting; the cost is that concurrent writes may land out of order, which
    // is only observable in exactly the pathological case being defended
    // against.
    void writeClipboardText(text);
  }
}

/** True for the OSC 52 query form (`Pd` is `?`). */
export function isOsc52Read(data: string): boolean {
  return data.split(';')[1] === '?';
}

/**
 * Swallow the OSC 52 forms this app refuses, before `ClipboardAddon` can act on
 * them: every write `isReplaying` attributes to replayed history, and the query
 * form otherwise. Register **after** the addon — later registration means
 * earlier dispatch.
 *
 * `isReplaying` is tested first because it is two field reads while
 * `isOsc52Read` splits the payload, and xterm accumulates an OSC body up to its
 * 10 MB `PAYLOAD_LIMIT` before dispatching. Both operands are pure, so `||`
 * commutes; this just avoids allocating a copy of a payload about to be dropped.
 * Matches the order iOS already ships, where the replay guard precedes the UTF-8
 * decode.
 */
export function installOsc52Policy(
  terminal: Terminal,
  isReplaying: () => boolean,
): IDisposable {
  return terminal.parser.registerOscHandler(52, (data) => isReplaying() || isOsc52Read(data));
}
