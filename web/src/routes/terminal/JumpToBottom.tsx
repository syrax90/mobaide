import { unreadLabel } from '../../terminal/terminalFollow';

interface JumpToBottomProps {
  unread: number;
  onJump: () => void;
}

/**
 * Shown only while the viewport is off the tail, with the unread badge both
 * mobile clients carry. The count is rows that entered the scrollback while
 * unpinned — never scroll events (CLA-183).
 */
export function JumpToBottom({ unread, onJump }: JumpToBottomProps) {
  return (
    <button
      type="button"
      className="terminal-jump"
      onClick={onJump}
      aria-label={
        unread > 0
          ? `Jump to bottom, ${unreadLabel(unread)} new lines`
          : 'Jump to bottom'
      }
    >
      <span aria-hidden="true">↓</span>
      {unread > 0 && (
        <span className="terminal-jump-badge" aria-hidden="true">
          {unreadLabel(unread)}
        </span>
      )}
    </button>
  );
}
