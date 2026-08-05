import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  closeAllAttentionNotifications,
  closeAttentionNotification,
  notificationPermission,
  postAttentionNotification,
  requestNotificationPermission,
  setAttentionNavigator,
} from './notificationService';

type PermissionValue = 'granted' | 'denied' | 'default';

/**
 * Stand-in for the browser Notification API, which jsdom does not implement at
 * all — the same absence the module treats as `unsupported`.
 */
class FakeNotification {
  static permission: PermissionValue = 'granted';
  static requestPermission: () => Promise<PermissionValue> = () =>
    Promise.resolve(FakeNotification.permission);
  static refuseConstruction = false;
  static readonly opened: FakeNotification[] = [];

  readonly title: string;
  readonly body: string;
  readonly tag: string;
  closeCount = 0;
  onclick: (() => void) | null = null;
  onclose: (() => void) | null = null;

  constructor(title: string, options?: { body?: string; tag?: string }) {
    if (FakeNotification.refuseConstruction) throw new Error('constructor disallowed');
    this.title = title;
    this.body = options?.body ?? '';
    this.tag = options?.tag ?? '';
    FakeNotification.opened.push(this);
  }

  close(): void {
    this.closeCount += 1;
    this.onclose?.();
  }
}

function post(sessionId: string, agentId?: string): boolean {
  return postAttentionNotification({
    sessionId,
    ...(agentId !== undefined && { agentId }),
    title: 'Permission needed',
    body: 'api refactor is waiting for your approval',
  });
}

beforeEach(() => {
  FakeNotification.permission = 'granted';
  FakeNotification.refuseConstruction = false;
  FakeNotification.requestPermission = () => Promise.resolve(FakeNotification.permission);
  FakeNotification.opened.length = 0;
  vi.stubGlobal('Notification', FakeNotification);
});

afterEach(() => {
  closeAllAttentionNotifications();
  setAttentionNavigator(null);
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('notificationService', () => {
  it('answers unsupported instead of throwing where the API is absent', async () => {
    vi.stubGlobal('Notification', undefined);

    expect(notificationPermission()).toBe('unsupported');
    await expect(requestNotificationPermission()).resolves.toBe('unsupported');
    expect(post('s-1')).toBe(false);
  });

  it('falls back to the current permission when the promise form rejects', async () => {
    // Older Safari only has the callback signature; the rejection it produces
    // must not read as a denial the user never made.
    FakeNotification.permission = 'denied';
    FakeNotification.requestPermission = () => Promise.reject(new Error('callback only'));

    await expect(requestNotificationPermission()).resolves.toBe('denied');
  });

  it('shows nothing without granted permission', () => {
    FakeNotification.permission = 'default';

    expect(post('s-1')).toBe(false);
    expect(FakeNotification.opened).toHaveLength(0);
  });

  it('tags the alert with the session so the next one replaces it', () => {
    expect(post('s-1')).toBe(true);

    expect(FakeNotification.opened[0]?.title).toBe('Permission needed');
    expect(FakeNotification.opened[0]?.body).toBe('api refactor is waiting for your approval');
    expect(FakeNotification.opened[0]?.tag).toBe('s-1');
  });

  it('reports a refused constructor as undelivered', () => {
    FakeNotification.refuseConstruction = true;

    expect(post('s-1')).toBe(false);
  });

  it('deep-links to the session on click, encoding both ids', () => {
    const focus = vi.spyOn(window, 'focus').mockImplementation(() => undefined);
    const navigate = vi.fn();
    setAttentionNavigator(navigate);
    post('s/1', 'agent 1');

    FakeNotification.opened[0]?.onclick?.();

    expect(navigate).toHaveBeenCalledWith('/agents/agent%201/sessions/s%2F1');
    expect(focus).toHaveBeenCalled();
    expect(FakeNotification.opened[0]?.closeCount).toBe(1);
  });

  it('dismisses without navigating when the event carried no agent', () => {
    vi.spyOn(window, 'focus').mockImplementation(() => undefined);
    const navigate = vi.fn();
    setAttentionNavigator(navigate);
    post('s-1');

    FakeNotification.opened[0]?.onclick?.();

    expect(navigate).not.toHaveBeenCalled();
    expect(FakeNotification.opened[0]?.closeCount).toBe(1);
  });

  it('keeps the replacement alert when the one it replaced closes late', () => {
    post('s-1');
    post('s-1');
    const [first, second] = FakeNotification.opened;

    // The browser collapsed the first by tag; its close event arrives after the
    // replacement was already recorded and must not evict it.
    first?.onclose?.();
    closeAttentionNotification('s-1');

    expect(second?.closeCount).toBe(1);
  });

  it('forgets everything it closed, so a later close cannot fire twice', () => {
    post('s-1');
    post('s-2');

    closeAllAttentionNotifications();
    closeAttentionNotification('s-1');

    expect(FakeNotification.opened.map((shown) => shown.closeCount)).toEqual([1, 1]);
  });
});
