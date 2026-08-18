import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { stubLocalStorage } from '../../test/localStorageStub';

// Before any persisted store is imported.
stubLocalStorage();

const { useExternalSessionsNoticeStore } = await import(
  '../../stores/externalSessionsNoticeStore'
);
const { ExternalSessionsNotice } = await import('./ExternalSessionsNotice');

const gotIt = () => screen.getByRole('button', { name: 'Got it' });

beforeEach(() => {
  useExternalSessionsNoticeStore.setState({ dismissed: false });
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('ExternalSessionsNotice', () => {
  it('explains the limit while the flag is unset', () => {
    render(<ExternalSessionsNotice />);

    expect(
      screen.getByRole('heading', { name: /Sessions started outside Mobaide/ }),
    ).toBeInTheDocument();
    expect(screen.getByText(/can only open sessions its own agent started/)).toBeInTheDocument();
    expect(gotIt()).toBeInTheDocument();
  });

  it('renders nothing once the notice has been dismissed', () => {
    useExternalSessionsNoticeStore.setState({ dismissed: true });

    render(<ExternalSessionsNotice />);

    expect(screen.queryByRole('heading', { name: /Sessions started outside Mobaide/ })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Got it' })).toBeNull();
  });

  it('disappears on "Got it" and does not come back on a remount', () => {
    const onDismiss = vi.fn();
    const first = render(<ExternalSessionsNotice onDismiss={onDismiss} />);

    fireEvent.click(gotIt());

    expect(screen.queryByRole('button', { name: 'Got it' })).toBeNull();
    // The host moves focus off the button it just unmounted.
    expect(onDismiss).toHaveBeenCalledTimes(1);

    // A fresh mount reads the same store: the dismissal is permanent, not a
    // per-render bit of local state.
    first.unmount();
    render(<ExternalSessionsNotice />);

    expect(screen.queryByRole('button', { name: 'Got it' })).toBeNull();
  });

  it('still hands focus back when storage refuses the write', () => {
    const onDismiss = vi.fn();
    vi.spyOn(window.localStorage, 'setItem').mockImplementation(() => {
      throw new DOMException('quota exceeded', 'QuotaExceededError');
    });
    render(<ExternalSessionsNotice onDismiss={onDismiss} />);

    fireEvent.click(gotIt());

    // `persist` calls `setItem` unguarded on every `set`, so a refusing profile
    // — partitioned, over quota, private mode — used to throw out of `dismiss()`
    // and never reach `onDismiss?.()`, parking focus on `<body>` instead of the
    // page heading. The store's own try/catch is what keeps this reachable.
    expect(onDismiss).toHaveBeenCalledTimes(1);
    // The in-memory flip lands before storage is touched, so the banner goes
    // regardless; only durability across a reload is lost.
    expect(screen.queryByRole('button', { name: 'Got it' })).toBeNull();
  });
});
