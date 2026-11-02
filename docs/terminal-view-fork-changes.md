# Vendored Termux Fork: TerminalView changes

The Android client embeds the Termux `terminal-view` and `terminal-emulator` modules
(see `android/terminal-view/` and `android/terminal-emulator/`) as a vendored copy. Any
local edit to that Java is recorded here so that an upstream rebase can re-apply each
change accurately. Keep this file as the single source of truth for fork drift —
do not rely on git blame alone.

## Edits

Entries are listed oldest first.

### 2026-04-25: `TerminalView.java::updateSize`

- **File:method:** `terminal-view/.../TerminalView.java::updateSize`
- **Upstream link:** [upstream](https://github.com/termux/termux-app/blob/master/terminal-view/src/main/java/com/termux/view/TerminalView.java)

**What changed.** Guarded `mTopRow = 0; scrollTo(0, 0);` with `if (mEmulator == null || !mEmulator.isAutoScrollDisabled())`. `invalidate()` still always runs.

**Why.** CLA-41: preserve scroll position through grid resizes when the user has paused follow-mode. Unpaused users still snap to bottom. The IME pans the Compose host instead of resizing the grid (see [`ImeFloatingPan`](../android/app/src/main/java/com/mobaide/app/ui/terminal/ImeFloatingPan.kt)), so `updateSize` now fires only on rotation, composer show/hide, and display-mode changes.

### 2026-04-25: `TerminalView.java::attachSession`

- **File:method:** `terminal-view/.../TerminalView.java::attachSession`
- **Upstream link:** [upstream](https://github.com/termux/termux-app/blob/master/terminal-view/src/main/java/com/termux/view/TerminalView.java)

**What changed.** Guarded `mTopRow = 0` with `if (mEmulator == null || !mEmulator.isAutoScrollDisabled())`.

**Why.** CLA-41: defense-in-depth for future re-attach flows. New sessions start with `isAutoScrollDisabled() == false`, so the reset still fires.

### 2026-04-25: `TerminalView.java::doScroll`

- **File:method:** `terminal-view/.../TerminalView.java::doScroll`
- **Upstream link:** [upstream](https://github.com/termux/termux-app/blob/master/terminal-view/src/main/java/com/termux/view/TerminalView.java)

**What changed.** Added `mClient.onScrollPositionChanged(mTopRow);` as the last line of the method.

**Why.** CLA-41: lets the Compose host observe drag/fling settle so it can flip `followMode`. Do NOT also fire from `setTopRow(int)` — that path is used by the host to programmatically jump-to-bottom and would create a feedback loop.

### 2026-04-25: `TerminalViewClient.java` (`onScrollPositionChanged`)

- **File:method:** `terminal-view/.../TerminalViewClient.java`
- **Upstream link:** [upstream](https://github.com/termux/termux-app/blob/master/terminal-view/src/main/java/com/termux/view/TerminalViewClient.java)

**What changed.** Added `default void onScrollPositionChanged(int topRow) {}` to the interface.

**Why.** CLA-41: Java→Kotlin scroll-position bridge. Default body keeps existing implementors source-/binary-compatible.

### 2026-04-30: `TerminalView.java::onDoubleTap`

- **File:method:** `terminal-view/.../TerminalView.java::onDoubleTap` + `dispatchInputRowGesture`
- **Upstream link:** [upstream](https://github.com/termux/termux-app/blob/master/terminal-view/src/main/java/com/termux/view/TerminalView.java)

**What changed.** Replaced the no-op `onDoubleTap` body with a call to a new private helper `dispatchInputRowGesture(event, copyToInput=true)` that runs the same row→message resolution as `onSingleTapUp` (via `findInputMessage`) and dispatches `mClient.onTerminalRowCopyToInput`. Refactored `onSingleTapUp` to share the same helper with `copyToInput=false`.

**Why.** Double-tap a `❯ `-prefixed row populates the InputBar without submitting (single-tap still submits — **narrowed to the plain-shell arm on 2026-07-31, see the entry below; the double-tap half stays available in every mode**). Pinch-to-zoom unaffected — quick-scale already disabled in `GestureAndScaleRecognizer`.

### 2026-04-30: `TerminalViewClient.java` (`onTerminalRowCopyToInput`)

- **File:method:** `terminal-view/.../TerminalViewClient.java`
- **Upstream link:** [upstream](https://github.com/termux/termux-app/blob/master/terminal-view/src/main/java/com/termux/view/TerminalViewClient.java)

**What changed.** Added `default void onTerminalRowCopyToInput(String text, boolean isCurrentInput) {}` to the interface.

**Why.** Pairs with the `onDoubleTap` change above. Default body keeps existing implementors source-/binary-compatible.

### 2026-05-06: `TerminalView.java` (forced-columns field and methods)

- **File:method:** `terminal-view/.../TerminalView.java` (field + new methods)
- **Upstream link:** [upstream](https://github.com/termux/termux-app/blob/master/terminal-view/src/main/java/com/termux/view/TerminalView.java)

**What changed.** Added `private int mForcedColumns`, `setForcedColumns(int)`, `getForcedColumns()`, `getRendererFontWidth()`, `getRendererFontLineSpacing()`, and a private `applyForcedColumnsLayoutWidth()` helper. Also added `import android.view.ViewGroup`.

**Why.** CLA-49: Static-column display mode needs to override `viewWidth / mFontWidth` and to expose font metrics so the Compose host can size `LayoutParams.width` to `forcedColumns * mFontWidth`.

### 2026-05-06: `TerminalView.java::setTextSize`

- **File:method:** `terminal-view/.../TerminalView.java::setTextSize` + `setTypeface`
- **Upstream link:** [upstream](https://github.com/termux/termux-app/blob/master/terminal-view/src/main/java/com/termux/view/TerminalView.java)

**What changed.** After creating the new renderer, call `applyForcedColumnsLayoutWidth()` before `updateSize()`. When `mForcedColumns > 0`, this resizes `LayoutParams.width` to `mForcedColumns * mRenderer.mFontWidth`.

**Why.** CLA-49: pinch-to-zoom (and any external `setTextSize`/`setTypeface` call) keeps the column count fixed but rescales the surface width so the visible text fills the user's chosen column count.

### 2026-05-06: `TerminalView.java::updateSize`

- **File:method:** `terminal-view/.../TerminalView.java::updateSize`
- **Upstream link:** [upstream](https://github.com/termux/termux-app/blob/master/terminal-view/src/main/java/com/termux/view/TerminalView.java)

**What changed.** (a) Early-return if `mRenderer == null || mRenderer.mFontWidth <= 0f || mRenderer.mFontLineSpacing <= 0`. (b) When `mForcedColumns > 0`, use it as the column count instead of `viewWidth / mFontWidth`.

**Why.** CLA-49: zero-dim crash guard during the brief `AndroidView` recreate window (Termux issues [#4346](https://github.com/termux/termux-app/issues/4346) and [#3541](https://github.com/termux/termux-app/issues/3541)) + Static-column mode hook.

### 2026-05-23: `TerminalView.java` (gesture listener `onScroll`)

- **File:method:** `terminal-view/.../TerminalView.java` gesture listener `onScroll` (constructor)
- **Upstream link:** [upstream](https://github.com/termux/termux-app/blob/master/terminal-view/src/main/java/com/termux/view/TerminalView.java)

**What changed.** On the first `onScroll` of a finger gesture (`!scrolledWithFinger`) with vertically-dominant motion (`abs(distanceY) >= abs(distanceX)`), call `getParent().requestDisallowInterceptTouchEvent(true)` before updating `scrolledWithFinger`/`mScrollRemainder`.

**Why.** Scrolling-fix: lets the new `TerminalHorizontalScrollView` parent cooperate with vertical scroll. Without it, horizontal finger drift past the parent's touch slop steals the gesture mid-scroll, killing vertical scroll dead. Direction-aware so horizontal-dominant pans (Static display mode) still reach the HSV.

### 2026-05-23: `TerminalView.java::onMeasure`

- **File:method:** `terminal-view/.../TerminalView.java::onMeasure` (new override)
- **Upstream link:** [upstream](https://github.com/termux/termux-app/blob/master/terminal-view/src/main/java/com/termux/view/TerminalView.java)

**What changed.** Added an `onMeasure` override: when the width spec is `UNSPECIFIED`, set `measuredWidth = lp.width` if `lp.width > 0`, else `getSuggestedMinimumWidth()`. For `EXACTLY`/`AT_MOST` the default `View.onMeasure` behavior is preserved (`getDefaultSize`).

**Why.** Scrolling-fix v2: `HorizontalScrollView.measureChildWithMargins` passes `UNSPECIFIED` to its child, expecting the child to declare its own intrinsic width. The default `View.onMeasure` returns 0 for UNSPECIFIED (no background, no `minimumWidth`), and HSV's `fillViewport=true` then re-measures with `EXACTLY = viewport width`, collapsing Static-column mode's surface and defeating horizontal panning.

The override gives `TerminalView` an intrinsic width based on its requested `LayoutParams.width` so `hasHorizontalOverflow()` in [`TerminalHorizontalScrollView`](../android/app/src/main/java/com/mobaide/app/ui/terminal/TerminalHorizontalScrollView.kt) actually triggers in Static mode. The Compose `AndroidView` measurement path uses `EXACTLY`/`AT_MOST`, so Full display mode is unaffected.

### 2026-05-23: `TextSelectionCursorController.java::setActionModeCallBacks`

- **File:method:** `terminal-view/.../textselection/TextSelectionCursorController.java::setActionModeCallBacks` + `onActionItemClicked`
- **Upstream link:** [upstream](https://github.com/termux/termux-app/blob/master/terminal-view/src/main/java/com/termux/view/textselection/TextSelectionCursorController.java)

**What changed.** Added `public final int ACTION_SEND_TO_INPUT = 4;`, a leading `menu.add(Menu.NONE, ACTION_SEND_TO_INPUT, Menu.NONE, R.string.send_to_input)` item in `onCreateActionMode` (before Copy), and an `ACTION_SEND_TO_INPUT` case in `onActionItemClicked` calling `terminalView.mClient.onCopySelectionToInput(getSelectedText())` then `stopTextSelectionMode()`.

**Why.** CLA-63: the long-press selection toolbar gains a leading "Send to Input" action that drops the selected text into the InputBar for editing (no submit, no clipboard write). Reuses the existing `ActionMode.Callback2`; the pre-existing `onGetContentRect` already anchors the toolbar through scroll.

### 2026-05-23: `TerminalViewClient.java` (`onCopySelectionToInput`)

- **File:method:** `terminal-view/.../TerminalViewClient.java`
- **Upstream link:** [upstream](https://github.com/termux/termux-app/blob/master/terminal-view/src/main/java/com/termux/view/TerminalViewClient.java)

**What changed.** Added `default void onCopySelectionToInput(String text) {}` to the interface.

**Why.** CLA-63: view-client seam for the "Send to Input" item, mirroring `onTerminalRowCopyToInput`. Default body keeps existing implementors source-/binary-compatible.

### 2026-05-23: `strings.xml` (`send_to_input`)

- **File:method:** `terminal-view/.../res/values/strings.xml`
- **Upstream link:** [upstream](https://github.com/termux/termux-app/blob/master/terminal-view/src/main/res/values/strings.xml)

**What changed.** Added `<string name="send_to_input">Send to Input</string>`.

**Why.** CLA-63: label for the new "Send to Input" selection-toolbar item, referenced via `R.string.send_to_input`.

### 2026-07-09: `TerminalEmulator.java` (`isBracketedPasteMode`)

- **File:method:** `terminal-emulator/.../TerminalEmulator.java` (new method)
- **Upstream link:** [upstream](https://github.com/termux/termux-app/blob/master/terminal-emulator/src/main/java/com/termux/terminal/TerminalEmulator.java)

**What changed.** Added `public boolean isBracketedPasteMode()` — a read accessor for `DECSET_BIT_BRACKETED_PASTE_MODE` (DECSET 2004), next to the existing mode accessors (`isKeypadApplicationMode` etc.).

**Why.** CLA-123: the client-side "terminal wants input" heuristic reads DECTCEM (`isCursorEnabled()`, already public) AND bracketed-paste state; the 2004 bit had no public accessor (`isDecsetInternalBitSet` is private).

### 2026-07-09: `TerminalViewClient.java` (tap-to-raise hooks)

- **File:method:** `terminal-view/.../TerminalViewClient.java`
- **Upstream link:** [upstream](https://github.com/termux/termux-app/blob/master/terminal-view/src/main/java/com/termux/view/TerminalViewClient.java)

**What changed.** Added `default boolean onTerminalTapToRaiseKeyboard() { return false; }` (query + fires the raise) and `default boolean isTapToRaiseKeyboardActive() { return false; }` (side-effect-free query for the same gate) to the interface.

**Why.** Keyboard-UX fix (CLA-123 follow-up): view-client seam for composer-hidden tap-to-raise. The separate side-effect-free query exists for the `onUp` mouse-tracking fast path (next rows), which must consult the gate without firing the raise. Default bodies keep existing implementors source-/binary-compatible. **Dormant since the explicit-keyboard-only change (2026-07-10, PR #375): the app's client hard-returns `false` from both hooks, so no tap ever raises the IME — the seam and the two rows below stay in the fork for rebase fidelity and possible re-enable.**

### 2026-07-09: `TerminalView.java::onSingleTapUp`

- **File:method:** `terminal-view/.../TerminalView.java::onSingleTapUp` (gesture listener)
- **Upstream link:** [upstream](https://github.com/termux/termux-app/blob/master/terminal-view/src/main/java/com/termux/view/TerminalView.java)

**What changed.** After `mClient.onSingleTapUp(event)` and before `dispatchInputRowGesture(...)`, added `if (mClient.onTerminalTapToRaiseKeyboard()) return true;`.

**Why.** Keyboard-UX fix (dormant since PR #375 — see the TerminalViewClient entry above): while the composer is hidden the terminal is the input field — a single tap raises the IME (the host does the `showSoftInput`) and is fully consumed, so tapping the TUI's own `❯` prompt row while hunting for the keyboard can never fire the row-tap submit (bare CR / execute frame). Selection dismissal keeps priority (the gate sits after the `isSelectingText()` branch); double-tap copy and long-press selection are unaffected.

### 2026-07-09: `TerminalView.java::onUp`

- **File:method:** `terminal-view/.../TerminalView.java::onUp` (gesture listener)
- **Upstream link:** [upstream](https://github.com/termux/termux-app/blob/master/terminal-view/src/main/java/com/termux/view/TerminalView.java)

**What changed.** Added `&& !mClient.isTapToRaiseKeyboardActive()` to the mouse-tracking fast-path condition.

**Why.** Keyboard-UX fix (Codex r3555387824; dormant since PR #375 — see the TerminalViewClient entry above): the fast path sends a mouse press/release to the PTY on ACTION_UP — immediately, long before the delayed confirmed single tap reaches the `onSingleTapUp` raise gate — so in a mouse-tracking TUI a raise-only tap would also click the remote UI. With the gate active the fast path is skipped and the gesture falls through to the normal tap resolution, which the raise gate consumes.

Uses the side-effect-free query so `onUp` never fires the raise itself (double-tap/scroll gestures also pass through `onUp`).

### 2026-07-09: `TerminalEmulator.java` (DECSET 2026 synchronized output)

- **File:method:** `terminal-emulator/.../TerminalEmulator.java` (DECSET table + accessors)
- **Upstream link:** [upstream](https://github.com/termux/termux-app/blob/master/terminal-emulator/src/main/java/com/termux/terminal/TerminalEmulator.java)

**What changed.** Added `DECSET_BIT_SYNCHRONIZED_OUTPUT = 1 << 13`, `case 2026` in `mapDecSetBitToInternalBit`, a `case 2026` in `doDecSetOrReset` that increments a new `mSynchronizedOutputResets` counter on reset (otherwise bit set/reset is the whole emulator-side behavior), and public accessors `isSynchronizedOutputActive()` / `getSynchronizedOutputResets()`. Upstream has no mode-2026 support.

**Why.** Keyboard-UX fix: synchronized output (BSU/ESU). Mapping the bit makes the existing generic DECRQM handler answer `?2026;1|2$y` instead of `0`, which flips Claude Code/Ink's sync-output auto-detection on; `CSI ?2026 s/r` save/restore and `reset()` clearing come free via the generic DECSET paths. The reset counter lets the session detect a frame that completed mid-chunk (Codex r3555172725). Render deferral lives in `TerminalSession.emitOutput` (next row).

### 2026-07-09: `TerminalSession.java::emitOutput` (sync-output deferral)

- **File:method:** `terminal-emulator/.../TerminalSession.java::emitOutput` (+ fields)
- **Upstream link:** [upstream](https://github.com/termux/termux-app/blob/master/terminal-emulator/src/main/java/com/termux/terminal/TerminalSession.java)

**What changed.** While `isSynchronizedOutputActive()` — and no ESU landed inside the appended chunk (`getSynchronizedOutputResets()` unchanged across the append) — `emitOutput` skips `notifyScreenUpdate()` and arms a leading-edge 150 ms flush timer (`SYNC_UPDATE_TIMEOUT_MS`, main-thread Handler). A chunk containing an ESU (even with a following BSU re-activating the mode — coalesced ESU..BSU writes) cancels the timer and notifies immediately, as does any non-sync append.

Timeout forces a paint but leaves the mode bit set (per spec). Note: this file is already a fully rewritten remote-PTY fork (CLA-14), so "upstream" is a loose reference.

**Why.** Keyboard-UX fix: defers only the `onTextChanged` fan-out (invalidate + mode sampling) so a multi-write Ink frame (Claude Code `/`-suggestion popup) paints atomically at the ESU boundary instead of tearing — including when the next frame's BSU rides in the same network chunk (Codex r3555172725). Parsing always continues; `scrollCounter` accumulates across suppressed notifies and is consumed once at the flush. Leading-edge timer keeps a program streaming inside a long sync window painting every 150 ms.

### 2026-07-10: `TerminalViewClient.java` (`canPasteFromClipboard`)

- **File:method:** `terminal-view/.../TerminalViewClient.java`
- **Upstream link:** [upstream](https://github.com/termux/termux-app/blob/master/terminal-view/src/main/java/com/termux/view/TerminalViewClient.java)

**What changed.** Added `default boolean canPasteFromClipboard() { return false; }` to the interface.

**Why.** CLA-132: view-client gate for the selection-toolbar "Paste" item, consulted once at menu build. The contract forbids reading clipboard *contents* in implementations (that fires the Android 12+ access toast on merely opening the menu) — description-level checks only. Default body keeps existing implementors source-/binary-compatible and hides the item.

### 2026-07-10: `TextSelectionCursorController.java::setActionModeCallBacks`

- **File:method:** `terminal-view/.../textselection/TextSelectionCursorController.java::setActionModeCallBacks` + `onActionItemClicked`
- **Upstream link:** [upstream](https://github.com/termux/termux-app/blob/master/terminal-view/src/main/java/com/termux/view/textselection/TextSelectionCursorController.java)

**What changed.** Re-added `public final int ACTION_PASTE = 2;` (upstream id), a trailing Paste `menu.add(...)` in `onCreateActionMode` gated behind `terminalView.mClient.canPasteFromClipboard()` (item hidden when false), and the upstream `ACTION_PASTE` case in `onActionItemClicked`: `stopTextSelectionMode()` then `terminalView.mTermSession.onPasteTextFromClipboard()`.

**Why.** CLA-132: restores the Paste item removed in 979f0a9 (then a dead end) now that the host implements the sink — the session callback reads the clipboard and routes it through `TerminalEmulator.paste()`, which strips ESC/C1, converts LF→CR, and applies the single canonical bracketed-paste wrap; the resulting `mSession.write` bytes inherit the app's Live+running input gate. Menu-time gating never reads clip contents (no Android 12+ toast until the user actually taps Paste).

### 2026-07-10: `strings.xml` (`paste_text`)

- **File:method:** `terminal-view/.../res/values/strings.xml`
- **Upstream link:** [upstream](https://github.com/termux/termux-app/blob/master/terminal-view/src/main/res/values/strings.xml)

**What changed.** Re-added `<string name="paste_text">Paste</string>` (upstream string, removed in 979f0a9).

**Why.** CLA-132: label for the restored Paste selection-toolbar item, referenced via `R.string.paste_text`.

### 2026-07-31: `TerminalView.java::onUp` (`programOwnsPointer`)

- **File:method:** `terminal-view/.../TerminalView.java::onUp` + `::onSingleTapUp` (gesture listener) + new private `programOwnsPointer`
- **Upstream link:** [upstream](https://github.com/termux/termux-app/blob/master/terminal-view/src/main/java/com/termux/view/TerminalView.java)

**What changed.** Added `private boolean programOwnsPointer()` (`isMouseTrackingActive() || isAlternateBufferActive()`, the same pair `doScroll` ladders on), a `boolean pointerOwnedAtUp` field on the gesture listener latched at the top of `onUp` (before its mouse fast-path early return), and, in `onSingleTapUp` after the tap-to-raise gate, `if (pointerOwnedAtUp || programOwnsPointer()) { requestFocus(); return true; }` — skipping `dispatchInputRowGesture` while keeping the focus behaviour of the existing "no row resolved" fallback.

`onDoubleTap`'s copy-to-InputBar dispatch is deliberately **not** gated. The latch is why both evaluations must agree: the click leaves on ACTION_UP but the row tap only arrives at `onSingleTapConfirmed` ~300 ms later, and the emulator state the gate reads is written by the PTY program — which the click itself signals.

Without it a program could answer the click by dropping mouse mode and repainting the tapped row as a prompt, turning the delayed tap into an `x` execute frame (Ctrl+C, retype, CR) whose text it chose. `onSingleTapConfirmed` always follows an ACTION_UP, and a long press or scroll suppresses the confirmed tap entirely, so the latch is never read stale.

**Why.** Tapping Claude Code's prompt box submitted the half-typed message. `findInputMessage` matches the `❯`+NBSP prefix Ink draws and walks 20 rows up, so nearly any tap in the prompt box resolved to the live input and `onTerminalRowTapped` sent a bare CR. The click the user actually wanted was already being sent: `onUp`'s mouse fast path fires on ACTION_UP, but the recognizer discards its `true` return, so the confirmed single tap still ran ~300 ms later.

Row-tap submit is a shell affordance — a mouse-tracking program positions its own cursor from the click, and on a bare alternate screen (`less`, `man`) the rows are program chrome whose "re-execute" path would Ctrl+C the program and retype the text. Double-tap-to-composer stays available in every mode as the replacement affordance for re-running a message.

### 2026-08-01: `TerminalEmulator.java::scrollDownOneLine`

- **File:method:** `terminal-emulator/.../TerminalEmulator.java::scrollDownOneLine` (+ `mScrollCounter` field javadoc)
- **Upstream link:** [upstream](https://github.com/termux/termux-app/blob/master/terminal-emulator/src/main/java/com/termux/terminal/TerminalEmulator.java)

**What changed.** Moved `mScrollCounter++` from the top of the method into the `else` branch — the `mScreen.scrollDownOneLine(mTopMargin, mBottomMargin, currentStyle)` path. The horizontal-margin branch above it (`mLeftMargin != 0 || mRightMargin != mColumns`) no longer counts. Field javadoc restated in terms of the screen window rotating over the buffer rather than of scroll calls, naming both consumers and noting that the alternate screen rotates with no transcript to grow.

**Why.** CLA-183: the counter has two consumers and the horizontal-margin branch (DECLRMM) was lying to both.

It `blockCopy`s within the margin columns and `blockSet`s the bottom row — no `mScreenFirstRow` rotation, no `mActiveTranscriptRows++` — yet it inflated the host's jump-to-bottom unread badge (`TerminalViewModel.sessionCallback.onScreenUpdated`) and made `TerminalView.onScreenUpdated` shift `mTopRow` and `decrementYTextSelectionCursors`, sliding a paused viewport that should have stayed put.

The `mTopMargin`/DECSTBM case deliberately keeps counting: `TerminalBuffer.scrollDownOneLine` rotates `mScreenFirstRow` and grows `mActiveTranscriptRows`, and `blockCopyLinesDown` parks the scrolled-out region row as the newest transcript row — so those rows really are reachable.

iOS mirrors the same rule over SwiftTerm, whose `scrollTop != 0` arm shifts in place instead (`TerminalFollowScroll.pushesRowIntoScrollback`), so the two clients agree on the rule but not on the DECSTBM count.

### 2026-08-01: `TerminalSession.java` (`isScreenUpdateDeferred`)

- **File:method:** `terminal-emulator/.../TerminalSession.java` (new `isScreenUpdateDeferred`)
- **Upstream link:** [upstream](https://github.com/termux/termux-app/blob/master/terminal-emulator/src/main/java/com/termux/terminal/TerminalSession.java)

**What changed.** Added `public boolean isScreenUpdateDeferred()` returning the existing `mSyncFlushPending` field set by the `emitOutput` sync-output deferral (see the 2026-07-09 `emitOutput` entry above). No behaviour change — a read-only accessor for the view. Main thread only.

**Why.** CLA-182: the view needs to know when the screen holds bytes it has parsed but deliberately not painted. Deliberately **not** `isSynchronizedOutputActive()`, which the emulator never clears on a timeout — a program that sets `ESC[?2026h` and never resets it would disable row taps permanently, whereas this flag self-clears at the 150 ms safety flush, which is exactly as long as the drawn pixels can lag the screen.

### 2026-08-01: `TerminalView.java::dispatchInputRowGesture`

- **File:method:** `terminal-view/.../TerminalView.java::dispatchInputRowGesture` + `::onUp` (gesture listener) + new private `screenUpdateDeferred`/`mScreenUpdateDeferredAtUp`
- **Upstream link:** [upstream](https://github.com/termux/termux-app/blob/master/terminal-view/src/main/java/com/termux/view/TerminalView.java)

**What changed.** Added a private `screenUpdateDeferred()` helper (`mTermSession != null && mTermSession.isScreenUpdateDeferred()`), a `boolean mScreenUpdateDeferredAtUp` field latched in `onUp` beside `pointerOwnedAtUp`, and `if (mScreenUpdateDeferredAtUp || screenUpdateDeferred()) return false;` as the first statement of `dispatchInputRowGesture` — the same "no row resolved" value the empty-`inputMessage` path already uses, so `onSingleTapUp` falls through to `requestFocus()`.

Gating the shared helper covers single-tap submit and double-tap copy-to-InputBar alike. **The latch is the load-bearing half** (Codex r3695765439): `SYNC_UPDATE_TIMEOUT_MS` is 150 ms but `onSingleTapConfirmed` delivers the row tap ~300 ms after touch-down, so the safety flush lands *between* them — by dispatch time the swapped-in row is painted and the dispatch-time check alone waves it through.

**Why.** CLA-182: closes the row hit-testing half of synchronized output.

`TerminalRenderer.render` and `findInputMessage` read the same `mEmulator.getScreen()`, so unlike iOS there is no frozen second buffer — but while a paint is deferred the *drawn* rows are older than the screen, and a program can duty-cycle inside that window (write a hostile row, restore the benign one just before each flush) so the display only ever paints the benign text while the resolved row is the hostile one.

A tap then executes an `x` frame (Ctrl+C, retype, CR) the user never saw. iOS parity: `MobaideTerminalView.resolveRow` refuses the same way via `TerminalSynchronizedOutput`.

### 2026-08-03: `TerminalSession.java::emitOutput` (`isApplyingEmulatorOutput`)

- **File:method:** `terminal-emulator/.../TerminalSession.java::emitOutput` (+ new `mApplyingEmulatorOutput` field / `isApplyingEmulatorOutput`)
- **Upstream link:** [upstream](https://github.com/termux/termux-app/blob/master/terminal-emulator/src/main/java/com/termux/terminal/TerminalSession.java)

**What changed.** Added a main-thread-only `private boolean mApplyingEmulatorOutput`, raised in a `try/finally` around `mEmulator.append(data, length)` inside `emitOutput`'s Runnable, plus a `public boolean isApplyingEmulatorOutput()` accessor. No behaviour change inside the emulator: the `getSynchronizedOutputResets()` sampling either side of the append (see the 2026-07-09 `emitOutput` row) is untouched, and the `finally` still lets a parser throw escape the Runnable as before.

**Why.** CLA-184: `TerminalSession.write` is one funnel for two sources — the user (typed keys, ActionMode paste, mouse events, all reached from *outside* `append`) and the parser answering a query the program sent it (DA, DSR/CPR, DECRQM, DECRQSS, XTGETTCAP, window ops, OSC colour reports, all emitted synchronously *inside* `append`).

The host must tell them apart so a bare `ESC[6n` — which powerlevel10k/starship emit on every prompt redraw — cannot resume a paused follow mode, snap the viewport to the tail and wipe the unread badge. `append` has exactly one caller (`emitOutput`), which is what makes the flag an exact discriminator rather than a heuristic. Consumer is `TerminalViewModel.sendTerminalBytes`; iOS parity is `MobaideTerminalView.applyingEmulatorOutput` (CLA-178 / PR #478).

### 2026-08-13: `TerminalSession.java::emitOutput` (`isApplyingReplayOutput`)

- **File:method:** `terminal-emulator/.../TerminalSession.java::emitOutput` (+ new `mApplyingReplayOutput` field / `isApplyingReplayOutput`)
- **Upstream link:** [upstream](https://github.com/termux/termux-app/blob/master/terminal-emulator/src/main/java/com/termux/terminal/TerminalSession.java)

**What changed.** `emitOutput(byte[], int)` gained a third parameter, `boolean replay`, captured into a main-thread-only `private boolean mApplyingReplayOutput` raised in the same `try/finally` as `mApplyingEmulatorOutput` (previous row) around `mEmulator.append`, plus a `public boolean isApplyingReplayOutput()` accessor. The parameter is required, not defaulted: a caller that forgets it would silently claim "live", which is the fail-open direction. No behaviour change inside the emulator.

**Why.** The server replays its ring verbatim on every attach, so any control whose effect lands *outside* the screen re-fires on every session open — for OSC 52 that means silently overwriting the phone's clipboard (Android 13+ shows its "Copied" chip), which is what users reported.

The flag has to travel with the bytes rather than be read from the host at append time: `emitOutput` posts the append to the main looper, while `TerminalViewModel` handles the whole `'b' → 'o'×N → 'e'` burst — clearing its own `inReplay` — in an earlier message, so a host-side read would see `false` for nearly every replayed byte.

Consumer is `TerminalSessionCallback.onCopyTextToClipboard`; iOS parity is `MobaideTerminalView.applyingEmulatorOutput(replayed:)`, web's is `TerminalSessionModel.isParsingReplay`.

### 2026-10-16: `TextSelectionCursorController.java::updatePosition`

- **File:method:** `terminal-view/.../textselection/TextSelectionCursorController.java::updatePosition` (+ new `autoscrollHorizontally`, `didAutoscroll`), `CursorController.java` (new `default boolean didAutoscroll()`), `TextSelectionHandleView.java` (`onTouchEvent` ACTION_MOVE → new `dragToLastTouch` + `mAutoscrollTick`)
- **Upstream link:** [upstream](https://github.com/termux/termux-app/blob/master/terminal-view/src/main/java/com/termux/view/textselection/TextSelectionCursorController.java)

**What changed.** `updatePosition` now pans a `HorizontalScrollView` parent one cell towards the edge when the dragged handle sits within an edge band (`max(2 × fontWidth, 24dp)`, capped at a third of the viewport), and records whether that or the existing top-row step scrolled.

The handle view keeps the last raw touch and, while the last update scrolled, re-runs the drag from it every 45 ms via `terminalView.postDelayed` — while that tick is queued `ACTION_MOVE` only records the touch, so the scroll keeps the tick's pace rather than the touch-sampling rate; the callback is removed on UP/CANCEL and in `hide()`.

The existing `mTouchToWindowOffsetX` parent-move compensation in `moveTo` is what turns a horizontal pan into newly revealed columns under a still finger.

**Why.** Selection could not reach off-screen text: in Static display mode nothing panned the host horizontally during a handle drag, and the upstream vertical step ran only on `ACTION_MOVE`, so a finger held still at an edge stopped scrolling. iOS parity: `TerminalSelectionController.updateAutoscroll` / `TerminalSelection.autoscrollColumns`.

### App-side note on the OSC 52 clipboard sink

**CLA-63 note (app-side behavior, not a fork edit):** The app now implements the previously-no-op clipboard sink (`TerminalSessionCallback.onCopyTextToClipboard` in `android/app/.../ui/terminal/TerminalSurfaceView.kt`).

Besides the ActionMode "Copy" gesture, this sink is also reached by the vendored emulator's **OSC 52** ("Manipulate Selection Data") handler in `terminal-emulator/.../TerminalEmulator.java`. So a program in the PTY (e.g. vim/tmux yank) can now set the system clipboard without a user gesture — from **live** output only, the sink refusing anything parsed out of the server's replayed ring (see the 2026-08-13 row above).

Write-direction only — the OSC 52 read/query form is not answered — and the clip is marked sensitive (`EXTRA_IS_SENSITIVE`).

Recorded here because a maintainer touching the emulator's OSC-52 path should know a live clipboard sink now exists downstream.

## How to re-apply on rebase

1. Fetch the upstream Termux master, identify the new `TerminalView.java` /
   `TerminalViewClient.java` / `TerminalEmulator.java`.
2. For each entry above, find the matching method in the new upstream and
   re-apply the diff. The upstream method signatures rarely change; the line
   numbers cited in commit messages do.
3. Run `./gradlew :terminal-view:compileDebugJavaWithJavac` and
   `./gradlew :terminal-emulator:compileDebugJavaWithJavac`, then
   `./gradlew :app:compileDebugKotlin` to verify no callsites broke.
4. Run `./gradlew :app:testDebugUnitTest --tests "*TerminalViewModelFollowMode*"`,
   `./gradlew :app:testDebugUnitTest --tests "*TerminalViewModelEmulatorReply*"`
   and `./gradlew :app:testDebugUnitTest --tests "*TerminalViewModelReplayClipboard*"`
   to confirm the follow-mode and replay-attribution contracts still hold
   end-to-end. The last one is the only check that catches a re-defaulted or
   reverted `emitOutput` replay flag — step 3 sees a *dropped* parameter, not a
   flag that stopped being set.
