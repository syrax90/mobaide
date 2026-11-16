import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { stubLocalStorage } from '../test/localStorageStub';

// Before importing the prefs store, which persists.
stubLocalStorage();

const { decodeEventEnvelope } = await import('../protocol/dto/envelope');
const { herdrPanePath, herdrPaneTag } = await import('../routes/herdr/herdrPath');
const { useNotificationPrefsStore } = await import('../stores/notificationPrefsStore');
const { useToastStore } = await import('../stores/toastStore');
const { useVisibleSessionStore } = await import('../stores/visibleSessionStore');
const { dismissResolvedHerdrAttention, handleHerdrAttentionEvent } = await import(
  './herdrAttention'
);
const { closeAttentionNotification, notificationPermission, postAttentionNotification } =
  await import('./notificationService');

vi.mock('./notificationService', () => ({
  notificationPermission: vi.fn(() => 'granted'),
  postAttentionNotification: vi.fn(() => true),
  closeAttentionNotification: vi.fn(),
  closeAllAttentionNotifications: vi.fn(),
}));

const permission = vi.mocked(notificationPermission);
const post = vi.mocked(postAttentionNotification);
const close = vi.mocked(closeAttentionNotification);

const AGENT = 'agent-1';
const SERVER = 'tui:default';
const PANE = 'w1:p1';
const TAG = herdrPaneTag(AGENT, SERVER, PANE);

function envelope(payload: Record<string, unknown>, type = 'herdr.attention') {
  const decoded = decodeEventEnvelope(JSON.stringify({ type, payload }));
  if (decoded === null) throw new Error('fixture envelope did not decode');
  return decoded;
}

/** The alert as the user-events channel carries it: the server adds `agent_id`. */
const alert = (over: Record<string, unknown> = {}) =>
  envelope({
    server_id: SERVER,
    pane_id: PANE,
    reason: 'awaiting_permission',
    title: 'api refactor',
    agent: 'claude',
    agent_id: AGENT,
    ...over,
  });

const snapshot = (panes: Record<string, unknown>[]) =>
  envelope(
    {
      servers: [
        {
          server_id: SERVER,
          workspaces: [{ workspace_id: 'w1', tabs: [{ tab_id: 'w1:t1', panes }] }],
        },
      ],
    },
    'herdr.changed',
  );

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

describe('handleHerdrAttentionEvent', () => {
  it('posts an alert that opens the pane, not a session', () => {
    expect(handleHerdrAttentionEvent(alert())).toBe('system');

    expect(post).toHaveBeenCalledWith({
      sessionId: TAG,
      agentId: AGENT,
      title: 'Permission needed',
      body: 'api refactor is waiting for your approval',
      path: herdrPanePath(AGENT, SERVER, PANE),
    });
  });

  it.each([{ agent_id: undefined }, { agent_id: null }, { agent_id: '' }])(
    'ignores an alert without the agent the server stamps: %j',
    (over) => {
      // The agent channel carries the same event without `agent_id`; posting
      // from it would alert twice and open no pane.
      expect(handleHerdrAttentionEvent(alert(over))).toBe('ignored');
      expect(post).not.toHaveBeenCalled();
    },
  );

  it('says nothing about the pane already on screen', () => {
    hide(false);
    useVisibleSessionStore.setState({ visibleSessionId: TAG });

    expect(handleHerdrAttentionEvent(alert())).toBe('suppressed');
    expect(useToastStore.getState().toasts).toHaveLength(0);
  });

  it('still alerts for another pane while one is on screen', () => {
    hide(false);
    useVisibleSessionStore.setState({ visibleSessionId: TAG });

    expect(handleHerdrAttentionEvent(alert({ pane_id: 'w1:p2' }))).toBe('toast');
    expect(post).not.toHaveBeenCalled();
    expect(useToastStore.getState().toasts).toHaveLength(1);
  });

  it('honours the user opting out', () => {
    useNotificationPrefsStore.setState({ enabled: false });

    expect(handleHerdrAttentionEvent(alert())).toBe('suppressed');
    expect(post).not.toHaveBeenCalled();
  });

  it('ignores a reason it does not know, a broken payload and other events', () => {
    expect(handleHerdrAttentionEvent(alert({ reason: 'awaiting_coffee' }))).toBe('ignored');
    expect(handleHerdrAttentionEvent(alert({ pane_id: 7 }))).toBe('ignored');
    expect(
      handleHerdrAttentionEvent(
        envelope({ session_id: 's-1', reason: 'turn_finished' }, 'session.attention'),
      ),
    ).toBe('ignored');
    expect(post).not.toHaveBeenCalled();
    expect(close).not.toHaveBeenCalled();
  });
});

describe('dismissResolvedHerdrAttention', () => {
  it('removes the alert of every pane that no longer needs the user', () => {
    dismissResolvedHerdrAttention(
      AGENT,
      snapshot([
        { pane_id: 'blocked', agent: 'claude', status: 'blocked' },
        { pane_id: 'done', agent: 'claude', status: 'done', turn: 2 },
        { pane_id: 'failed', agent: 'claude', status: 'failed' },
        { pane_id: 'working', agent: 'claude', status: 'working' },
        { pane_id: 'idle', agent: 'claude', status: 'idle' },
        { pane_id: 'shell' },
      ]),
    );

    expect(close.mock.calls.map(([tag]) => tag)).toEqual([
      herdrPaneTag(AGENT, SERVER, 'working'),
      herdrPaneTag(AGENT, SERVER, 'idle'),
      herdrPaneTag(AGENT, SERVER, 'shell'),
    ]);
  });

  it('leaves every alert alone for another event', () => {
    dismissResolvedHerdrAttention(AGENT, alert());

    expect(close).not.toHaveBeenCalled();
  });
});
