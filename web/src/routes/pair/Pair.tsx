import { useEffect, useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Link, useLocation, useNavigate } from 'react-router';
import { claimPairing } from '../../api/agentRepository';
import { isAuthenticated } from '../../auth/authState';
import { useSessionStore } from '../../auth/sessionStore';
import { TextAreaField } from '../../components/form/TextAreaField';
import { queryKeys } from '../../lib/queryKeys';
import { decodePairingFragment, type ParsedPairingPayload } from '../../protocol/pairingPayload';
import { useSelectedAgentStore } from '../../stores/selectedAgentStore';
import { checkTrustPin, pinTrustKey } from '../../stores/trustPinStore';
import { WAIT_FOR_ONLINE_TIMEOUT_SECONDS, claimErrorMessage, expiresInMessage } from './pairCopy';
import { evaluatePairingInput, type PairState } from './pairMachine';
import { useWaitForOnline } from './useWaitForOnline';
import { WaitTimedOutPanel } from './WaitTimedOutPanel';

/**
 * Where a deep-linked payload waits out the trip through sign-in.
 *
 * `sessionStorage`, matching the user JWT: a pairing nonce is single-use and
 * short-lived, and one left on disk after the tab closed could only ever be
 * replayed. Per-tab is also correct — two tabs opening two different links must
 * not overwrite each other's.
 */
const HANDOFF_KEY = 'mobaide-pairing';

function readHandoff(): string | null {
  try {
    return sessionStorage.getItem(HANDOFF_KEY);
  } catch {
    return null;
  }
}

function writeHandoff(payload: string): void {
  try {
    sessionStorage.setItem(HANDOFF_KEY, payload);
  } catch {
    // Storage denied (private mode, partitioned context). The payload is lost
    // only if the user is signed out and has to bounce through login; a
    // signed-in tab reads it back out of this render pass instead.
  }
}

function clearHandoff(): void {
  try {
    sessionStorage.removeItem(HANDOFF_KEY);
  } catch {
    // Nothing to do — a nonce that cannot be removed is still single-use.
  }
}

function nowSeconds(): number {
  return Math.floor(Date.now() / 1000);
}

/**
 * Pair an agent from a link, or from a pasted code.
 *
 * **This route is deliberately outside both guards.** `requireAuthMiddleware`
 * would bounce a signed-out visitor before this component ever mounts, and the
 * guard builds its `?next=` from `pathname + search` only — a URL fragment
 * never survives it, and putting the nonce in a query string instead would put
 * it in the relay's access log. So the route is public, captures its own
 * fragment, stashes it, scrubs the URL, and only then sends the visitor to sign
 * in. `RequireAnonymous` is equally wrong here: a signed-in user must be able
 * to open a pairing link.
 */
