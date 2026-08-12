import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeEventTarget } from './__testing__/fakeEventTarget';
import { observeResume, onResume } from './resumeOnFocus';
import { forceReconnectAllEvents } from './channelRegistry';

vi.mock('./channelRegistry', () => ({ forceReconnectAllEvents: vi.fn() }));

const reconnect = vi.mocked(forceReconnectAllEvents);

const fakeTarget = fakeEventTarget;

let clock = 0;
const now = () => clock;

beforeEach(() => {
  vi.clearAllMocks();
  clock = 0;
});

describe('observeResume', () => {
  it('reconnects when the network comes back', () => {
    const win = fakeTarget();
    const doc = fakeTarget();
    observeResume(now, win, doc);

    win.fire('online');

    expect(reconnect).toHaveBeenCalledTimes(1);
  });

  it('reconnects after a long absence, but not on an alt-tab', () => {
    const win = fakeTarget();
    const doc = fakeTarget();
    observeResume(now, win, doc);

    // A glance at another tab: reconnecting here would be its own handshake
    // storm, and the socket had no time to die.
    doc.hidden = true;
    doc.fire('visibilitychange');
    clock += 5_000;
    doc.hidden = false;
    doc.fire('visibilitychange');
    expect(reconnect).not.toHaveBeenCalled();

    // A closed laptop: the socket can be half-open with no close event, which is
    // the case a browser gives us no ping to detect.
    doc.hidden = true;
    doc.fire('visibilitychange');
    clock += 120_000;
    doc.hidden = false;
    doc.fire('visibilitychange');
    expect(reconnect).toHaveBeenCalledTimes(1);
  });

  it('treats a tab that starts hidden as hidden from installation', () => {
    const win = fakeTarget();
    const doc = fakeTarget();
    doc.hidden = true;
    observeResume(now, win, doc);

    clock += 120_000;
    doc.hidden = false;
    doc.fire('visibilitychange');

    expect(reconnect).toHaveBeenCalledTimes(1);
  });

  it('does not reconnect on a becoming-visible it never saw hidden', () => {
    const win = fakeTarget();
    const doc = fakeTarget();
    observeResume(now, win, doc);

    clock += 120_000;
    doc.fire('visibilitychange');

    expect(reconnect).not.toHaveBeenCalled();
  });

  it('re-opens registered consumer channels alongside the registry sweep', () => {
    // The auto-scan monitor's socket is built by its screen, so the registry
    // cannot reach it — and it is the one that most needs this, being silent for
    // most of a 300 s scan cycle behind a screen a user leaves open.
    const win = fakeTarget();
    const doc = fakeTarget();
    observeResume(now, win, doc);
    const consumer = vi.fn();
    const unsubscribe = onResume(consumer);

    win.fire('online');
    expect(consumer).toHaveBeenCalledTimes(1);
    expect(reconnect).toHaveBeenCalledTimes(1);

    unsubscribe();
    win.fire('online');
    expect(consumer).toHaveBeenCalledTimes(1);
  });

  it('removes both listeners on teardown', () => {
    const win = fakeTarget();
    const doc = fakeTarget();
    const stop = observeResume(now, win, doc);

    expect(win.count('online')).toBe(1);
    expect(doc.count('visibilitychange')).toBe(1);

    stop();

    expect(win.count('online')).toBe(0);
    expect(doc.count('visibilitychange')).toBe(0);
  });
});
