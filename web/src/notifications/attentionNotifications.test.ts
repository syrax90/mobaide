import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { stubLocalStorage } from '../test/localStorageStub';

// Before importing the prefs store, which persists.
stubLocalStorage();

const { decodeEventEnvelope } = await import('../protocol/dto/envelope');
const { useNotificationPrefsStore } = await import('../stores/notificationPrefsStore');
const { useToastStore } = await import('../stores/toastStore');
const { useVisibleSessionStore } = await import('../stores/visibleSessionStore');
const { clearAttentionNotifications, dismissResolvedAttention, handleAttentionEvent } =
  await import('./attentionNotifications');
const {
  closeAllAttentionNotifications,
  closeAttentionNotification,
  notificationPermission,
  postAttentionNotification,
} = await import('./notificationService');

vi.mock('./notificationService', () => ({
  notificationPermission: vi.fn(() => 'granted'),
  postAttentionNotification: vi.fn(() => true),
  closeAttentionNotification: vi.fn(),
  closeAllAttentionNotifications: vi.fn(),
}));

const permission = vi.mocked(notificationPermission);
const post = vi.mocked(postAttentionNotification);
const close = vi.mocked(closeAttentionNotification);

/** A user-events frame, as it arrives off the socket. */
function frame(payload: Record<string, unknown>, type = 'session.attention'): string {
  return JSON.stringify({ type, payload });
}

function envelope(payload: Record<string, unknown>, type = 'session.attention') {
  const decoded = decodeEventEnvelope(frame(payload, type));
  if (decoded === null) throw new Error('fixture envelope did not decode');
  return decoded;
}

/** A session row as the agent channel carries it. `created_at` is required. */
function sessionRow(sessionId: string, attention?: string): Record<string, unknown> {
  return {
    session_id: sessionId,
    state: 'running',
    created_at: '2026-07-08T12:00:00Z',
    kind: 'claude',
    ...(attention !== undefined && { attention }),
  };
}

function hide(hidden: boolean): void {
  Object.defineProperty(document, 'hidden', { configurable: true, value: hidden });
}

beforeEach(() => {
  vi.clearAllMocks();
  permission.mockReturnValue('granted');
  post.mockReturnValue(true);
  hide(true);
  useVisibleSessionStore.setState({ visibleSessionId: null });
  useNotificationPrefsStore.setState({ enabled: true });
  useToastStore.setState({ toasts: [] });
});

afterEach(() => {
  hide(false);
});

