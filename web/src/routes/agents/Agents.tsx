import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router';
import {
  createAgent,
  deleteAgent,
  listAgents,
  reregisterAgent,
  type Agent,
  type AgentCredentials,
} from '../../api/agentRepository';
import { clearAgentToken } from '../../auth/agentTokens';
import { ErrorState } from '../../components/ErrorState';
import { Modal } from '../../components/Modal';
import { queryKeys, wsBackedQueryOptions } from '../../lib/queryKeys';
import { disconnectAgentEvents } from '../../realtime';
import { useLastSessionStore } from '../../stores/lastSessionStore';
import { clearRecentPaths } from '../../stores/recentPaths';
import { useSelectedAgentStore } from '../../stores/selectedAgentStore';
import { useToastStore } from '../../stores/toastStore';
import { AddAgentDialog } from './AddAgentDialog';
import { AgentCard } from './AgentCard';
import { AgentCredentialsPanel } from './AgentCredentialsPanel';
import { AgentsEmptyState } from './AgentsEmptyState';
import { InstallCommandsPanel } from './InstallCommandsPanel';

type Panel =
  | { kind: 'none' }
  | { kind: 'add' }
  | { kind: 'install' }
  | { kind: 'credentials'; title: string; credentials: AgentCredentials }
  | { kind: 'confirmReregister'; agent: Agent }
  | { kind: 'confirmDelete'; agent: Agent };

/**
 * Everything an account can do to its agents, minus pairing itself (`/pair`).
 *
 * One panel is open at a time, as a union rather than a bag of booleans: the
 * credentials panel must replace the add dialog rather than stack on it, and a
 * token that is only ever shown once should not be reachable from two states.
 */
