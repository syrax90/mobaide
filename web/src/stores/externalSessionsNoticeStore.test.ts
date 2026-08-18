import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { stubLocalStorage } from '../test/localStorageStub';

// Before importing the store, so zustand's
// `createJSONStorage(() => window.localStorage)` captures the stand-in.
stubLocalStorage();

const { useExternalSessionsNoticeStore } = await import('./externalSessionsNoticeStore');

const STORAGE_KEY = 'mobaide-external-sessions-notice';

function seed(state: Record<string, unknown>) {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ state, version: 0 }));
}

beforeEach(() => {
  useExternalSessionsNoticeStore.setState({ dismissed: false });
  window.localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('hydration', () => {
  it('shows the notice when nothing has been persisted', async () => {
    await useExternalSessionsNoticeStore.persist.rehydrate();

    expect(useExternalSessionsNoticeStore.getState().dismissed).toBe(false);
  });

  it('keeps the notice hidden once the flag was persisted', async () => {
    seed({ dismissed: true });

    await useExternalSessionsNoticeStore.persist.rehydrate();

    expect(useExternalSessionsNoticeStore.getState().dismissed).toBe(true);
  });

  it('shows the notice again when the persisted flag is not a real boolean', async () => {
    // The same untrusted-boundary rule as `themeStore`: only an explicit `true`
    // may suppress, so a truthy string hand-edited into storage must not hide
    // the explainer forever.
    seed({ dismissed: 'yes' });

    await useExternalSessionsNoticeStore.persist.rehydrate();

    expect(useExternalSessionsNoticeStore.getState().dismissed).toBe(false);
  });

  it('shows the notice when the envelope carries no state object', async () => {
    useExternalSessionsNoticeStore.setState({ dismissed: true });
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: 0 }));

    await useExternalSessionsNoticeStore.persist.rehydrate();

    expect(useExternalSessionsNoticeStore.getState().dismissed).toBe(false);
  });
});

describe('dismiss', () => {
  it('writes through so the notice stays gone across a reload', async () => {
    useExternalSessionsNoticeStore.getState().dismiss();

    // A reload re-reads storage rather than the in-memory singleton, so assert
    // the raw envelope and then re-hydrate over a reset store.
    const raw = window.localStorage.getItem(STORAGE_KEY);
    expect(raw).not.toBeNull();
    const envelope = JSON.parse(raw as string) as { state?: unknown };
    expect(envelope.state).toEqual({ dismissed: true });

    // A reload is a default store plus those bytes. `setState` writes back
    // through `persist`, so put the captured envelope back before rehydrating.
    useExternalSessionsNoticeStore.setState({ dismissed: false });
    window.localStorage.setItem(STORAGE_KEY, raw as string);
    await useExternalSessionsNoticeStore.persist.rehydrate();

    expect(useExternalSessionsNoticeStore.getState().dismissed).toBe(true);
  });
});

describe('when storage refuses', () => {
  it('swallows a rejected write and still hides the notice for this session', () => {
    vi.spyOn(window.localStorage, 'setItem').mockImplementation(() => {
      throw new DOMException('quota exceeded', 'QuotaExceededError');
    });

    // `persist` wraps the setter as `set(...args); return setItem()` and its
    // `setItem` carries no guard, so a partitioned, over-quota or private-mode
    // profile throws straight out of the dismiss button's `onClick` — past the
    // focus hand-off back to the heading. `launchPrefsStore` may let that out
    // because its call site catches; this one has to swallow it itself.
    expect(() => {
      useExternalSessionsNoticeStore.getState().dismiss();
    }).not.toThrow();

    // Only durability is lost: zustand's inner `set` lands before `persist`
    // reaches storage, so the explainer stays gone until the next reload.
    expect(useExternalSessionsNoticeStore.getState().dismissed).toBe(true);
  });
});
