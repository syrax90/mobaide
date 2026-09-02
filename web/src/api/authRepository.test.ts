import { beforeEach, describe, expect, it, vi } from 'vitest';

// Hand-written stand-in for the typed client: `relayClient` is built with the
// SPA's relative base URL, which the undici Request under vitest refuses to
// parse (see clients.test.ts). The repository's own mapping is what is under
// test here, so the queue below plays the part of the wire.
const relay = vi.hoisted(() => ({
  results: [] as { data?: unknown; error?: unknown; response: Response }[],
  calls: [] as { path: string; body: Record<string, unknown> }[],
}));

vi.mock('./clients', () => ({
  relayClient: {
    POST: (path: string, init: { body?: Record<string, unknown> }) => {
      relay.calls.push({ path, body: init.body ?? {} });
      const next = relay.results.shift();
      if (next === undefined) {
        return Promise.reject(new Error(`unexpected POST ${path}`));
      }
      return Promise.resolve(next);
    },
  },
}));

// The landing page's click-attribution cookie, as the repository sees it.
// jsdom's jar is empty, so the default (`null`) is also what a visitor who
// never accepted the banner produces.
const attribution = vi.hoisted(() => ({
  value: null as null | {
    click_id: string | null;
    click_id_kind: 'gclid' | 'wbraid' | 'gbraid' | null;
    clicked_at: number | null;
    signature: string | null;
  },
}));

vi.mock('../auth/adAttribution', () => ({
  readAdAttribution: () => attribution.value,
}));

const {
  login,
  loginWithApple,
  loginWithGoogle,
  register,
  resendVerification,
  verifyEmail,
  VerificationRequiredError,
} = await import('./authRepository');
const { AdminLoginNotSupportedError, ADMIN_LOGIN_MESSAGE, ApiError } = await import(
  './errors'
);

function succeed(data: unknown): void {
  relay.results.push({ data, response: new Response(null, { status: 200 }) });
}

function failWith(
  status: number,
  error: unknown,
  headers: Record<string, string> = {},
): void {
  relay.results.push({ error, response: new Response(null, { status, headers }) });
}

const okBody = {
  access_token: 'access-1',
  refresh_token: 'refresh-1',
  user_id: 'user-1',
  email: 'user@example.com',
  role: 'viewer',
  status: 'ok',
  scheduled_deletion_at: null,
};

beforeEach(() => {
  relay.results.length = 0;
  relay.calls.length = 0;
  attribution.value = null;
});

describe('session-minting calls', () => {
  it('maps a login response into a session', async () => {
    succeed({ ...okBody, scheduled_deletion_at: '2026-09-01T00:00:00+00:00' });

    const outcome = await login('user@example.com', 'hunter22');

    expect(outcome).toEqual({
      kind: 'session',
      session: {
        accessToken: 'access-1',
        userId: 'user-1',
        email: 'user@example.com',
        role: 'viewer',
        scheduledDeletionAt: '2026-09-01T00:00:00+00:00',
      },
    });
  });

  it('opts every session-minting call into the refreshable session', async () => {
    succeed(okBody);
    succeed(okBody);
    succeed(okBody);
    succeed(okBody);
    succeed(okBody);

    await register('user@example.com', 'hunter22');
    await login('user@example.com', 'hunter22');
    await loginWithGoogle('google-id-token');
    await loginWithApple('apple-id-token', 'nonce-1');
    await verifyEmail('user@example.com', '123456');

    expect(relay.calls.map((call) => call.body.refreshable)).toEqual([
      true,
      true,
      true,
      true,
      true,
    ]);
  });

  it('sends the Apple nonce alongside the token it was minted with', async () => {
    succeed(okBody);

    await loginWithApple('apple-id-token', 'nonce-1');

    // Where the Google twin sends none: the server verifies this against the
    // token's own claim on every Apple sign-in, not only on re-auth.
    expect(relay.calls).toEqual([
      {
        path: '/api/auth/apple',
        body: {
          id_token: 'apple-id-token',
          nonce: 'nonce-1',
          refreshable: true,
          session_transport: 'cookie',
        },
      },
    ]);
  });
});

