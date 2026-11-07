import { memo, useState, type ReactNode } from 'react';
import type {
  ChatItem,
  CompactionItem,
  ContentPart,
  NoticeItem,
  ToolItem,
  ToolStatus,
  UserItem,
} from '../../chat/chatWire';
import { safeHref } from '../../chat/markdown';
import { writeClipboardText } from '../../lib/clipboard';
import { useToastStore } from '../../stores/toastStore';
import {
  CheckIcon,
  ChevronIcon,
  CircleIcon,
  CrossIcon,
  BulbIcon,
  SlashedCircleIcon,
  ToolKindIcon,
} from './ChatIcons';
import { DiffView } from './DiffView';
import { Markdown } from './Markdown';

const INLINE_IMAGE_MIMES = new Set(['image/png', 'image/jpeg', 'image/gif', 'image/webp']);

function ContentPartView({ part }: { part: ContentPart }) {
  switch (part.type) {
    case 'text':
      return (
        <div className="chat-part">
          <Markdown text={part.text} />
          {part.truncated && <p className="chat-part-note">Output truncated</p>}
        </div>
      );
    case 'diff':
      return <DiffView part={part} />;
    case 'image':
      return typeof part.data === 'string' && part.data !== '' && INLINE_IMAGE_MIMES.has(part.mime) ? (
        <img
          className="chat-part-image"
          src={`data:${part.mime};base64,${part.data}`}
          alt="Image from the agent"
        />
      ) : (
        <p className="chat-part-note">Image</p>
      );
    case 'link':
    case 'resource': {
      const href = safeHref(part.uri);
      const label = part.name !== '' ? part.name : part.uri;
      return (
        <div className="chat-part chat-part-resource">
          {href === null ? (
            <span className="chat-part-link">{label}</span>
          ) : (
            <a className="chat-part-link" href={href} target="_blank" rel="noopener noreferrer">
              {label}
            </a>
          )}
          {part.type === 'resource' && typeof part.text === 'string' && part.text !== '' && (
            <pre className="chat-part-pre" tabIndex={0}>
              {part.text}
            </pre>
          )}
        </div>
      );
    }
    case 'terminal':
      return <p className="chat-part-note">Terminal output</p>;
  }
}

export function ContentParts({ parts }: { parts: readonly ContentPart[] }) {
  return (
    <>
      {parts.map((part, index) => (
        <ContentPartView key={index} part={part} />
      ))}
    </>
  );
}

const STATUS_LABEL: Record<ToolStatus, string> = {
  pending: 'Pending',
  in_progress: 'Running',
  completed: 'Done',
  failed: 'Failed',
  cancelled: 'Cancelled',
};

function ToolStatusIcon({ status }: { status: ToolStatus }) {
  let icon: ReactNode;
  switch (status) {
    case 'in_progress':
      icon = <span className="chat-spinner" aria-hidden="true" />;
      break;
    case 'completed':
      icon = <CheckIcon />;
      break;
    case 'failed':
      icon = <CrossIcon />;
      break;
    case 'cancelled':
      icon = <SlashedCircleIcon />;
      break;
    default:
      icon = <CircleIcon />;
  }
  return (
    <span className={`chat-tool-status chat-tool-status-${status}`}>
      {icon}
      <span className="visually-hidden">{STATUS_LABEL[status]}</span>
    </span>
  );
}

const DIFF_KINDS = new Set(['edit', 'delete', 'move']);

function diffStat(item: ToolItem): { added: number; removed: number } | null {
  if (!DIFF_KINDS.has(item.kind)) return null;
  let added = 0;
  let removed = 0;
  let found = false;
  for (const part of item.content) {
    if (part.type !== 'diff') continue;
    found = true;
    added += part.added;
    removed += part.removed;
  }
  return found ? { added, removed } : null;
}