export function Agents() {
  const queryClient = useQueryClient();
  const [panel, setPanel] = useState<Panel>({ kind: 'none' });
  const removeCancelRef = useRef<HTMLButtonElement>(null);

  const agents = useQuery({
    queryKey: queryKeys.agents(),
    queryFn: listAgents,
    ...wsBackedQueryOptions,
  });

  const invalidateAgents = async () => {
    await queryClient.invalidateQueries({ queryKey: queryKeys.agents() });
  };

  const create = useMutation({
    mutationFn: createAgent,
    onSuccess: async (credentials) => {
      setPanel({ kind: 'credentials', title: 'Agent created', credentials });
      await invalidateAgents();
    },
    onError: () => {
      setPanel({ kind: 'none' });
      useToastStore.getState().show('Could not create the agent', 'error');
    },
  });

  const reregister = useMutation({
    mutationFn: (agent: Agent) => reregisterAgent(agent.agentId, agent.name),
    // No invalidation: rotating the token changes nothing the list renders, and
    // both mobile clients leave the row untouched for the same reason.
    onSuccess: (credentials) => {
      setPanel({ kind: 'credentials', title: 'New credentials', credentials });
    },
    onError: () => {
      setPanel({ kind: 'none' });
      useToastStore.getState().show('Could not re-register the agent', 'error');
    },
  });

  const remove = useMutation({
    mutationFn: (agent: Agent) => deleteAgent(agent.agentId),
    onSuccess: async (_result, agent) => {
      setPanel({ kind: 'none' });
      // `clearAgentToken`, never `sessionStore.agentDisconnect()` — that one
      // drops *every* agent's tokens, which would sign the browser out of the
      // agents it still owns.
      clearAgentToken(agent.agentId);
      disconnectAgentEvents(agent.agentId);
      useLastSessionStore.getState().clearLastUsedSession(agent.agentId);
      // Otherwise the bucket outlives the machine it names, and re-pairing an
      // agent that happens to reuse the id resurrects directories from it.
      clearRecentPaths(agent.agentId);
      const selected = useSelectedAgentStore.getState();
      if (selected.selectedAgentId === agent.agentId) {
        selected.setSelectedAgent(null);
      }
      await invalidateAgents();
    },
    onError: () => {
      setPanel({ kind: 'none' });
      useToastStore.getState().show('Could not remove the agent', 'error');
    },
  });

  const busy = create.isPending || reregister.isPending || remove.isPending;
  const isEmpty = agents.data?.length === 0;

  return (
    <section className="page">
      <header className="page-header">
        <h1>Agents</h1>
        <Link to="/">Back to sessions</Link>
      </header>

      {!isEmpty && (
        <p className="page-note">
          An agent is the Mobaide process running on a machine you want to work on.
          Pair one to run sessions on it from this browser.
        </p>
      )}

      {agents.isPending && <p className="page-note state-loading">Loading agents…</p>}
      {agents.isError && (
        <ErrorState
          message="Failed to load agents."
          onRetry={() => void agents.refetch()}
        />
      )}

      {isEmpty && (
        <AgentsEmptyState
          onAddManually={() => {
            setPanel({ kind: 'add' });
          }}
        />
      )}

      <ul className="device-list">
        {agents.data?.map((agent) => (
          <AgentCard
            key={agent.agentId}
            agent={agent}
            busy={busy}
            onReregister={(target) => {
              setPanel({ kind: 'confirmReregister', agent: target });
            }}
            onDelete={(target) => {
              setPanel({ kind: 'confirmDelete', agent: target });
            }}
          />
        ))}
      </ul>

      {/* Suppressed while the guide is up: every one of these actions appears
          there in an order, and a flat row beside it would undo the ordering
          that is the whole point of the empty state. */}
      {!isEmpty && (
        <div className="button-row">
          <Link className="button button-primary" to="/agents/pair">
            Add an agent
          </Link>
          <Link className="button" to="/pair">
            Pair from a code
          </Link>
          <button
            type="button"
            className="button"
            onClick={() => {
              setPanel({ kind: 'install' });
            }}
          >
            Show install commands
          </button>
          <button
            type="button"
            className="button"
            onClick={() => {
              setPanel({ kind: 'add' });
            }}
          >
            Add manually
          </button>
        </div>
      )}

      {panel.kind === 'add' && (
        <AddAgentDialog
          busy={create.isPending}
          onCreate={(name) => {
            create.mutate(name);
          }}
          onCancel={() => {
            setPanel({ kind: 'none' });
          }}
        />
      )}

      {panel.kind === 'install' && (
        <InstallCommandsPanel
          onClose={() => {
            setPanel({ kind: 'none' });
          }}
        />
      )}

      {panel.kind === 'credentials' && (
        <AgentCredentialsPanel
          title={panel.title}
          credentials={panel.credentials}
          onDone={() => {
            setPanel({ kind: 'none' });
          }}
        />
      )}

      {panel.kind === 'confirmReregister' && (
        <Modal
          labelledBy="reregister-title"
          dismissible={!reregister.isPending}
          onClose={() => {
            setPanel({ kind: 'none' });
          }}
        >
          <h2 id="reregister-title">Re-register agent?</h2>
          <p>
            This generates new credentials for &ldquo;{panel.agent.name}&rdquo; and
            invalidates the existing token immediately.
          </p>
          <div className="button-row">
            <button
              type="button"
              className="button button-primary"
              disabled={reregister.isPending}
              onClick={() => {
                reregister.mutate(panel.agent);
              }}
            >
              Re-register
            </button>
            <button
              type="button"
              className="button"
              disabled={reregister.isPending}
              onClick={() => {
                setPanel({ kind: 'none' });
              }}
            >
              Cancel
            </button>
          </div>
        </Modal>
      )}

      {panel.kind === 'confirmDelete' && (
        <Modal
          role="alertdialog"
          labelledBy="remove-agent-title"
          dismissible={!remove.isPending}
          initialFocus={removeCancelRef}
          onClose={() => {
            setPanel({ kind: 'none' });
          }}
        >
          <h2 id="remove-agent-title">Remove agent?</h2>
          <p>
            Permanently removes &ldquo;{panel.agent.name}&rdquo; and all of its data.
            The host keeps running until you stop it, and can be paired again from
            scratch.
          </p>
          <div className="button-row">
            <button
              type="button"
              className="button button-danger"
              disabled={remove.isPending}
              onClick={() => {
                remove.mutate(panel.agent);
              }}
            >
              Remove
            </button>
            <button
              type="button"
              ref={removeCancelRef}
              className="button"
              disabled={remove.isPending}
              onClick={() => {
                setPanel({ kind: 'none' });
              }}
            >
              Cancel
            </button>
          </div>
        </Modal>
      )}
    </section>
  );
}
