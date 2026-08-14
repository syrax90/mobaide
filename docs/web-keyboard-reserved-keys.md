# Reserved keys in the web terminal

Which keyboard chords the browser or the window manager takes before the web
terminal can have them, where that table comes from, and how to replace its
documented guesses with measurements.

The table itself is `web/src/keyboard/reservedKeys.ts`. This document is its
bibliography and its runbook.

## Two facts, deliberately kept apart

Every row carries both, because they are independent:

| Field | Question | Where it comes from |
|---|---|---|
| `browserAction` | What does the chord do by default? | Vendor documentation. Always knowable. |
| `pageCanCancel` | Does `preventDefault()` on the page stop it? | Almost never documented. Starts at `'unknown'`. |

Conflating them is the mistake the split exists to prevent. Firefox delivers
`Ctrl+W` to the page *and* closes the tab anyway — so "the page saw the
keydown" proves nothing about whether the terminal gets to keep it, and a sweep
that only records delivery gets this whole class wrong.

`confidence` says which of the two states a row's `pageCanCancel` is in:
`'documented'` (inferred, never observed) or `'measured'` (someone ran the
sweep). **Every row ships at `'documented'`.** Promoting them is
[CLA-251](https://linear.app/claude-mobile/issue/CLA-251).

## Sources

| Group | Source |
|---|---|
| Firefox Ctrl+W / Ctrl+T / Ctrl+N are unsuppressable | [bugzilla 1291706](https://bugzilla.mozilla.org/show_bug.cgi?id=1291706) (WONTFIX), tracked under [bugzilla 1052569](https://bugzilla.mozilla.org/show_bug.cgi?id=1052569) |
| Chromium shortcuts | [Chrome keyboard shortcuts](https://support.google.com/chrome/answer/157179) |
| Firefox shortcuts | [Firefox keyboard shortcuts](https://support.mozilla.org/en-US/kb/keyboard-shortcuts-perform-firefox-tasks-quickly) |
| Safari shortcuts | [Safari keyboard and other shortcuts](https://support.apple.com/guide/safari/keyboard-and-other-shortcuts-cpsh003/mac) |
| macOS F-keys are media keys by default | [Use keyboard function keys on Mac](https://support.apple.com/guide/mac-help/use-keyboard-function-keys-mchlp2596/mac) |
| Windows window-management chords | [Keyboard shortcuts in Windows](https://support.microsoft.com/en-us/windows/keyboard-shortcuts-in-windows-dcc61a57-8ff0-cffe-9796-cb9706c75eec) |
| Keyboard Lock needs JS-initiated fullscreen; the announced Chrome 131 permission was **not** shipped | [Keyboard Lock API](https://developer.chrome.com/docs/capabilities/web-apis/keyboard-lock), [permission announcement and reversal](https://developer.chrome.com/blog/keyboard-lock-pointer-lock-permission), [WICG spec](https://wicg.github.io/keyboard-lock/) |

Only chords inside the shared token grammar are listed — at most two of
Ctrl/Alt/Shift over `a`–`z`, `0`–`9`, `F1`–`F12`, Tab, Enter, Space, Backspace.
Cmd chords are outside it entirely; the one that matters (Cmd+W in an installed
app) is covered by the PWA probe below rather than by a table row.

The rows that hurt most are `ctrl+l` and `ctrl+o`: both are **built-in
control-bar keys** (clear screen, transcript toggle), and on Windows and Linux
the browser takes them for the address bar and the file picker. That is the
single best argument for keeping the bar on a machine that has a real keyboard.

## Running the sweep

Requires a real browser window — Playwright's WebKit is not Safari and has no
browser chrome shortcuts at all, so nothing headless can measure this.

1. `cd web && npm run dev`, sign in, and open `/dev/key-sweep`. The route exists
   only in development builds (`import.meta.env.DEV`), so it is not reachable in
   production.
2. Use a throwaway window. Several chords under test close tabs, open private
   windows, or open DevTools.
3. The page lists every chord the table claims for *this* browser and OS. For
   each one: press it, then click **Page survived** or **Browser acted**.
   - "delivered" and "cancelable" are filled in automatically from the event.
   - `cancelable === false` is the closest thing to an automatic answer: the
     browser had already decided before the page ran.
4. Toggle **Call preventDefault() on every chord** off and repeat the chords that
   looked ambiguous. The interesting measurement is whether the browser acts
   *despite* the page fighting it.
5. Copy the JSON blob at the bottom into the matrix below, then update
   `reservedKeys.ts`: correct `browserAction`, set `pageCanCancel`, flip
   `confidence` to `'measured'`, and delete rows the sweep contradicts.
6. Record the browser version. Chord behaviour is not stable across majors, and
   a measurement without a version is a measurement with an expiry date you
   cannot see.

Priority order: Safari on macOS (least documented, most divergent), then Chrome
and Firefox on Windows, then the rest.

## The macOS PWA probe

Tier-1 widening rests on an unverified claim: that an installed PWA window hands
chords back to the page that a tab would not. Rows believed to be affected carry
`relaxedInStandalone`, and it only *softens* the warning — it never removes it.

To settle it: install the SPA on macOS, open a live terminal session, and press
**Cmd+W**. If the window closes, delete `relaxedInStandalone` from every row and
the branch that reads it. If the page survives and sees the keydown, the flag is
real and the affected rows can be measured properly.

Note that the SPA's manifest now carries a single 180px PNG icon, which clears
the known Chromium failure on an SVG-only `sizes: "any"` set but does not yet
meet the published 192 + 512 installability criteria; whether the install bar
appears at all is still unverified, and closing that properly is
[CLA-252](https://linear.app/claude-mobile/issue/CLA-252).

## Results

Nothing measured yet. Fill this in per browser/OS as the sweep runs.

| Browser | Version | OS | Date | Notes |
|---|---|---|---|---|
| _(pending)_ | | | | |

## Immersive mode

`navigator.keyboard.lock()` is the only mechanism that delivers Ctrl+W, Ctrl+T,
Ctrl+N and F11 to a web page at all. It is Chromium-only and requires
JavaScript-initiated fullscreen. The terminal's **Immersive** button is
feature-detected and simply absent in Firefox and Safari.

Chrome announced a permission prompt for Keyboard Lock and Pointer Lock in 131
and then **did not launch it** — the experiment found no clear benefit on scam
sites and frequent confusion on legitimate ones. Treat "requires permission" as
false; the `NotAllowedError` path still exists for other refusals.

`lock()` is called *before* `requestFullscreen()`, which is the WICG
recommendation. Note the trap if that permission ever does ship: awaiting the
prompt would likely consume the transient user activation `requestFullscreen()`
requires, and the first click would fail. With no argument `lock()` captures
every key, Escape included — leaving fullscreen then needs a press-and-hold,
which is the point: an Escape that exits browser chrome is an Escape the TUI
never received.
