import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { useStore } from 'zustand';
import type { ChatSessionModel } from '../../chat/chatModel';
import { timelineRows } from '../../chat/focusView';
import { useChatPrefsStore } from '../../stores/chatPrefsStore';
import { ChatItemView, StepsRow } from './ChatItems';
import { MiddleEllipsis } from './MiddleEllipsis';

/** Within this many pixels of the end counts as "at the bottom". */
const BOTTOM_SLACK = 32;
/** A scroll this soon after wheel, touch, key or press input is the user's. */
const INPUT_WINDOW_MS = 800;

interface ChatTimelineViewProps {
  model: ChatSessionModel;
  /** The kind label ("Claude Code"), until the agent names itself. */
  kindLabel: string;
  onRestart: () => void;
  /** Floats over the timeline: the connection pill. */
  children?: ReactNode;
}

export function ChatTimelineView({ model, kindLabel, onRestart, children }: ChatTimelineViewProps) {
  const items = useStore(model.store, (state) => state.timeline.items);
  const loaded = useStore(model.store, (state) => state.timeline.loaded);
  const truncated = useStore(model.store, (state) => state.timeline.truncated);
  const status = useStore(model.store, (state) => state.timeline.state.status);
  const restarting = useStore(model.store, (state) => state.restarting);
  const requests = useStore(model.store, (state) => state.timeline.state.requests);
  const agent = useStore(model.store, (state) => state.timeline.state.agent);
  const cwd = useStore(model.store, (state) => state.timeline.state.cwd);
  const hasCommands = useStore(model.store, (state) => state.timeline.state.commands.length > 0);

  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const lastInput = useRef(Number.NEGATIVE_INFINITY);
  /** Set briefly by a press inside the list: a card the user opened must not scroll away. */
  const interacting = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [unseen, setUnseen] = useState(false);
  const [farFromEnd, setFarFromEnd] = useState(false);
  const focus = useChatPrefsStore((prefs) => prefs.focusView);
  const [openSteps, setOpenSteps] = useState<ReadonlySet<string>>(() => new Set());

  const waitingTools = useMemo(() => {
    const ids = new Set<string>();
    for (const request of requests) {
      // Any kind, as on Android and iOS: a question asked from a tool keeps that tool in view.
      if (request.tool_call_id !== null) ids.add(request.tool_call_id);
    }
    return ids;
  }, [requests]);

  const running = status === 'running';
  const rows = useMemo(
    () => timelineRows(items, { focus, running, waitingTools, open: openSteps }),
    [items, focus, running, waitingTools, openSteps],
  );
  const toggleSteps = useCallback((id: string) => {
    setOpenSteps((current) => {
      const next = new Set(current);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  }, []);

  const scrollToEnd = useCallback(() => {
    const scroller = scrollRef.current;
    if (scroller === null) return;
    scroller.scrollTop = scroller.scrollHeight;
  }, []);

  // Only the user leaves the bottom. Scroll events also come from layout —
  // rows rendering in (`content-visibility`) and the browser's scroll
  // anchoring — and those must not stop the follow.
  const onScroll = useCallback(() => {
    const scroller = scrollRef.current;
    if (scroller === null) return;
    const distance = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
    if (distance <= BOTTOM_SLACK) {
      following.current = true;
      setUnseen(false);
    } else if (performance.now() - lastInput.current < INPUT_WINDOW_MS) {
      following.current = false;
    }
    setFarFromEnd(!following.current && distance > scroller.clientHeight);
  }, []);

  const noteInput = useCallback(() => {
    lastInput.current = performance.now();
  }, []);

  const lastItem = items[items.length - 1];
  useLayoutEffect(() => {
    if (following.current) scrollToEnd();
    else if (lastItem !== undefined) setUnseen(true);
  }, [items.length, lastItem, scrollToEnd]);

  // Rows grow without a new item: a streamed token, an expanded card, a font
  // swap. Following means staying pinned through all of them.
  useEffect(() => {
    const content = contentRef.current;
    if (content === null || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => {
      if (interacting.current !== null) {
        const scroller = scrollRef.current;
        if (scroller !== null) {
          following.current =
            scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight <= BOTTOM_SLACK;
        }
        return;
      }
      if (following.current) scrollToEnd();
    });
    observer.observe(content);
    return () => {
      observer.disconnect();
      if (interacting.current !== null) clearTimeout(interacting.current);
    };
  }, [scrollToEnd]);

  const noteInteraction = useCallback(() => {
    lastInput.current = performance.now();
    if (interacting.current !== null) clearTimeout(interacting.current);
    interacting.current = setTimeout(() => {
      interacting.current = null;
    }, 400);
  }, []);

  const agentTitle = agent?.title !== undefined && agent.title !== '' ? agent.title : kindLabel;
  const starting = status === 'starting' || status === 'loading';
  const lastId = items[items.length - 1]?.id;
  let lastErrorId: string | undefined;
  for (let index = items.length - 1; index >= 0 && status === 'error'; index -= 1) {
    const candidate = items[index];
    if (candidate?.type === 'error') {
      lastErrorId = candidate.id;
      break;
    }
  }

  let body;
  if (!loaded || (starting && items.length === 0)) {
    body = (
      <div className="chat-placeholder">
        <p className="state-loading" role="status">
          {loaded && status === 'starting' ? `Starting ${agentTitle}…` : 'Opening the session…'}
        </p>
      </div>
    );
  } else if (items.length === 0) {
    body = (
      <div className="chat-placeholder chat-empty">
        <p className="chat-empty-title">{agentTitle}</p>
        {cwd !== null && <MiddleEllipsis className="chat-empty-cwd" text={cwd} />}
        {status !== 'error' && status !== 'stopped' && (
          <p className="chat-empty-hint">
            {hasCommands
              ? 'Send a message to start. Type / for commands.'
              : 'Send a message to start.'}
          </p>
        )}
      </div>
    );
  } else {
    body = (
      <>
        {truncated && <p className="chat-truncated">Earlier messages are not shown.</p>}
        {rows.map((row) => {
          if (row.kind === 'steps') {
            return (
              <div key={row.id} className="chat-row chat-row-steps">
                <StepsRow
                  id={row.id}
                  count={row.count}
                  live={row.live}
                  current={row.current}
                  open={row.open}
                  onToggle={toggleSteps}
                />
              </div>
            );
          }
          const { item } = row;
          return (
            <div
              key={item.id}
              className={`chat-row chat-row-${item.type}${row.nested ? ' chat-row-nested' : ''}`}
            >
              <ChatItemView
                item={item}
                streaming={running && item.id === lastId}
                waiting={item.type === 'tool' && waitingTools.has(item.id)}
                canRestart={item.id === lastErrorId}
                restarting={item.id === lastErrorId && restarting}
                onRestart={onRestart}
              />
            </div>
          );
        })}
      </>
    );
  }

  return (
    <div className="chat-stage">
      <div
        ref={scrollRef}
        className="chat-scroll"
        role="region"
        aria-label="Conversation"
        tabIndex={0}
        onScroll={onScroll}
        onPointerDownCapture={noteInteraction}
        onKeyDownCapture={noteInteraction}
        onWheel={noteInput}
        onTouchMove={noteInput}
      >
        <div ref={contentRef} className="chat-column chat-timeline">
          {body}
        </div>
      </div>
      {children}
      {(unseen || farFromEnd) && (
        <button
          type="button"
          className="chat-jump"
          onClick={() => {
            following.current = true;
            setUnseen(false);
            setFarFromEnd(false);
            scrollToEnd();
          }}
        >
          {unseen && <span className="chat-jump-dot" aria-hidden="true" />}
          Jump to latest
        </button>
      )}
    </div>
  );
}
