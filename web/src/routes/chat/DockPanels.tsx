import { useId, useState } from 'react';
import type { ChatResult, ChatSessionModel } from '../../chat/chatModel';
import type { ChatPlanEntry, ChatQueued } from '../../chat/chatWire';
import { useToastStore } from '../../stores/toastStore';
import { CheckIcon, ChevronIcon, CircleIcon, CrossIcon } from './ChatIcons';

export function toastFailure(result: ChatResult, prefix: string): void {
  if (result.ok) return;
  useToastStore.getState().show(`${prefix}: ${result.error}`, 'error');
}

export function PlanBar({ plan }: { plan: readonly ChatPlanEntry[] }) {
  const [open, setOpen] = useState(false);
  const listId = useId();
  if (plan.length === 0) return null;
  const done = plan.filter((entry) => entry.status === 'completed').length;
  const current = plan.find((entry) => entry.status === 'in_progress');
  return (
    <section className="chat-plan" aria-label="Plan">
      <button
        type="button"
        className="chat-plan-head"
        aria-expanded={open}
        aria-controls={listId}
        onClick={() => {
          setOpen((value) => !value);
        }}
      >
        <span className="chat-plan-progress">
          Plan · {done}/{plan.length}
        </span>
        {current !== undefined && <span className="chat-plan-current">{current.content}</span>}
        <span className={`chat-chevron${open ? ' chat-chevron-open' : ''}`}>
          <ChevronIcon />
        </span>
      </button>
      {open && (
        <ol className="chat-plan-list" id={listId}>
          {plan.map((entry, index) => (
            <li key={index} className={`chat-plan-entry chat-plan-${entry.status}`}>
              <span className="chat-plan-mark" aria-hidden="true">
                {entry.status === 'completed' ? (
                  <CheckIcon />
                ) : entry.status === 'in_progress' ? (
                  <span className="chat-plan-active" />
                ) : (
                  <CircleIcon />
                )}
              </span>
              <span className="chat-plan-text">{entry.content}</span>
              <span className="visually-hidden">
                {entry.status === 'completed'
                  ? '(done)'
                  : entry.status === 'in_progress'
                    ? '(in progress)'
                    : '(to do)'}
              </span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

export function QueueList({
  model,
  queue,
  live,
}: {
  model: ChatSessionModel;
  queue: readonly ChatQueued[];
  live: boolean;
}) {
  const [removing, setRemoving] = useState<ReadonlySet<string>>(new Set());
  if (queue.length === 0) return null;
  return (
    <ul className="chat-queue" aria-label="Queued messages">
      {queue.map((entry) => (
        <li key={entry.id} className="chat-queue-row">
          <span className="chat-queue-label">Queued</span>
          <span className="chat-queue-text">
            {entry.text === '' ? `${String(entry.images)} image(s)` : entry.text}
          </span>
          <button
            type="button"
            className="chat-icon-button"
            aria-label="Remove from queue"
            title="Remove from queue"
            disabled={!live || removing.has(entry.id)}
            onClick={() => {
              setRemoving((current) => new Set(current).add(entry.id));
              void model.dequeue(entry.id).then((result) => {
                setRemoving((current) => {
                  const next = new Set(current);
                  next.delete(entry.id);
                  return next;
                });
                if (!result.ok && result.error !== 'This message was already sent') {
                  toastFailure(result, 'Could not remove the message');
                }
              });
            }}
          >
            <CrossIcon />
          </button>
        </li>
      ))}
    </ul>
  );
}
