import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { ChatResult, ChatSessionModel } from '../../chat/chatModel';
import type { ChatQueued } from '../../chat/chatWire';
import { useToastStore } from '../../stores/toastStore';
import { QueueList } from './DockPanels';

/** The two queue commands, answered when the test says so. */
class FakeQueueModel {
  readonly sent: { op: 'steer' | 'dequeue'; id: string }[] = [];
  private answers: ((result: ChatResult) => void)[] = [];

  steer(id: string): Promise<ChatResult> {
    return this.record('steer', id);
  }

  dequeue(id: string): Promise<ChatResult> {
    return this.record('dequeue', id);
  }

  async answer(result: ChatResult): Promise<void> {
    const next = this.answers.shift();
    if (next === undefined) throw new Error('no command waits for an answer');
    await act(async () => {
      next(result);
      await Promise.resolve();
    });
  }

  private record(op: 'steer' | 'dequeue', id: string): Promise<ChatResult> {
    this.sent.push({ op, id });
    return new Promise((resolve) => this.answers.push(resolve));
  }
}

const QUEUE: readonly ChatQueued[] = [
  { id: 'q:1', text: 'Also update the README', images: 0 },
  { id: 'q:2', text: 'Then run the tests', images: 0 },
];

function renderQueue(canSteer: boolean, live = true): FakeQueueModel {
  const model = new FakeQueueModel();
  render(
    <QueueList
      model={model as unknown as ChatSessionModel}
      queue={QUEUE}
      live={live}
      canSteer={canSteer}
    />,
  );
  return model;
}

const failed = (error: string): ChatResult => ({ ok: false, error, lost: false });

beforeEach(() => {
  useToastStore.setState({ toasts: [] });
});

afterEach(() => {
  cleanup();
});

describe('QueueList — Send now', () => {
  it('shows Send now on every row only while the agent can take a message mid-turn', () => {
    renderQueue(false);
    expect(screen.queryByRole('button', { name: 'Send now' })).not.toBeInTheDocument();
    cleanup();

    renderQueue(true);
    expect(screen.getAllByRole('button', { name: 'Send now' })).toHaveLength(2);
  });

  it('steers that row and locks its buttons until the answer', async () => {
    const model = renderQueue(true);
    const [sendFirst, sendSecond] = screen.getAllByRole('button', { name: 'Send now' });
    const [removeFirst] = screen.getAllByRole('button', { name: 'Remove from queue' });

    if (sendFirst === undefined) throw new Error('no Send now button');
    fireEvent.click(sendFirst);

    expect(model.sent).toEqual([{ op: 'steer', id: 'q:1' }]);
    expect(sendFirst).toBeDisabled();
    expect(removeFirst).toBeDisabled();
    expect(sendSecond).toBeEnabled();

    await model.answer({ ok: true, data: { steered: true } });

    expect(sendFirst).toBeEnabled();
    expect(useToastStore.getState().toasts).toEqual([]);
  });

  it('reports a refusal', async () => {
    const model = renderQueue(true);
    const [sendFirst] = screen.getAllByRole('button', { name: 'Send now' });
    if (sendFirst === undefined) throw new Error('no Send now button');

    fireEvent.click(sendFirst);
    await model.answer(failed('This agent cannot take a message during a turn'));

    expect(useToastStore.getState().toasts.map((toast) => [toast.kind, toast.message])).toEqual([
      ['error', 'Could not send the message now: This agent cannot take a message during a turn'],
    ]);
  });

  it('stays quiet when the message went out meanwhile', async () => {
    const model = renderQueue(true);
    const [sendFirst] = screen.getAllByRole('button', { name: 'Send now' });
    if (sendFirst === undefined) throw new Error('no Send now button');

    fireEvent.click(sendFirst);
    await model.answer(failed('This message was already sent'));

    expect(useToastStore.getState().toasts).toEqual([]);
  });
});
