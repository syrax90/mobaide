import { StrictMode } from 'react';
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const sdk = vi.hoisted(() => ({
  initCalls: [] as string[],
  initFailure: null as Error | null,
  signInNonces: [] as string[],
  answer: null as (() => Promise<string>) | null,
}));

const nonces = vi.hoisted(() => ({ queue: [] as string[] }));

// `importOriginal` keeps the real `AppleSignInCancelledError`: the button
// discriminates on the class identity, so a stubbed sentinel would let a
// broken `instanceof` pass.
vi.mock('../../auth/appleSignIn', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../auth/appleSignIn')>();
  return {
    ...actual,
    initAppleIdentity: (clientId: string) => {
      sdk.initCalls.push(clientId);
      return sdk.initFailure === null
        ? Promise.resolve()
        : Promise.reject(sdk.initFailure);
    },
    signInWithApple: (nonce: string) => {
      sdk.signInNonces.push(nonce);
      return sdk.answer === null
        ? Promise.resolve('apple-id-token')
        : sdk.answer();
    },
  };
});

vi.mock('../../auth/nonce', () => ({
  generateNonce: () => Promise.resolve(nonces.queue.shift() ?? 'nonce-exhausted'),
}));

const { AppleSignInCancelledError } = await import('../../auth/appleSignIn');
const { AppleSignInButton } = await import('./AppleSignInButton');

const CLIENT_ID = 'com.mobaide.app.web';

interface Reported {
  credentials: { idToken: string; nonce: string }[];
  errors: string[];
  /** Tears the screen down while a popup may still be open. */
  unmount: () => void;
}

function mount(
  props: Partial<Parameters<typeof AppleSignInButton>[0]> = {},
  { strict = false }: { strict?: boolean } = {},
): Reported {
  const reported: Reported = {
    credentials: [],
    errors: [],
    unmount: () => {},
  };
  const subject = (
    <AppleSignInButton
      clientId={CLIENT_ID}
      onCredential={(idToken, nonce) => {
        reported.credentials.push({ idToken, nonce });
      }}
      onError={(message) => {
        reported.errors.push(message);
      }}
      {...props}
    />
  );
  const view = render(strict ? <StrictMode>{subject}</StrictMode> : subject);
  reported.unmount = view.unmount;
  return reported;
}

const button = () => screen.getByRole('button', { name: /Apple/ });

/**
 * A popup the test settles by hand.
 *
 * Apple's SDK resolves through a `postMessage` listener with no timeout, so
 * the interval between the click and the answer is unbounded in production;
 * these tests need to act inside it.
 */
function deferredSignIn(): {
  resolve: (idToken: string) => void;
  reject: (cause: unknown) => void;
} {
  let resolve!: (idToken: string) => void;
  let reject!: (cause: unknown) => void;
  const popup = new Promise<string>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  sdk.answer = () => popup;
  return { resolve, reject };
}

/** Let a settled promise chain run to its end — a macrotask drains it all. */
const flush = async (): Promise<void> => {
  await act(async () => {
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
  });
};

beforeEach(() => {
  sdk.initCalls.length = 0;
  sdk.initFailure = null;
  sdk.signInNonces.length = 0;
  sdk.answer = null;
  nonces.queue = ['nonce-1', 'nonce-2'];
});

afterEach(cleanup);

