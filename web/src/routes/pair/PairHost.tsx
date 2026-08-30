import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router';
import {
  cancelDeviceCode,
  confirmDeviceCode,
  getDeviceCodeStatus,
  mintDeviceCode,
  type DeviceCodeMint,
} from '../../api/agentRepository';
import { CopyableCode } from '../../components/CopyableCode';
import { queryKeys } from '../../lib/queryKeys';
import { useSelectedAgentStore } from '../../stores/selectedAgentStore';
import {
  WAIT_FOR_ONLINE_TIMEOUT_SECONDS,
  deviceCodeBlockedMessage,
  deviceCodeErrorMessage,
} from './pairCopy';
import { useWaitForOnline } from './useWaitForOnline';
import { WaitTimedOutPanel } from './WaitTimedOutPanel';

/**
 * How often the browser asks whether a machine has entered the code.
 *
 * A poll rather than a WebSocket event: the redemption is a transient step in a
 * handshake, not a change to an entity any `*.changed` event describes.
 */
const POLL_INTERVAL_MS = 2_000;

/**
 * Longest delay `setTimeout` stores. Anything larger overflows its 32-bit
 * counter and fires *immediately*, so a deadline further out than this is left
 * unscheduled — no pairing code lives 24 days.
 */
const MAX_TIMEOUT_MS = 2_147_483_647;

function expiredMessage(kind: 'waitingForHost' | 'confirming'): string {
  return kind === 'confirming'
    ? 'This pairing request expired before it was confirmed. Get a new code.'
    : 'That code expired before any machine used it. Get a new one.';
}

type Phase =
  | { kind: 'idle' }
  | { kind: 'waitingForHost'; mint: DeviceCodeMint }
  | {
      kind: 'confirming';
      mint: DeviceCodeMint;
      hostname: string;
      os: string | null;
      /** Set only when approving would *move* an existing agent onto that host. */
      repairAgentName: string | null;
    }
  | { kind: 'claiming' }
  | {
      kind: 'waitingForOnline';
      agentId: string;
      friendlyName: string;
      wsDelivered: boolean | null;
      /** Bumped by "Keep waiting" so the watcher restarts its clock. */
      attempt: number;
    }
  | {
      kind: 'waitTimedOut';
      agentId: string;
      friendlyName: string;
      wsDelivered: boolean | null;
      attempt: number;
    }
  | { kind: 'success'; agentId: string; friendlyName: string }
  | { kind: 'error'; message: string };

/**
 * Pair a computer by typing a code into it, rather than copying one out of it.
 *
 * The direction that survives an air gap: an operator on an SSH jump host or a
 * VM console can retype eight characters into a terminal, but often cannot get
 * a 192-bit nonce back out of one.
 *
 * Unlike `/pair` this route is inside the auth guard — there is no payload in
 * the URL to capture before signing in, and minting requires a session anyway.
 */
