import { afterEach, describe, expect, it } from 'vitest';
import { fakeEventTarget, type FakeEventTarget } from './__testing__/fakeEventTarget';
import { observeTabVisibility, onTabVisibility, tabHidden } from './tabVisibility';

const fakeDoc = fakeEventTarget;

const uninstallers: (() => void)[] = [];

function install(doc: FakeEventTarget): void {
  uninstallers.push(observeTabVisibility(doc));
}

afterEach(() => {
  for (const uninstall of uninstallers.splice(0)) uninstall();
});

describe('observeTabVisibility', () => {

  it('seeds from a document that was already hidden at installation', () => {
    const doc = fakeDoc();
    doc.hidden = true;
    install(doc);

    expect(tabHidden()).toBe(true);
  });

  it('notifies subscribers on both transitions', () => {
    const doc = fakeDoc();
    install(doc);
    const seen: boolean[] = [];
    onTabVisibility((hidden) => seen.push(hidden));

    doc.hidden = true;
    doc.fire('visibilitychange');
    doc.hidden = false;
    doc.fire('visibilitychange');

    expect(seen).toEqual([true, false]);
  });

  it('removes the listener and reverts to visible on teardown', () => {
    // Reverting matters: a torn-down installation must not strand subscribers
    // believing they sit in a hidden tab, and a test file must not leak one
    // case's hidden document into the next.
    const doc = fakeDoc();
    doc.hidden = true;
    const uninstall = observeTabVisibility(doc);

    expect(tabHidden()).toBe(true);

    uninstall();

    expect(doc.count('visibilitychange')).toBe(0);
    expect(tabHidden()).toBe(false);
  });

  it('keeps subscribers registered across an uninstall and re-install', () => {
    // StrictMode runs the installing effect as install -> uninstall -> install,
    // and a model that subscribed in between has to survive it.
    const first = fakeDoc();
    const uninstall = observeTabVisibility(first);
    const seen: boolean[] = [];
    onTabVisibility((hidden) => seen.push(hidden));
    uninstall();

    const second = fakeDoc();
    install(second);
    second.hidden = true;
    second.fire('visibilitychange');

    expect(seen).toEqual([true]);
  });
});