describe('ad attribution forwarding', () => {
  it('forwards ad_attribution on register, Google and Apple only', async () => {
    attribution.value = {
      click_id: 'Cj0K_abc-123',
      click_id_kind: 'gclid',
      clicked_at: 1756800000,
      signature: 'a'.repeat(43),
    };
    for (let i = 0; i < 5; i += 1) succeed(okBody);

    await register('user@example.com', 'hunter22');
    await login('user@example.com', 'hunter22');
    await loginWithGoogle('google-id-token');
    await loginWithApple('apple-id-token', 'nonce-1');
    await verifyEmail('user@example.com', '123456');

    // Login and verify cannot create an account, so they never carry it.
    expect(relay.calls.map((call) => call.body.ad_attribution)).toEqual([
      attribution.value,
      undefined,
      attribution.value,
      attribution.value,
      undefined,
    ]);
  });

  it('omits the key entirely when the cookie is absent', async () => {
    succeed(okBody);

    await register('user@example.com', 'hunter22');

    expect('ad_attribution' in relay.calls[0]!.body).toBe(false);
  });
});

describe('admin rejection', () => {
  it('rejects an admin before any session is produced', async () => {
    succeed({ ...okBody, role: 'admin' });

    await expect(login('admin@example.com', 'hunter22')).rejects.toThrow(
      AdminLoginNotSupportedError,
    );
  });

  it('revokes the cookie the server already set for the rejected admin', async () => {
    // The rejection is client-side and the server has no admin gate on these
    // routes, so the session cookie is installed before this check runs.
    // Before the cookie the throw was enough — the body's refresh token was
    // never read. Now the credential outlives the rejection unless revoked,
    // and the next tab would restore the admin.
    const logout = vi.fn(() => Promise.resolve({ status: 204, ok: true } as Response));
    vi.stubGlobal('fetch', logout);
    succeed({ ...okBody, role: 'admin' });

    await expect(login('admin@example.com', 'hunter22')).rejects.toThrow(
      AdminLoginNotSupportedError,
    );

    expect(logout).toHaveBeenCalledWith('/api/auth/logout', expect.anything());
    vi.unstubAllGlobals();
  });

  it('rejects an admin arriving through Google with the shared copy', async () => {
    succeed({ ...okBody, role: 'admin' });

    await expect(loginWithGoogle('google-id-token')).rejects.toThrow(
      ADMIN_LOGIN_MESSAGE,
    );
  });

  it('rejects an admin arriving through Apple with the shared copy', async () => {
    succeed({ ...okBody, role: 'admin' });

    await expect(loginWithApple('apple-id-token', 'nonce-1')).rejects.toThrow(
      ADMIN_LOGIN_MESSAGE,
    );
  });
});

describe('verification routing', () => {
  it('routes a verification_required registration to the code screen', async () => {
    succeed({
      ...okBody,
      access_token: null,
      refresh_token: null,
      status: 'verification_required',
    });

    expect(await register('user@example.com', 'hunter22')).toEqual({
      kind: 'verificationRequired',
      email: 'user@example.com',
    });
  });

  it('routes a 403 login to the code screen with the address the server used', async () => {
    failWith(403, {
      detail: { error: 'verification_required', email: 'normalised@example.com' },
    });

    await expect(login('User@Example.com', 'hunter22')).rejects.toThrow(
      VerificationRequiredError,
    );
  });

  it('falls back to the typed address when the 403 carries none', async () => {
    failWith(403, { detail: { error: 'verification_required' } });

    await expect(login('typed@example.com', 'hunter22')).rejects.toMatchObject({
      email: 'typed@example.com',
    });
  });

  it('surfaces a wrong-password 401 as an ordinary API error', async () => {
    failWith(401, { detail: 'Invalid email or password' });

    await expect(login('user@example.com', 'wrong')).rejects.toBeInstanceOf(ApiError);
  });
});

describe('resendVerification', () => {
  it('reports the server cooldown instead of failing on a 429', async () => {
    failWith(429, { detail: { error: 'resend_rate_limited' } }, { 'Retry-After': '42' });

    expect(await resendVerification('user@example.com')).toEqual({
      cooldownSeconds: 42,
      rateLimited: true,
    });
  });

  it('falls back to the 60s window when the 429 carries no wait', async () => {
    failWith(429, { detail: { error: 'resend_rate_limited' } });

    expect(await resendVerification('user@example.com')).toEqual({
      cooldownSeconds: 60,
      rateLimited: true,
    });
  });

  it('still raises for a failure that is not a rate limit', async () => {
    failWith(500, { detail: 'Internal Server Error' });

    await expect(resendVerification('user@example.com')).rejects.toBeInstanceOf(
      ApiError,
    );
  });
});