describe('handleAttentionEvent', () => {
  it('posts a system notification for a hidden tab, carrying the agent for the deep link', () => {
    const outcome = handleAttentionEvent(
      envelope({
        session_id: 's-1',
        reason: 'awaiting_permission',
        title: 'api refactor',
        agent_id: 'agent-1',
      }),
    );

    expect(outcome).toBe('system');
    expect(post).toHaveBeenCalledWith({
      sessionId: 's-1',
      agentId: 'agent-1',
      title: 'Permission needed',
      body: 'api refactor is waiting for your approval',
    });
  });

  it('reads a null kind off the wire as a Claude session', () => {
    handleAttentionEvent(
      envelope({ session_id: 's-1', reason: 'turn_finished', kind: null, kind_label: null }),
    );

    expect(post).toHaveBeenCalledWith({
      sessionId: 's-1',
      title: 'Your move',
      body: 'Claude session finished its turn — tap to continue',
    });
  });

  it('toasts instead while the tab is visible', () => {
    hide(false);

    expect(handleAttentionEvent(envelope({ session_id: 's-1', reason: 'turn_finished' }))).toBe(
      'toast',
    );
    expect(post).not.toHaveBeenCalled();
    expect(useToastStore.getState().toasts[0]?.message).toBe(
      'Your move — Claude session finished its turn — tap to continue',
    );
  });

  it('says nothing about the session already on screen', () => {
    hide(false);
    useVisibleSessionStore.setState({ visibleSessionId: 's-1' });

    expect(handleAttentionEvent(envelope({ session_id: 's-1', reason: 'turn_finished' }))).toBe(
      'suppressed',
    );
    expect(useToastStore.getState().toasts).toHaveLength(0);
  });

  it('reports a dropped post rather than claiming delivery', () => {
    // A `false` from the service means nothing reached the user — the
    // constructor threw, or permission changed between the check and the post.
    post.mockReturnValue(false);

    expect(handleAttentionEvent(envelope({ session_id: 's-1', reason: 'ended' }))).toBe(
      'suppressed',
    );
  });

  it('ignores an unrecognised reason without cancelling a live alert', () => {
    // The forward-compat contract both mobile clients keep: a reason a newer
    // agent knows and this client does not posts nothing *and cancels nothing*.
    // A clear never arrives this way — the relay drops a payload whose
    // `attention` is not a non-empty string — so dismissing here would only ever
    // kill an alert that is still live on the phone.
    expect(handleAttentionEvent(envelope({ session_id: 's-1', reason: 'awaiting_coffee' }))).toBe(
      'ignored',
    );
    expect(post).not.toHaveBeenCalled();
    expect(close).not.toHaveBeenCalled();
  });

  it('ignores every other user-events type', () => {
    expect(handleAttentionEvent(envelope({ agent_id: 'a' }, 'agent.status_changed'))).toBe(
      'ignored',
    );
    expect(post).not.toHaveBeenCalled();
    expect(close).not.toHaveBeenCalled();
  });

  it('drops a payload that fails its schema without touching the surface', () => {
    expect(handleAttentionEvent(envelope({ reason: 'turn_finished' }))).toBe('ignored');
    expect(post).not.toHaveBeenCalled();
    expect(close).not.toHaveBeenCalled();
  });

  it('honours the user opting out, in both surfaces', () => {
    useNotificationPrefsStore.setState({ enabled: false });

    expect(handleAttentionEvent(envelope({ session_id: 's-1', reason: 'ended' }))).toBe(
      'suppressed',
    );
    hide(false);
    expect(handleAttentionEvent(envelope({ session_id: 's-1', reason: 'ended' }))).toBe(
      'suppressed',
    );
    expect(post).not.toHaveBeenCalled();
    expect(useToastStore.getState().toasts).toHaveLength(0);
  });

  it('does not post from a snapshot-shaped payload', () => {
    // The whole design: `attention` is *latched* on the agent, so every
    // full-state snapshot keeps reporting it. Only the one-shot edge — whose
    // payload is the slim user-events shape with `reason` — reaches this
    // module; a `session.updated` row carries `attention` instead and is not it.
    expect(
      handleAttentionEvent(
        envelope({ session: { session_id: 's-1', attention: 'turn_finished' } }, 'session.updated'),
      ),
    ).toBe('ignored');
    expect(post).not.toHaveBeenCalled();
  });
});

describe('dismissResolvedAttention', () => {
  it('dismisses when a session row arrives without an attention', () => {
    // The clearing half cannot be edge-triggered: the agent publishes no
    // "attention resolved" event, only the full row with the field gone. Android
    // runs the same second collector.
    dismissResolvedAttention(
      envelope({ session: sessionRow('s-1') }, 'session.updated'),
    );
    expect(close).toHaveBeenCalledWith('s-1');
  });

  it('leaves a still-latched attention alone', () => {
    dismissResolvedAttention(
      envelope(
        { session: sessionRow('s-1', 'turn_finished') },
        'session.updated',
      ),
    );
    expect(close).not.toHaveBeenCalled();
  });

  it('sweeps a whole snapshot', () => {
    dismissResolvedAttention(
      envelope(
        {
          sessions: [
            sessionRow('s-1'),
            sessionRow('s-2', 'ended'),
            sessionRow('s-3'),
          ],
        },
        'sessions.changed',
      ),
    );
    expect(close.mock.calls.map(([id]) => id)).toEqual(['s-1', 's-3']);
  });

  it('ignores an unrelated type and a malformed payload', () => {
    dismissResolvedAttention(envelope({ projects: [] }, 'projects.changed'));
    dismissResolvedAttention(envelope({ sessions: 'nope' }, 'sessions.changed'));
    expect(close).not.toHaveBeenCalled();
  });
});

describe('clearAttentionNotifications', () => {
  it('closes everything this tab posted', () => {
    clearAttentionNotifications();
    expect(closeAllAttentionNotifications).toHaveBeenCalled();
  });
});