function firstErrorLine(item: ToolItem): string | null {
  if (item.status !== 'failed') return null;
  const text = item.content.find((part) => part.type === 'text');
  if (text?.type !== 'text') return null;
  const line = text.text
    .split('\n')
    .map((candidate) => candidate.trim())
    .find((candidate) => candidate !== '' && !/^(`{3,}|~{3,})/.test(candidate));
  return line ?? null;
}

function copyText(text: string): void {
  void writeClipboardText(text).then((ok) => {
    useToastStore.getState().show(ok ? 'Copied' : 'Could not copy', ok ? 'success' : 'error', 2000);
  });
}

const ToolCard = memo(function ToolCard({ item, waiting }: { item: ToolItem; waiting: boolean }) {
  const [open, setOpen] = useState(false);
  const stat = diffStat(item);
  const failure = firstErrorLine(item);
  const bodyId = `tool-body-${item.id}`;
  return (
    <div className={`chat-tool chat-tool-${item.status}${open ? ' chat-tool-open' : ''}`}>
      <button
        type="button"
        className="chat-tool-head"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => {
          setOpen((value) => !value);
        }}
      >
        <span className="chat-tool-kind">
          <ToolKindIcon kind={item.kind} />
        </span>
        <span className="chat-tool-text">
          <span className="chat-tool-title">{item.title === '' ? 'Tool call' : item.title}</span>
          {item.subtitle !== null && item.subtitle !== '' && item.subtitle !== item.title && (
            <span className="chat-tool-subtitle">{item.subtitle}</span>
          )}
          {failure !== null && <span className="chat-tool-failure">{failure}</span>}
        </span>
        {waiting && <span className="chat-tool-waiting">Waiting for approval</span>}
        {stat !== null && (
          <span className="chat-diff-stat">
            <span className="chat-stat-add">+{stat.added}</span>{' '}
            <span className="chat-stat-del">−{stat.removed}</span>
          </span>
        )}
        <ToolStatusIcon status={item.status} />
      </button>
      {open && (
        <div className="chat-tool-body" id={bodyId}>
          {item.subtitle !== null && item.subtitle !== '' && (
            <pre className="chat-tool-command">{item.subtitle}</pre>
          )}
          <ContentParts parts={item.content} />
          {item.locations.length > 0 && (
            <ul className="chat-locations">
              {item.locations.map((location, index) => {
                const label =
                  typeof location.line === 'number'
                    ? `${location.path}:${String(location.line)}`
                    : location.path;
                return (
                  <li key={index}>
                    <code>{label}</code>
                    <button
                      type="button"
                      className="chat-link-button"
                      aria-label={`Copy ${label}`}
                      onClick={() => {
                        copyText(label);
                      }}
                    >
                      Copy
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          {item.input !== null && item.input !== '' && (
            <details className="chat-tool-input">
              <summary>Input</summary>
              <pre tabIndex={0}>{item.input}</pre>
            </details>
          )}
          {item.content.length === 0 && item.locations.length === 0 && item.input === null && (
            <p className="chat-part-note">No details.</p>
          )}
        </div>
      )}
    </div>
  );
});

function UserBubble({ item }: { item: UserItem }) {
  return (
    <div className="chat-user">
      <div className="chat-user-bubble">
        {item.text !== '' && <p className="chat-user-text">{item.text}</p>}
        {item.attachments.length > 0 && (
          <ul className="chat-attachments" aria-label="Attachments">
            {item.attachments.map((attachment, index) => (
              <li key={index} className="chat-badge">
                {attachment.kind === 'image' ? 'Image' : (attachment.name ?? 'File')}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function ThoughtRow({ text, streaming }: { text: string; streaming: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="chat-thought">
      <button
        type="button"
        className={`chat-thought-head${streaming ? ' chat-thought-live' : ''}`}
        aria-expanded={open}
        onClick={() => {
          setOpen((value) => !value);
        }}
      >
        <BulbIcon />
        <span>{streaming ? 'Thinking…' : 'Thinking'}</span>
        <span className={`chat-chevron${open ? ' chat-chevron-open' : ''}`}>
          <ChevronIcon />
        </span>
      </button>
      {open && <Markdown className="chat-thought-text" text={text} />}
    </div>
  );
}

function NoticeBanner({ item }: { item: NoticeItem }) {
  const tone =
    item.severity === 'error' ? 'danger' : item.severity === 'warning' ? 'warning' : 'info';
  return (
    <div className={`chat-notice chat-notice-${tone}`} role="note">
      {item.title !== '' && <p className="chat-notice-title">{item.title}</p>}
      {item.text !== '' && <p className="chat-notice-text">{item.text}</p>}
    </div>
  );
}

function CompactionDivider({ item }: { item: CompactionItem }) {
  const [open, setOpen] = useState(false);
  const running = item.status === 'in_progress';
  const label =
    item.status === 'failed'
      ? 'Compaction failed'
      : item.status === 'cancelled'
        ? 'Compaction cancelled'
        : running
          ? 'Compacting context'
          : 'Context compacted';
  return (
    <div className="chat-compaction">
      <button
        type="button"
        className="chat-divider"
        aria-expanded={open}
        disabled={item.text === ''}
        onClick={() => {
          setOpen((value) => !value);
        }}
      >
        <span>{label}</span>
        {running && <span className="chat-spinner" aria-hidden="true" />}
      </button>
      {open && <Markdown className="chat-compaction-text" text={item.text} />}
    </div>
  );
}

export interface ChatItemViewProps {
  item: ChatItem;
  /** The last item while a turn runs: shows the live cursor or pulse. */
  streaming: boolean;
  /** A tool whose permission request is pending. */
  waiting: boolean;
  /** The agent is in `error`: the error card offers Restart. */
  canRestart: boolean;
  /** A Restart was sent and is not answered yet. */
  restarting: boolean;
  onRestart: () => void;
}

export const ChatItemView = memo(function ChatItemView({
  item,
  streaming,
  waiting,
  canRestart,
  restarting,
  onRestart,
}: ChatItemViewProps) {
  switch (item.type) {
    case 'user':
      return <UserBubble item={item} />;
    case 'agent':
      return (
        <Markdown className={streaming ? 'chat-agent chat-agent-live' : 'chat-agent'} text={item.text} />
      );
    case 'thought':
      return <ThoughtRow text={item.text} streaming={streaming} />;
    case 'tool':
      return <ToolCard item={item} waiting={waiting} />;
    case 'notice':
      return <NoticeBanner item={item} />;
    case 'compaction':
      return <CompactionDivider item={item} />;
    case 'turn_end':
      return <p className="chat-turn-end">{item.text === '' ? 'Turn ended' : item.text}</p>;
    case 'error':
      return (
        <div className="chat-error">
          <p>{item.text}</p>
          {canRestart && (
            <button type="button" className="button" disabled={restarting} onClick={onRestart}>
              Restart
            </button>
          )}
        </div>
      );
  }
});
