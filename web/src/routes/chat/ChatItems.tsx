import { memo, useEffect, useId, useRef, useState, type ReactNode } from 'react';
import type { ChatResult } from '../../chat/chatModel';
import type {
  ChatItem,
  CompactionItem,
  ContentPart,
  NoticeItem,
  SubagentItem,
  ToolItem,
  ToolStatus,
  UserItem,
} from '../../chat/chatWire';
import { stepsLabel, type SubagentTree } from '../../chat/focusView';
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
  StepsIcon,
  ToolKindIcon,
} from './ChatIcons';
import { DiffView } from './DiffView';
import { PlanList, toastFailure } from './DockPanels';
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

interface ToolCardProps {
  item: ToolItem;
  waiting: boolean;
  /** Cut from the start of the title: a subagent's name inside its own card. */
  titlePrefix?: string;
}

function toolTitle(item: ToolItem, titlePrefix?: string): string {
  const title =
    titlePrefix !== undefined && item.title.startsWith(titlePrefix)
      ? item.title.slice(titlePrefix.length)
      : item.title;
  return title === '' ? 'Tool call' : title;
}

const ToolCard = memo(function ToolCard({ item, waiting, titlePrefix }: ToolCardProps) {
  const [open, setOpen] = useState(false);
  const title = toolTitle(item, titlePrefix);
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
          <span className="chat-tool-title">{title}</span>
          {item.subtitle !== null && item.subtitle !== '' && item.subtitle !== title && (
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

/** What a subagent card needs beyond its own item; one object for the whole timeline. */
export interface SubagentContext {
  tree: SubagentTree;
  /** Tool calls a pending request waits on. */
  waitingTools: ReadonlySet<string>;
  onStop: (subagentId: string) => Promise<ChatResult>;
  /** The socket is open: a Stop can reach the agent. */
  live: boolean;
}

const NO_TOOLS: readonly ToolItem[] = [];
const NO_SUBAGENTS: readonly SubagentItem[] = [];

function toolCallsLabel(count: number): string {
  return count === 1 ? '1 tool call' : `${String(count)} tool calls`;
}

function subagentChip(item: SubagentItem): { label: string; tone: string } | null {
  switch (item.state) {
    case 'running':
      return { label: 'Running', tone: 'running' };
    case 'requires_action':
      return { label: 'Needs you', tone: 'attention' };
    case 'idle':
      if (item.stop_reason === 'cancelled') return { label: 'Stopped', tone: 'stopped' };
      if (item.stop_reason === 'error') return { label: 'Failed', tone: 'failed' };
      return { label: 'Done', tone: 'done' };
    case 'unknown':
      return null;
  }
}

/** The result, clamped; "Show more" appears only when the clamp hides text. */
function SubagentResult({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  const [overflows, setOverflows] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const bodyId = useId();
  useEffect(() => {
    const element = ref.current;
    if (element === null || expanded || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => {
      setOverflows(element.scrollHeight > element.clientHeight + 1);
    });
    observer.observe(element);
    return () => {
      observer.disconnect();
    };
  }, [expanded, text]);
  return (
    <div className="chat-subagent-result">
      <div
        ref={ref}
        id={bodyId}
        className={expanded ? 'chat-subagent-result-body' : 'chat-subagent-result-body chat-subagent-clamped'}
      >
        <Markdown className="chat-agent" text={text} />
      </div>
      {(overflows || expanded) && (
        <button
          type="button"
          className="chat-link-button"
          aria-expanded={expanded}
          aria-controls={bodyId}
          onClick={() => {
            setExpanded((value) => !value);
          }}
        >
          {expanded ? 'Show less' : 'Show more'}
        </button>
      )}
    </div>
  );
}

/** The task, clamped to 3 lines; a toggle only when the clamp hides text. */
function SubagentTask({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const [overflows, setOverflows] = useState(false);
  const ref = useRef<HTMLElement | null>(null);
  useEffect(() => {
    const element = ref.current;
    if (element === null || open || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => {
      setOverflows(element.scrollHeight > element.clientHeight + 1);
    });
    observer.observe(element);
    return () => {
      observer.disconnect();
    };
  }, [open, text, overflows]);
  const className = `chat-subagent-task${open ? '' : ' chat-subagent-task-clamped'}`;
  if (!overflows && !open) {
    return (
      <p
        ref={(element) => {
          ref.current = element;
        }}
        className={`${className} chat-subagent-task-static`}
      >
        {text}
      </p>
    );
  }
  return (
    <button
      ref={(element) => {
        ref.current = element;
      }}
      type="button"
      className={className}
      aria-expanded={open}
      onClick={() => {
        setOpen((value) => !value);
      }}
    >
      {text}
    </button>
  );
}

interface SubagentCardProps {
  item: SubagentItem;
  context: SubagentContext;
  nested: boolean;
}

/** A subagent: its task, checklist, tool calls, nested subagents and result. */
const SubagentCard = memo(function SubagentCard({ item, context, nested }: SubagentCardProps) {
  const title = item.title === '' ? 'Subagent' : item.title;
  const tools = context.tree.tools.get(item.subagent_id) ?? NO_TOOLS;
  const nestedCards = context.tree.children.get(item.subagent_id) ?? NO_SUBAGENTS;
  const titlePrefix = `${title}: `;
  const waiting = tools.some((tool) => context.waitingTools.has(tool.id));

  const [planOpen, setPlanOpen] = useState(false);
  const [toolsOpen, setToolsOpen] = useState(waiting);
  const [wasWaiting, setWasWaiting] = useState(waiting);
  if (waiting !== wasWaiting) {
    setWasWaiting(waiting);
    if (waiting) setToolsOpen(true);
  }
  // The item a Stop was sent for: a new item (the agent's answer to it) ends the wait.
  const [stopSentFor, setStopSentFor] = useState<SubagentItem | null>(null);
  const stopping = stopSentFor === item;
  const planId = useId();
  const toolsId = useId();

  const chip = subagentChip(item);
  const done = item.plan.filter((entry) => entry.status === 'completed').length;
  const newest = tools[tools.length - 1];
  return (
    <div className={`chat-subagent chat-subagent-${item.state}${nested ? ' chat-subagent-nested' : ''}`}>
      <div className="chat-subagent-head">
        <span className="chat-tool-kind">
          <BulbIcon />
        </span>
        <span className="chat-tool-text">
          <span className="chat-tool-title">{title}</span>
          {item.description !== null && item.description !== '' && (
            <span className="chat-subagent-role">{item.description}</span>
          )}
        </span>
        {chip !== null && (
          <span className={`chat-badge chat-subagent-chip chat-subagent-chip-${chip.tone}`}>
            {chip.tone === 'running' && <span className="chat-spinner" aria-hidden="true" />}
            {chip.label}
          </span>
        )}
      </div>
      {item.task !== null && item.task !== '' && <SubagentTask text={item.task} />}
      {item.plan.length > 0 && (
        <>
          <button
            type="button"
            className="chat-subagent-toggle"
            aria-expanded={planOpen}
            aria-controls={planId}
            onClick={() => {
              setPlanOpen((value) => !value);
            }}
          >
            <span className="chat-subagent-toggle-text">
              Checklist {done}/{item.plan.length}
            </span>
            <span className={`chat-chevron${planOpen ? ' chat-chevron-open' : ''}`}>
              <ChevronIcon />
            </span>
          </button>
          {planOpen && <PlanList id={planId} plan={item.plan} className="chat-subagent-plan" />}
        </>
      )}
      {tools.length > 0 && (
        <>
          <button
            type="button"
            className="chat-subagent-toggle"
            aria-expanded={toolsOpen}
            aria-controls={toolsId}
            onClick={() => {
              setToolsOpen((value) => !value);
            }}
          >
            <span className="chat-subagent-toggle-text">
              {toolCallsLabel(tools.length)}
              {item.state === 'running' && newest !== undefined && (
                <>
                  {' · '}
                  <span className="chat-steps-current">{toolTitle(newest, titlePrefix)}</span>
                </>
              )}
            </span>
            <span className={`chat-chevron${toolsOpen ? ' chat-chevron-open' : ''}`}>
              <ChevronIcon />
            </span>
          </button>
          {toolsOpen && (
            <div className="chat-subagent-tools" id={toolsId}>
              {tools.map((tool) => (
                <ToolCard
                  key={tool.id}
                  item={tool}
                  waiting={context.waitingTools.has(tool.id)}
                  titlePrefix={titlePrefix}
                />
              ))}
            </div>
          )}
        </>
      )}
      {nestedCards.map((child) => (
        <SubagentCard key={child.id} item={child} context={context} nested />
      ))}
      {item.result !== null && item.result !== '' && <SubagentResult text={item.result} />}
      {item.can_cancel && (
        <div className="chat-subagent-actions">
          <button
            type="button"
            className="button chat-button-reject chat-subagent-stop"
            aria-label={`Stop ${title}`}
            disabled={!context.live || stopping}
            onClick={() => {
              setStopSentFor(item);
              void context.onStop(item.subagent_id).then((result) => {
                if (result.ok) return;
                setStopSentFor(null);
                toastFailure(result, 'Could not stop the subagent');
              });
            }}
          >
            Stop
          </button>
        </div>
      )}
    </div>
  );
});

interface StepsRowProps {
  id: string;
  count: number;
  live: boolean;
  current: string | null;
  open: boolean;
  onToggle: (id: string) => void;
}

/** Focus view: one folded run of thinking and tool calls; opens in place. */
export const StepsRow = memo(function StepsRow({
  id,
  count,
  live,
  current,
  open,
  onToggle,
}: StepsRowProps) {
  const label = stepsLabel(count);
  return (
    <button
      type="button"
      className={`chat-steps${live ? ' chat-steps-live' : ''}`}
      aria-expanded={open}
      onClick={() => {
        onToggle(id);
      }}
    >
      {live ? <span className="chat-spinner" aria-hidden="true" /> : <StepsIcon />}
      <span className="chat-steps-text">
        {live && current !== null ? (
          <>
            <span className="chat-steps-current">{current}</span> · {label}
          </>
        ) : (
          label
        )}
      </span>
      <span className={`chat-chevron${open ? ' chat-chevron-open' : ''}`}>
        <ChevronIcon />
      </span>
    </button>
  );
});

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
  /** Renders `subagent` items as cards; without it they are skipped. */
  subagents?: SubagentContext;
}

export const ChatItemView = memo(function ChatItemView({
  item,
  streaming,
  waiting,
  canRestart,
  restarting,
  onRestart,
  subagents,
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
    case 'subagent':
      return subagents === undefined ? null : (
        <SubagentCard item={item} context={subagents} nested={false} />
      );
  }
});