export function PairHost() {
  const queryClient = useQueryClient();
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' });

  const activeRequestId =
    phase.kind === 'waitingForHost' || phase.kind === 'confirming'
      ? phase.mint.requestId
      : null;

  const mint = useMutation({
    mutationFn: mintDeviceCode,
    onSuccess: (result) => {
      setPhase({ kind: 'waitingForHost', mint: result });
    },
    onError: (error: unknown) => {
      setPhase({ kind: 'error', message: deviceCodeErrorMessage(error) });
    },
  });

  const status = useQuery({
    queryKey: queryKeys.devicePairing(activeRequestId ?? ''),
    queryFn: () => getDeviceCodeStatus(activeRequestId ?? ''),
    enabled: activeRequestId !== null,
    // Stops on its own once the deadline passes. Without that the poll would
    // outlive the request it is watching — a confirm screen left open keeps
    // asking about a nonce that died minutes ago.
    refetchInterval: (query) => {
      if (activeRequestId === null) return false;
      const expiresAt = query.state.data?.expiresAt;
      if (expiresAt !== undefined && Date.parse(expiresAt) <= Date.now()) return false;
      return POLL_INTERVAL_MS;
    },
    // Nothing here is worth reading from cache: a stale `pending` would hide a
    // machine that has already entered the code.
    staleTime: 0,
    gcTime: 0,
  });

  // The latest deadline the relay stated: the code's while it is unredeemed,
  // the nonce's once a machine has entered it. Falls back to the mint until a
  // poll answers.
  const expiresAt =
    status.data?.expiresAt ??
    (phase.kind === 'waitingForHost' || phase.kind === 'confirming'
      ? phase.mint.expiresAt
      : undefined);
  const deadlineMs = expiresAt === undefined ? null : Date.parse(expiresAt);

  // Expiry is driven locally rather than waited for from a poll. The poll that
  // would cross the deadline can fail — connectivity, or a 404 once the
  // request is gone — and React Query then keeps serving the last successful
  // body, whose `expiresAt` is by now in the past, which disables the interval
  // for good. Nothing else would move the screen off a confirm button that can
  // only 410. Android and iOS bound their poll loops the same way.
  useEffect(() => {
    if (deadlineMs === null || Number.isNaN(deadlineMs)) return;
    const delay = deadlineMs - Date.now();
    if (delay > MAX_TIMEOUT_MS) return;
    const timer = setTimeout(
      () => {
        setPhase((current) =>
          current.kind === 'waitingForHost' || current.kind === 'confirming'
            ? { kind: 'error', message: expiredMessage(current.kind) }
            : current,
        );
      },
      Math.max(0, delay),
    );
    return () => {
      clearTimeout(timer);
    };
  }, [deadlineMs]);

  const statusKind = status.data?.status;
  const statusHostname = status.data?.hostname ?? null;
  const statusOs = status.data?.os ?? null;
  const statusRepairAgentName = status.data?.repairAgentName ?? null;
  useEffect(() => {
    if (statusKind === undefined) return;
    setPhase((current) => {
      // Terminal states are handled from `confirming` too, not just
      // `waitingForHost`: the confirm window is the nonce's, and a user who
      // sat on the screen past it would otherwise be looking at a live button
      // whose only possible outcome is a 410.
      if (current.kind === 'confirming' || current.kind === 'waitingForHost') {
        if (statusKind === 'expired' || statusKind === 'canceled') {
          return { kind: 'error', message: expiredMessage(current.kind) };
        }
      }
      if (current.kind !== 'waitingForHost') return current;
      if (statusKind === 'redeemed') {
        return {
          kind: 'confirming',
          mint: current.mint,
          hostname: statusHostname ?? 'an unnamed machine',
          os: statusOs,
          repairAgentName: statusRepairAgentName,
        };
      }
      return current;
    });
  }, [statusKind, statusHostname, statusOs, statusRepairAgentName]);

  const confirm = useMutation({
    mutationFn: (requestId: string) => confirmDeviceCode(requestId),
    onSuccess: async (result) => {
      // Best-effort: the pairing already succeeded, and failing here would
      // report a broken pairing when only a list refresh broke.
      try {
        await queryClient.refetchQueries({ queryKey: queryKeys.agents() });
      } catch {
        // The watcher refreshes again and the WS event invalidates besides.
      }
      setPhase({
        kind: 'waitingForOnline',
        agentId: result.agentId,
        friendlyName: result.agentFriendlyName,
        wsDelivered: result.wsDelivered,
        attempt: 0,
      });
    },
    onError: (error: unknown) => {
      setPhase({ kind: 'error', message: deviceCodeErrorMessage(error) });
    },
  });

  const wait = useWaitForOnline(
    phase.kind === 'waitingForOnline' ? phase.agentId : null,
    phase.kind === 'waitingForOnline' ? phase.attempt : 0,
  );

  useEffect(() => {
    if (phase.kind !== 'waitingForOnline') return;
    if (wait.online) {
      useSelectedAgentStore.getState().setSelectedAgent(phase.agentId);
      setPhase({
        kind: 'success',
        agentId: phase.agentId,
        friendlyName: phase.friendlyName,
      });
      return;
    }
    if (wait.timedOut) {
      setPhase({
        kind: 'waitTimedOut',
        agentId: phase.agentId,
        friendlyName: phase.friendlyName,
        wsDelivered: phase.wsDelivered,
        attempt: phase.attempt,
      });
    }
  }, [phase, wait.online, wait.timedOut]);

  const abandon = (requestId: string) => {
    // Fire-and-forget: cancelling is a courtesy to the agent waiting on its
    // WebSocket, and a failed cancel changes nothing the user can act on — the
    // request expires on its own either way.
    void cancelDeviceCode(requestId).catch(() => undefined);
  };

  const reset = () => {
    if (activeRequestId !== null) abandon(activeRequestId);
    setPhase({ kind: 'idle' });
  };

  const blockedReason = status.data?.blockedReason ?? null;

  return (
    <section className="auth-card pair-card">
      <h1 className="auth-title">Add an agent</h1>

      {phase.kind === 'idle' && (
        <>
          <p className="auth-subtitle">
            Get a short code here, then type it on the computer you want to work on.
            Nothing has to be copied back off that machine.
          </p>
          <div className="button-row">
            <button
              type="button"
              className="button button-primary"
              disabled={mint.isPending}
              onClick={() => {
                mint.mutate();
              }}
            >
              {mint.isPending ? 'Getting a code…' : 'Get a pairing code'}
            </button>
            <Link className="button" to="/pair">
              I have a code from the agent
            </Link>
          </div>
        </>
      )}

      {phase.kind === 'waitingForHost' && (
        <div role="status" aria-live="polite">
          <p className="auth-subtitle">On the computer you want to pair, run:</p>
          <CopyableCode
            label="Pairing command"
            value={phase.mint.verificationCommand}
            testId="device-code-command"
          />
          <p className="page-note">
            The code is <strong>{phase.mint.code}</strong> if you would rather type it
            yourself. The agent will name this account and ask you to confirm before it
            pairs — only enter the code on a machine you own.
          </p>
          <p className="page-note">Waiting for a machine to enter the code…</p>
          <div className="button-row">
            <button type="button" className="button" onClick={reset}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {phase.kind === 'confirming' && (
        <div role="dialog" aria-labelledby="pair-host-confirm-title">
          <h2 id="pair-host-confirm-title">
            {phase.repairAgentName === null
              ? 'Is this your machine?'
              : `Move ‘${phase.repairAgentName}’ to this machine?`}
          </h2>
          <dl className="device-meta">
            <div>
              <dt>Hostname</dt>
              <dd>{phase.hostname}</dd>
            </div>
            {phase.os !== null && (
              <div>
                <dt>System</dt>
                <dd>{phase.os}</dd>
              </div>
            )}
          </dl>
          {/*
            The name above is whatever the machine that entered the code
            reported, over an endpoint that authenticates nobody — so it helps a
            user recognise their own host and proves nothing by itself. The
            control that actually stops a stranger's machine from joining this
            account is the confirmation the agent prints on its own terminal,
            naming this account before it writes any credentials.
          */}
          <p className="page-note">
            A machine calling itself <strong>{phase.hostname}</strong> entered your code.
            If that is not the computer you just typed it into, do not continue.
          </p>
          {phase.repairAgentName !== null && (
            <p className="page-note">
              That machine reported it is already <strong>‘{phase.repairAgentName}’</strong>.
              Continuing rotates that agent&rsquo;s credentials onto it — if the real
              &lsquo;{phase.repairAgentName}&rsquo; is a different computer, that one goes
              offline.
            </p>
          )}
          {blockedReason !== null && (
            <p className="auth-error" role="alert">
              {deviceCodeBlockedMessage(blockedReason)}
            </p>
          )}
          <div className="button-row">
            <button
              type="button"
              className="button button-primary"
              disabled={blockedReason !== null || confirm.isPending}
              onClick={() => {
                const { requestId } = phase.mint;
                setPhase({ kind: 'claiming' });
                confirm.mutate(requestId);
              }}
            >
              Pair this machine
            </button>
            <button type="button" className="button" onClick={reset}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {phase.kind === 'claiming' && (
        <p className="page-note" role="status" aria-live="polite">
          Pairing…
        </p>
      )}

      {phase.kind === 'waitingForOnline' && (
        <div role="status" aria-live="polite">
          <h2>Waiting for &lsquo;{phase.friendlyName}&rsquo; to come online…</h2>
          <p className="page-note">
            The relay has the pairing request. The agent still has to pick up its
            credentials and connect back.
          </p>
          <p className="page-note">
            Elapsed {wait.elapsedSeconds}s / {WAIT_FOR_ONLINE_TIMEOUT_SECONDS}s
          </p>
          <div className="button-row">
            <button
              type="button"
              className="button"
              onClick={() => {
                setPhase({ kind: 'idle' });
              }}
            >
              Stop waiting
            </button>
          </div>
        </div>
      )}

      {phase.kind === 'waitTimedOut' && (
        <WaitTimedOutPanel
          friendlyName={phase.friendlyName}
          wsDelivered={phase.wsDelivered}
          onKeepWaiting={() => {
            setPhase({
              kind: 'waitingForOnline',
              agentId: phase.agentId,
              friendlyName: phase.friendlyName,
              wsDelivered: phase.wsDelivered,
              attempt: phase.attempt + 1,
            });
          }}
        />
      )}

      {phase.kind === 'success' && (
        <div role="status" aria-live="polite">
          <h2>&lsquo;{phase.friendlyName}&rsquo; paired and online</h2>
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

      {phase.kind === 'error' && (
        <div role="alert">
          <h2>Pairing failed</h2>
          <p className="auth-error">{phase.message}</p>
          <div className="button-row">
            <button
              type="button"
              className="button button-primary"
              onClick={() => {
                setPhase({ kind: 'idle' });
              }}
            >
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
