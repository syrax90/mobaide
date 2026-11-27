import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { stubLocalStorage } from '../test/localStorageStub';

// Before importing the prefs store, which persists.
stubLocalStorage();

const { decodeEventEnvelope } = await import('../protocol/dto/envelope');
const { useNotificationPrefsStore } = await import('../stores/notificationPrefsStore');
const { useToastStore } = await import('../stores/toastStore');
const { useVisibleSessionStore } = await import('../stores/visibleSessionStore');
const {
  REQUEST_TITLE_WAIT_MS,
  clearAttentionNotifications,
  dismissResolvedAttention,
  handleAttentionEvent,
} = await import('./attentionNotifications');
const { queryClient } = await import('../lib/queryClient');
const { queryKeys } = await import('../lib/queryKeys');
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

/** The answerable chat request a session row names. */
function pendingRequest(id: string): Record<string, unknown> {
  return { id, title: 'npm test', tool_kind: 'execute', choices: ['allow_once', 'reject_once'] };
}

/** A permission alert of session `s-1` on `agent-1` that names a request. */
function permissionAlert(requestId: string) {
  return envelope({
    session_id: 's-1',
    reason: 'awaiting_permission',
    title: 'api refactor',
    agent_id: 'agent-1',
    request_id: requestId,
    request_tool_kind: 'execute',
  });
}

/** This tab's cached session list of `agent-1`, as the agent socket fills it. */
function cacheRow(row: Record<string, unknown>): void {
  queryClient.setQueryData(queryKeys.sessions('agent-1'), [row]);
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
  queryClient.clear();
  vi.useRealTimers();
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

  it('adds the request title only when the cached row names the same request', () => {
    // The user event never carries the title (an APNs alert is built from
    // it), so it comes from this tab's session list; a row that already moved
    // on to another request must not lend its title to this alert.
    vi.useFakeTimers();
    cacheRow({
      ...sessionRow('s-1', 'awaiting_permission'),
      attention_request: pendingRequest('r:3:a1b2c3'),
    });

    expect(handleAttentionEvent(permissionAlert('r:3:a1b2c3'))).toBe('system');
    expect(handleAttentionEvent(permissionAlert('r:4:d4e5f6'))).toBe('deferred');
    vi.advanceTimersByTime(REQUEST_TITLE_WAIT_MS);

    expect(post.mock.calls.map(([notification]) => notification.body)).toEqual([
      'api refactor wants to run a command\nnpm test',
      'api refactor wants to run a command',
    ]);
  });

  it('holds a permission alert for the row that names its request', () => {
    // The user event and the agent's own row race; the row often lands just
    // after. The held alert reads the cache again when it is delivered.
    vi.useFakeTimers();
    cacheRow(sessionRow('s-1', 'awaiting_permission'));

    expect(handleAttentionEvent(permissionAlert('r:3:a1b2c3'))).toBe('deferred');
    expect(post).not.toHaveBeenCalled();
    cacheRow({
      ...sessionRow('s-1', 'awaiting_permission'),
      attention_request: pendingRequest('r:3:a1b2c3'),
    });
    vi.advanceTimersByTime(REQUEST_TITLE_WAIT_MS);

    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0]?.[0].body).toBe('api refactor wants to run a command\nnpm test');
  });

  it('posts a permission alert at once when this tab holds no list of that agent', () => {
    // No row can arrive here to name the request, so waiting would only delay.
    expect(handleAttentionEvent(permissionAlert('r:3:a1b2c3'))).toBe('system');
    expect(post).toHaveBeenCalledWith({
      sessionId: 's-1',
      agentId: 'agent-1',
      title: 'Permission needed',
      body: 'api refactor wants to run a command',
    });
  });

  it('replaces a held permission alert with a newer alert of the same session', () => {
    // The session ended while the permission alert waited for its title. The
    // newer edge is what is true now, so the held alert must not post after it.
    vi.useFakeTimers();
    cacheRow(sessionRow('s-1', 'awaiting_permission'));
    expect(handleAttentionEvent(permissionAlert('r:3:a1b2c3'))).toBe('deferred');

    expect(
      handleAttentionEvent(
        envelope({
          session_id: 's-1',
          reason: 'ended',
          title: 'api refactor',
          agent_id: 'agent-1',
        }),
      ),
    ).toBe('system');
    vi.advanceTimersByTime(REQUEST_TITLE_WAIT_MS);

    expect(post.mock.calls.map(([notification]) => notification.title)).toEqual([
      'Session ended',
    ]);
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

  it('drops a held permission alert once a row named its request and then cleared', () => {
    // Another device answered while the alert waited for its title: the row
    // named the request, then came back without attention. Posting now would
    // ask about a request that is already gone.
    vi.useFakeTimers();
    cacheRow(sessionRow('s-1', 'awaiting_permission'));
    expect(handleAttentionEvent(permissionAlert('r:3:a1b2c3'))).toBe('deferred');

    dismissResolvedAttention(
      envelope(
        {
          session: {
            ...sessionRow('s-1', 'awaiting_permission'),
            attention_request: pendingRequest('r:3:a1b2c3'),
          },
        },
        'session.updated',
      ),
    );
    dismissResolvedAttention(envelope({ session: sessionRow('s-1') }, 'session.updated'));
    vi.advanceTimersByTime(REQUEST_TITLE_WAIT_MS);

    expect(post).not.toHaveBeenCalled();
  });

  it('keeps a held permission alert through a row without attention from before its edge', () => {
    // Rows the agent sent before the attention edge can still land after the
    // user event. A row without attention clears nothing until a row named
    // the request.
    vi.useFakeTimers();
    cacheRow(sessionRow('s-1', 'awaiting_permission'));
    expect(handleAttentionEvent(permissionAlert('r:3:a1b2c3'))).toBe('deferred');

    dismissResolvedAttention(envelope({ session: sessionRow('s-1') }, 'session.updated'));
    vi.advanceTimersByTime(REQUEST_TITLE_WAIT_MS);

    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0]?.[0].body).toBe('api refactor wants to run a command');
  });
});

describe('clearAttentionNotifications', () => {
  it('closes everything this tab posted', () => {
    clearAttentionNotifications();
    expect(closeAllAttentionNotifications).toHaveBeenCalled();
  });

  it('drops a held permission alert, so nothing posts after sign-out', () => {
    vi.useFakeTimers();
    cacheRow(sessionRow('s-1', 'awaiting_permission'));
    expect(handleAttentionEvent(permissionAlert('r:3:a1b2c3'))).toBe('deferred');

    clearAttentionNotifications();
    vi.advanceTimersByTime(REQUEST_TITLE_WAIT_MS);

    expect(post).not.toHaveBeenCalled();
  });
});