export function Pair() {
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const authenticated = useSessionStore((state) => isAuthenticated(state.authState));
  const [state, setState] = useState<PairState>({ kind: 'idle' });
  const [pasted, setPasted] = useState('');

  // Runs exactly once, before anything can navigate away. The ref rather than an
  // empty dependency list because StrictMode double-invokes mount effects, and
  // the second pass would find the hash already scrubbed and clear the stash.
  const captured = useRef(false);
  useEffect(() => {
    if (captured.current) return;
    captured.current = true;
    if (location.hash === '') return;
    const payload = decodePairingFragment(location.hash);
    if (payload !== null) writeHandoff(payload);
    // `navigate` with `replace`, never `location.hash = ''`: assigning to
    // `location.hash` *pushes* a history entry, so Back would put the nonce
    // right back in the address bar. `replace` is a `replaceState` and keeps
    // the router's own location in sync, which a bare `history.replaceState`
    // would not.
    void navigate(
      { pathname: location.pathname, search: location.search, hash: '' },
      { replace: true },
    );
  }, [location.hash, location.pathname, location.search, navigate]);

  useEffect(() => {
    if (authenticated) return;
    void navigate(`/login?next=${encodeURIComponent('/pair')}`, { replace: true });
  }, [authenticated, navigate]);

  useEffect(() => {
    if (!authenticated || state.kind !== 'idle') return;
    const stored = readHandoff();
    if (stored === null) return;
    // Consumed on read: a nonce is single-use, so a failed attempt must not be
    // silently retried on the next mount with copy that no longer matches.
    clearHandoff();
    setState(evaluatePairingInput(stored, nowSeconds(), checkTrustPin));
  }, [authenticated, state.kind]);

  const claim = useMutation({
    mutationFn: (payload: ParsedPairingPayload) =>
      claimPairing(payload.nonce, payload.trustKeyFp),
    onSuccess: async (result) => {
      // Best-effort, iOS's `try?` rather than Android's throwing refresh: the
      // claim has already succeeded, and failing it here would tell the user
      // pairing broke when only a list refresh did.
      try {
        await queryClient.refetchQueries({ queryKey: queryKeys.agents() });
      } catch {
        // The watcher refreshes again and the WS event invalidates besides.
      }
      setState({
        kind: 'waitingForOnline',
        agentId: result.agentId,
        friendlyName: result.agentFriendlyName,
        wsDelivered: result.wsDelivered,
        attempt: 0,
      });
    },
    onError: (error: unknown) => {
      setState({ kind: 'error', message: claimErrorMessage(error) });
    },
  });

  const wait = useWaitForOnline(
    state.kind === 'waitingForOnline' ? state.agentId : null,
    state.kind === 'waitingForOnline' ? state.attempt : 0,
  );

  useEffect(() => {
    if (state.kind !== 'waitingForOnline') return;
    if (wait.online) {
      // Point the sessions list at what was just paired, so "Open sessions"
      // lands on the new agent rather than whichever one was sticky before.
      useSelectedAgentStore.getState().setSelectedAgent(state.agentId);
      setState({
        kind: 'success',
        agentId: state.agentId,
        friendlyName: state.friendlyName,
      });
      return;
    }
    if (wait.timedOut) {
      setState({
        kind: 'waitTimedOut',
        agentId: state.agentId,
        friendlyName: state.friendlyName,
        wsDelivered: state.wsDelivered,
        attempt: state.attempt,
      });
    }
  }, [state, wait.online, wait.timedOut]);

  if (!authenticated) return null;

  const reset = () => {
    setPasted('');
    setState({ kind: 'idle' });
  };

  return (
    <section className="auth-card pair-card">
      <h1 className="auth-title">Pair an agent</h1>

      {state.kind === 'idle' && (
        <>
          <p className="auth-subtitle">
            Run <code>mobaide-agent show-qr</code> on the machine you want to work on,
            then paste the <code>MOBA1|…</code> line it prints — or open the link next
            to it.
          </p>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              setState(evaluatePairingInput(pasted, nowSeconds(), checkTrustPin));
            }}
          >
            <TextAreaField
              label="Pairing code"
              value={pasted}
              onChange={setPasted}
              placeholder="MOBA1|https://…"
              hint="Line breaks from a wrapped terminal are fine — they are stripped before the code is read."
              className="pair-input"
              autoCapitalize="off"
              autoCorrect="off"
            />
            <div className="button-row">
              <button
                type="submit"
                className="button button-primary"
                disabled={pasted.trim() === ''}
              >
                Continue
              </button>
              <Link className="button" to="/agents">
                All agents
              </Link>
            </div>
          </form>
        </>
      )}

      {state.kind === 'confirming' && (
        <div role="dialog" aria-labelledby="pair-confirm-title">
          <h2 id="pair-confirm-title">Pair this agent?</h2>
          <dl className="device-meta">
            <div>
              <dt>Relay</dt>
              <dd>{state.payload.relayUrl}</dd>
            </div>
            <div>
              <dt>Code on the agent host</dt>
              <dd className="device-id">{state.payload.nonce.slice(0, 8)}…</dd>
            </div>
            <div>
              <dt>Expires</dt>
              <dd>{expiresInMessage(state.payload.expUnix, nowSeconds())}</dd>
            </div>
          </dl>
          {/*
            RFC 8628 §5.4's remote-phishing mitigation, and the reason this
            confirm step exists at all. A pairing link works for whoever opens
            it: an attacker can run `pair-qr` against the real relay on their
            own machine and mail the link on, and every field above would look
            correct, because the relay really is ours. Only the code the user
            can read off their own terminal distinguishes the two.
          */}
          <p className="page-note">
            Check those first characters match the code printed on the machine you are
            pairing. If someone sent you this link, do not continue — pairing gives them
            a session on your account.
          </p>
          <div className="button-row">
            <button
              type="button"
              className="button button-primary"
              onClick={() => {
                const { payload } = state;
                // Trust-on-first-use is recorded here, not during validation:
                // this click is the first moment the user has actually vouched
                // for the payload, and a pin written earlier could be planted
                // by any link they merely opened.
                pinTrustKey(payload.relayUrl, payload.trustKeyFp);
                setState({ kind: 'claiming', payload });
                claim.mutate(payload);
              }}
            >
              Pair
            </button>
            <button type="button" className="button" onClick={reset}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {state.kind === 'claiming' && (
        <p className="page-note" role="status" aria-live="polite">
          Claiming the pairing code…
        </p>
      )}

      {state.kind === 'waitingForOnline' && (
        <div role="status" aria-live="polite">
          <h2>Waiting for &lsquo;{state.friendlyName}&rsquo; to come online…</h2>
          <p className="page-note">
            The relay has the pairing request. The agent still has to pick up its
            credentials and connect back.
          </p>
          <p className="page-note">
            Elapsed {wait.elapsedSeconds}s / {WAIT_FOR_ONLINE_TIMEOUT_SECONDS}s
          </p>
          <div className="button-row">
            <button type="button" className="button" onClick={reset}>
              Stop waiting
            </button>
          </div>
        </div>
      )}

      {state.kind === 'waitTimedOut' && (
        <WaitTimedOutPanel
          friendlyName={state.friendlyName}
          wsDelivered={state.wsDelivered}
          onKeepWaiting={() => {
            setState({
              kind: 'waitingForOnline',
              agentId: state.agentId,
              friendlyName: state.friendlyName,
              wsDelivered: state.wsDelivered,
              attempt: state.attempt + 1,
            });
          }}
        />
      )}

      {state.kind === 'success' && (
        <div role="status" aria-live="polite">
          <h2>&lsquo;{state.friendlyName}&rsquo; paired and online</h2>
          <div className="button-row">
            <Link className="button button-primary" to="/">
              Open sessions
            </Link>
            <Link className="button" to="/agents">
              All agents
            </Link>
          </div>
        </div>
      )}

      {state.kind === 'error' && (
        <div role="alert">
          <h2>Pairing failed</h2>
          <p className="auth-error">{state.message}</p>
          <div className="button-row">
            <button type="button" className="button button-primary" onClick={reset}>
              Try again
            </button>
            <Link className="button" to="/agents">
              All agents
            </Link>
          </div>
        </div>
      )}
    </section>
  );
}
