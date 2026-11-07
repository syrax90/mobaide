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
import { ChatItemView } from './ChatItems';
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

  const waitingTools = useMemo(() => {
    const ids = new Set<string>();
    for (const request of requests) {
      if (request.kind === 'permission' && request.tool_call_id !== null) ids.add(request.tool_call_id);
    }
    return ids;
  }, [requests]);

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

  const running = status === 'running';
  const agentTitle = agent?.title !== undefined && agent.title !== '' ? agent.title : kindLabel;
  const starting = status === 'starting' || status === 'loading';
  const lastIndex = items.length - 1;
  let lastError = -1;
  for (let index = lastIndex; index >= 0 && status === 'error'; index -= 1) {
    if (items[index]?.type === 'error') {
      lastError = index;
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
        {items.map((item, index) => (
          <div key={item.id} className={`chat-row chat-row-${item.type}`}>
            <ChatItemView
              item={item}
              streaming={running && index === lastIndex}
              waiting={item.type === 'tool' && waitingTools.has(item.id)}
              canRestart={index === lastError}
              restarting={index === lastError && restarting}
              onRestart={onRestart}
            />
          </div>
        ))}
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
