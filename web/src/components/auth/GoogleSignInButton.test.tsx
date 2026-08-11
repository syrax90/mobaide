import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { stubLocalStorage } from '../../test/localStorageStub';

const gis = vi.hoisted(() => ({
  renders: [] as {
    clientId: string;
    nonce: string;
    theme: string;
    text: string | undefined;
  }[],
  failure: null as Error | null,
  /** The wrapped callback GIS would fire when the popup answers. */
  answer: null as ((credential: string) => void) | null,
}));

vi.mock('../../auth/googleSignIn', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../auth/googleSignIn')>();
  return {
    ...actual,
    renderGoogleButton: (
      options: import('../../auth/googleSignIn').RenderGoogleButtonOptions,
    ) => {
      gis.renders.push({
        clientId: options.clientId,
        nonce: options.nonce,
        theme: options.theme,
        text: options.text,
      });
      gis.answer = options.onCredential;
      return gis.failure === null
        ? Promise.resolve()
        : Promise.reject(gis.failure);
    },
  };
});

vi.mock('../../auth/nonce', () => ({
  generateNonce: () => Promise.resolve('nonce-1'),
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
  gis.renders.length = 0;
  gis.failure = null;
  gis.answer = null;
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
    expect(gis.renders).toHaveLength(0);
  });

  // jsdom ships no `matchMedia`, so the `system` preference resolves light.
  it('asks GIS for the black fill that matches the Apple button on a light surface', async () => {
    mount({ text: 'signin_with' });

    await waitFor(() => {
      expect(gis.renders).toHaveLength(1);
    });
    expect(gis.renders[0]).toEqual({
      clientId: CLIENT_ID,
      nonce: 'nonce-1',
      theme: 'filled_black',
      text: 'signin_with',
    });
  });

  it('asks GIS for the outline theme on a dark surface, where it renders white', async () => {
    useThemeStore.setState({ preference: 'dark' });

    mount();

    await waitFor(() => {
      expect(gis.renders).toHaveLength(1);
    });
    expect(gis.renders[0]?.theme).toBe('outline');
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