describe('AppleSignInButton', () => {
  it('renders nothing until a server configures a Services ID', () => {
    const { container } = render(
      <AppleSignInButton clientId={null} onCredential={() => {}} onError={() => {}} />,
    );

    expect(container).toBeEmptyDOMElement();
    // Nor may it reach for the SDK: an unconfigured instance loads no Apple
    // script at all, and its CSP would block one.
    expect(sdk.initCalls).toEqual([]);
  });

  it('stays disabled until both the SDK and the nonce are ready', async () => {
    mount();

    expect(button()).toBeDisabled();
    await waitFor(() => {
      expect(button()).toBeEnabled();
    });
    expect(sdk.initCalls).toEqual([CLIENT_ID]);
  });

  it('hands back the token together with the nonce the popup was opened with', async () => {
    const reported = mount();
    await waitFor(() => {
      expect(button()).toBeEnabled();
    });

    fireEvent.click(button());

    await waitFor(() => {
      expect(reported.credentials).toEqual([
        { idToken: 'apple-id-token', nonce: 'nonce-1' },
      ]);
    });
    // The nonce reaching the server has to be the one bound into the token,
    // so the pair must come from the same attempt.
    expect(sdk.signInNonces).toEqual(['nonce-1']);
  });

  it('mints a fresh nonce for the next attempt', async () => {
    mount();
    await waitFor(() => {
      expect(button()).toBeEnabled();
    });
    fireEvent.click(button());
    // The replacement is generated in the attempt's `finally`; the queue
    // draining is what says it landed.
    await waitFor(() => {
      expect(nonces.queue).toHaveLength(0);
    });

    fireEvent.click(button());

    // A nonce is single-use by intent: the server binds it to one token, so a
    // replayed one is a 401 the second time round.
    await waitFor(() => {
      expect(sdk.signInNonces).toEqual(['nonce-1', 'nonce-2']);
    });
  });

  it('says nothing when the user dismisses the popup', async () => {
    sdk.answer = () => Promise.reject(new AppleSignInCancelledError());
    const reported = mount();
    await waitFor(() => {
      expect(button()).toBeEnabled();
    });

    fireEvent.click(button());

    await waitFor(() => {
      expect(sdk.signInNonces).toEqual(['nonce-1']);
    });
    expect(reported.errors).toEqual([]);
    expect(reported.credentials).toEqual([]);
  });

  it('surfaces a real failure with the message the caller can show', async () => {
    sdk.answer = () =>
      Promise.reject(new Error('Your browser blocked the Apple sign-in window.'));
    const reported = mount();
    await waitFor(() => {
      expect(button()).toBeEnabled();
    });

    fireEvent.click(button());

    await waitFor(() => {
      expect(reported.errors).toEqual([
        'Your browser blocked the Apple sign-in window.',
      ]);
    });
  });

  it('drops a credential that arrives after the screen is gone', async () => {
    // `markUserLoggedIn` is unconditional, so a token landing on a screen the
    // user has already left would overwrite whatever session replaced it.
    const popup = deferredSignIn();
    const reported = mount();
    await waitFor(() => {
      expect(button()).toBeEnabled();
    });
    fireEvent.click(button());
    await waitFor(() => {
      expect(sdk.signInNonces).toEqual(['nonce-1']);
    });

    reported.unmount();
    popup.resolve('late-token');
    await flush();

    expect(reported.credentials).toEqual([]);
  });

  it('reports no error once the screen is gone', async () => {
    // The popup outliving the screen is the normal case for a user who walked
    // away; an error toast on a route they have left is noise about nothing.
    const popup = deferredSignIn();
    const reported = mount();
    await waitFor(() => {
      expect(button()).toBeEnabled();
    });
    fireEvent.click(button());
    await waitFor(() => {
      expect(sdk.signInNonces).toEqual(['nonce-1']);
    });

    reported.unmount();
    popup.reject(new Error('Your browser blocked the Apple sign-in window.'));
    await flush();

    expect(reported.errors).toEqual([]);
  });

  it('locks itself for the length of the popup round trip', async () => {
    const popup = deferredSignIn();
    mount();
    await waitFor(() => {
      expect(button()).toBeEnabled();
    });

    fireEvent.click(button());

    // A second press would open a second popup against a nonce the first one
    // has already spent.
    await waitFor(() => {
      expect(button()).toBeDisabled();
    });

    popup.resolve('apple-id-token');

    await waitFor(() => {
      expect(button()).toBeEnabled();
    });
  });

  // `main.tsx` wraps the whole tree in `<StrictMode>`, so this — not the bare
  // render above — is the shape every real user gets. StrictMode runs each
  // effect setup → cleanup → setup on mount, which a ref-backed liveness flag
  // latches off unless the setup half restores it: refs survive the remount.
  describe('under StrictMode, as the app mounts it', () => {
    it('hands back the token together with the nonce the popup was opened with', async () => {
      const reported = mount({}, { strict: true });
      await waitFor(() => {
        expect(button()).toBeEnabled();
      });

      fireEvent.click(button());

      await waitFor(() => {
        expect(reported.credentials).toHaveLength(1);
      });
      // The discarded first effect pass still drew from the nonce queue, so
      // the live nonce is the second one; the pair must stay internally
      // consistent whichever it is.
      expect(sdk.signInNonces).toEqual(['nonce-2']);
      expect(reported.credentials).toEqual([
        { idToken: 'apple-id-token', nonce: 'nonce-2' },
      ]);
    });

    it('accepts a second press once the first sign-in settles', async () => {
      const popup = deferredSignIn();
      mount({}, { strict: true });
      await waitFor(() => {
        expect(button()).toBeEnabled();
      });
      fireEvent.click(button());
      await waitFor(() => {
        expect(button()).toBeDisabled();
      });

      popup.resolve('apple-id-token');

      // A button that never re-enables is a dead sign-in surface for the rest
      // of the page's life — the user's only recourse is a reload.
      await waitFor(() => {
        expect(button()).toBeEnabled();
      });
    });
  });

  it('offers no clickable button when the SDK never loads', async () => {
    sdk.initFailure = new Error('Sign in with Apple could not be loaded');
    mount();

    await waitFor(() => {
      expect(screen.getByText(/could not be loaded/)).toBeInTheDocument();
    });
    // Enabling it would open a popup against an SDK that is not there.
    expect(button()).toBeDisabled();
  });
});
