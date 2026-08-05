/**
 * The browser Notification API, behind a seam.
 *
 * `Notification` is absent in more places than it looks: iOS Safari exposes it
 * only for a Home-Screen-installed app, and jsdom has none at all. Every entry
 * point therefore answers `unsupported` rather than throwing, and the Settings
 * screen renders that state instead of a button that cannot work.
 *
 * Permission is **never** requested on load — only from the explicit button in
 * Settings. A prompt fired at page load is the pattern browsers now
 * auto-suppress, and it would burn the user's one chance to say yes before they
 * know what they are being asked.
 */
import { terminalPath } from '../routes/sessions/terminalPath';

export type NotificationPermissionState = 'granted' | 'denied' | 'default' | 'unsupported';

export interface AttentionPost {
  sessionId: string;
  agentId?: string;
  title: string;
  body: string;
}

/**
 * Where a notification click navigates. Installed by `main.tsx` rather than
 * imported: the router imports the route tree, which reaches the realtime layer
 * that calls this module, so importing `router` here would close a cycle. Same
 * seam shape as `api/auth.ts`.
 */
type Navigator = (path: string) => void;
let navigate: Navigator | null = null;

export function setAttentionNavigator(next: Navigator | null): void {
  navigate = next;
}

function api(): typeof Notification | null {
  return typeof Notification === 'undefined' ? null : Notification;
}

export function notificationPermission(): NotificationPermissionState {
  const notification = api();
  if (notification === null) return 'unsupported';
  return notification.permission;
}

/**
 * Ask for permission. Must be called from a user gesture.
 *
 * Not single-flighted: the browser itself collapses concurrent requests, and a
 * cached promise here would be worse than useless — a `default` resolved with no
 * prompt shown (the tab was backgrounded) would be memoised and every later
 * attempt would rejoin it, stranding the install silent. That is exactly the bug
 * iOS shipped and had to unpick in PR #537.
 */
export async function requestNotificationPermission(): Promise<NotificationPermissionState> {
  const notification = api();
  if (notification === null) return 'unsupported';
  try {
    return await notification.requestPermission();
  } catch {
    // Older Safari's callback-only signature rejects the promise form.
    return notification.permission;
  }
}

const live = new Map<string, Notification>();

/**
 * Post (or replace) the alert for one session.
 *
 * `tag` is the session id, so a second alert for the same session replaces the
 * first rather than stacking — matching the APNs `collapse-id`/`thread-id` and
 * Android's per-session notification id, all three of which are the session id.
 *
 * Returns whether anything was actually shown, so a caller can tell a dropped
 * post from a delivered one.
 */
export function postAttentionNotification(post: AttentionPost): boolean {
  const notification = api();
  if (notification === null || notification.permission !== 'granted') return false;

  let shown: Notification;
  try {
    // No `renotify`: it is Chromium-only, absent from the DOM typings, and its
    // default is what is wanted anyway — replacing a still-visible alert for the
    // same session should not ring a second time.
    shown = new notification(post.title, { body: post.body, tag: post.sessionId });
  } catch {
    // Chrome throws here when the page has a service worker registered and the
    // constructor path is disallowed. Nothing to recover; the in-app surface
    // still has the event.
    return false;
  }

  live.set(post.sessionId, shown);
  shown.onclose = () => {
    if (live.get(post.sessionId) === shown) live.delete(post.sessionId);
  };
  shown.onclick = () => {
    window.focus();
    shown.close();
    if (post.agentId !== undefined && post.agentId !== '') {
      navigate?.(terminalPath(post.agentId, post.sessionId));
    }
  };
  return true;
}

/** Dismiss a session's alert — the attention it announced is resolved. */
export function closeAttentionNotification(sessionId: string): void {
  const shown = live.get(sessionId);
  if (shown === undefined) return;
  live.delete(sessionId);
  shown.close();
}

/**
 * Dismiss everything this tab posted.
 *
 * Called on sign-out and on leaving the connected state: the alerts are scoped
 * to an account's agents, and one left on screen after a handover would deep-link
 * into a session the new user cannot open.
 */
export function closeAllAttentionNotifications(): void {
  for (const shown of live.values()) shown.close();
  live.clear();
}
