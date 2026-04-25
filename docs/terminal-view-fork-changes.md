# Vendored Termux Fork: TerminalView changes

The Android client embeds the Termux `terminal-view` and `terminal-emulator` modules
(see `android/terminal-view/` and `android/terminal-emulator/`) as a vendored copy. Any
local edit to that Java is recorded here so that an upstream rebase can re-apply each
change accurately. Keep this file as the single source of truth for fork drift —
do not rely on git blame alone.

## Edits

| Date | File:Method | Upstream link | What changed | Why |
|---|---|---|---|---|
| 2026-04-25 | `terminal-view/.../TerminalView.java::updateSize` | [upstream](https://github.com/termux/termux-app/blob/master/terminal-view/src/main/java/com/termux/view/TerminalView.java) | Guarded `mTopRow = 0; scrollTo(0, 0);` with `if (mEmulator == null \|\| !mEmulator.isAutoScrollDisabled())`. `invalidate()` still always runs. | CLA-41: preserve scroll position through soft-keyboard show/hide and rotation when the user has paused follow-mode. Unpaused users still snap to bottom. |
| 2026-04-25 | `terminal-view/.../TerminalView.java::attachSession` | [upstream](https://github.com/termux/termux-app/blob/master/terminal-view/src/main/java/com/termux/view/TerminalView.java) | Guarded `mTopRow = 0` with `if (mEmulator == null \|\| !mEmulator.isAutoScrollDisabled())`. | CLA-41: defense-in-depth for future re-attach flows. New sessions start with `isAutoScrollDisabled() == false`, so the reset still fires. |
| 2026-04-25 | `terminal-view/.../TerminalView.java::doScroll` | [upstream](https://github.com/termux/termux-app/blob/master/terminal-view/src/main/java/com/termux/view/TerminalView.java) | Added `mClient.onScrollPositionChanged(mTopRow);` as the last line of the method. | CLA-41: lets the Compose host observe drag/fling settle so it can flip `followMode`. Do NOT also fire from `setTopRow(int)` — that path is used by the host to programmatically jump-to-bottom and would create a feedback loop. |
| 2026-04-25 | `terminal-view/.../TerminalViewClient.java` | [upstream](https://github.com/termux/termux-app/blob/master/terminal-view/src/main/java/com/termux/view/TerminalViewClient.java) | Added `default void onScrollPositionChanged(int topRow) {}` to the interface. | CLA-41: Java→Kotlin scroll-position bridge. Default body keeps existing implementors source-/binary-compatible. |

## How to re-apply on rebase

1. Fetch the upstream Termux master, identify the new `TerminalView.java` /
   `TerminalViewClient.java`.
2. For each row in the table, find the matching method in the new upstream and
   re-apply the diff. The upstream method signatures rarely change; the line
   numbers cited in commit messages do.
3. Run `./gradlew :terminal-view:compileDebugJavaWithJavac` then
   `./gradlew :app:compileDebugKotlin` to verify no callsites broke.
4. Run `./gradlew :app:testDebugUnitTest --tests "*TerminalViewModelFollowMode*"`
   to confirm the follow-mode contract still holds end-to-end.
