import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { stubLocalStorage } from '../../test/localStorageStub';

const gis = vi.hoisted(() => ({
  inits: [] as { clientId: string; nonce: string }[],
  renders: [] as { theme: string; text: string | undefined }[],
  failure: null as Error | null,
  /** The wrapped callback GIS would fire when the popup answers. */
  answer: null as ((credential: string) => void) | null,
}));

const nonces = vi.hoisted(() => ({ next: 0 }));

vi.mock('../../auth/googleSignIn', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../auth/googleSignIn')>();
  return {
    ...actual,
    initGoogleIdentity: (
      options: import('../../auth/googleSignIn').InitGoogleIdentityOptions,
    ) => {
      gis.inits.push({ clientId: options.clientId, nonce: options.nonce });
      gis.answer = options.onCredential;
      return Promise.resolve();
    },
    renderGoogleButton: (
      options: import('../../auth/googleSignIn').RenderGoogleButtonOptions,
    ) => {
      gis.renders.push({ theme: options.theme, text: options.text });
      return gis.failure === null
        ? Promise.resolve()
        : Promise.reject(gis.failure);
    },
  };
});

// Distinct per call, so a test can tell a reused nonce from a fresh one.
vi.mock('../../auth/nonce', () => ({
  generateNonce: () => Promise.resolve(`nonce-${String(++nonces.next)}`),
}));

// Before importing the theme store the button reads, so zustand's `persist`
// captures the stand-in.
stubLocalStorage();

const { GoogleSignInButton } = await import('./GoogleSignInButton');
const { useThemeStore } = await import('../../stores/themeStore');

const CLIENT_ID = '1234.apps.googleusercontent.com';

function mount(props: Partial<Parameters<typeof GoogleSignInButton>[0]> = {}) {
  const credentials: { credential: string; nonce: string }[] = [];
  const view = render(
    <GoogleSignInButton
      clientId={CLIENT_ID}
      onCredential={(credential, nonce) => {
        credentials.push({ credential, nonce });
      }}
      {...props}
    />,
  );
  return { credentials, view };
}

beforeEach(() => {
  gis.inits.length = 0;
  gis.renders.length = 0;
  gis.failure = null;
  gis.answer = null;
  nonces.next = 0;
  useThemeStore.setState({ preference: 'system' });
});

afterEach(() => {
  cleanup();
});

describe('GoogleSignInButton', () => {
  it('renders nothing when the server has no Google client id', () => {
    const { container } = render(
      <GoogleSignInButton clientId={null} onCredential={() => {}} />,
    );

    expect(container).toBeEmptyDOMElement();
    expect(gis.inits).toHaveLength(0);
    expect(gis.renders).toHaveLength(0);
  });

  it('re-renders the button when the theme flips', async () => {
    mount();
    await waitFor(() => {
      expect(gis.renders).toHaveLength(1);
    });

    act(() => {
      useThemeStore.setState({ preference: 'dark' });
    });

    await waitFor(() => {
      expect(gis.renders).toHaveLength(2);
    });
    expect(gis.renders.map((render) => render.theme)).toEqual([
      'filled_black',
      'outline',
    ]);
  });

  // A theme flip is cosmetic, but the popup it can land in the middle of is
  // not: re-arming GIS would mint a second nonce, and the token coming back
  // carries the first — which the server compares against and rejects.
  it('leaves an in-flight sign-in armed when the theme flips', async () => {
    const { credentials } = mount();
    await waitFor(() => {
      expect(gis.renders).toHaveLength(1);
    });

    // The user opens the popup, then toggles the theme behind it.
    act(() => {
      useThemeStore.setState({ preference: 'dark' });
    });
    await waitFor(() => {
      expect(gis.renders).toHaveLength(2);
    });
    act(() => {
      gis.answer?.('google-credential');
    });

    expect(gis.inits).toHaveLength(1);
    expect(credentials).toEqual([
      { credential: 'google-credential', nonce: 'nonce-1' },
    ]);
  });

  it('reports the credential with the nonce it was minted against', async () => {
    const { credentials } = mount();
    await waitFor(() => {
      expect(gis.answer).not.toBeNull();
    });

    act(() => {
      gis.answer?.('google-credential');
    });

    expect(credentials).toEqual([
      { credential: 'google-credential', nonce: 'nonce-1' },
    ]);
  });

  it('drops a credential that lands after the screen is gone', async () => {
    const { credentials, view } = mount();
    await waitFor(() => {
      expect(gis.answer).not.toBeNull();
    });

    view.unmount();
    act(() => {
      gis.answer?.('late-credential');
    });

    expect(credentials).toEqual([]);
  });

  it('offers the email fallback when GIS never loads', async () => {
    gis.failure = new Error('Google Sign-In could not be loaded');

    mount();

    expect(await screen.findByText(/could not be loaded/)).toBeInTheDocument();
  });
});
