import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Link, useNavigate, useParams } from 'react-router';
import { listAgents } from '../../api/agentRepository';
import { isApiError } from '../../api/errors';
import {
  closeHerdrItem,
  createHerdrTerminal,
  renameHerdrItem,
  startHerdrAgent,
} from '../../api/herdrRepository';
import { ErrorState } from '../../components/ErrorState';
import { MenuButton, type MenuItem } from '../../components/MenuButton';
import {
  herdrAgentLabel,
  herdrAgentRows,
  herdrPaneRows,
  herdrPaneTitle,
  herdrServerLabel,
  herdrSummary,
  paneSeenKey,
  type HerdrPaneRow,
} from '../../herdr/herdrDisplay';
import { useHerdrSeenStore } from '../../herdr/herdrSeenStore';
import { useHerdr } from '../../herdr/useHerdr';
import { queryKeys, wsBackedQueryOptions } from '../../lib/queryKeys';
import {
  HERDR_SERVER_READY,
  type HerdrPane,
  type HerdrServer,
  type HerdrTab,
  type HerdrWorkspace,
} from '../../protocol/dto/herdr';
import { useAgentEvents } from '../../realtime';
import { useToastStore } from '../../stores/toastStore';
import {
  HerdrCloseDialog,
  HerdrCreateDialog,
  HerdrRenameDialog,
  HerdrStartAgentDialog,
  type HerdrCreateValues,
  type HerdrTarget,
} from './HerdrDialogs';
import { HerdrStatusChip, HerdrStatusMark } from './HerdrStatus';
import { herdrPanePath } from './herdrPath';

/** `/agents/:agentId/herdr` — every Herdr server on one agent's computer. */
export function HerdrScreen() {
  const { agentId } = useParams();
  if (agentId === undefined) {
    return (
      <section className="page">
        <h1>Agent not specified</h1>
        <Link to="/">Back to sessions</Link>
      </section>
    );
  }
  return <HerdrDashboard key={agentId} agentId={agentId} />;
}

type Dialog =
  | { kind: 'none' }
  | { kind: 'create'; server: HerdrServer; placement: 'workspace' | 'tab'; workspaceId?: string }
  | { kind: 'rename'; server: HerdrServer; target: HerdrTarget; id: string; current: string }
  | { kind: 'close'; server: HerdrServer; target: HerdrTarget; id: string; label: string }
  | { kind: 'agent'; server: HerdrServer; paneId: string };

function HerdrDashboard({ agentId }: { agentId: string }) {
  const navigate = useNavigate();
  useAgentEvents(agentId);
  const agents = useQuery({
    queryKey: queryKeys.agents(),
    queryFn: listAgents,
    ...wsBackedQueryOptions,
  });
  const online = agents.data?.find((agent) => agent.agentId === agentId)?.online ?? false;
  const { supported, snapshot } = useHerdr(agentId, online);
  const seen = useHerdrSeenStore((state) => state.seen);
  const markSeen = useHerdrSeenStore((state) => state.markSeen);
  const [dialog, setDialogState] = useState<Dialog>({ kind: 'none' });
  // Why the action of the open dialog failed. Shown inside the dialog: a toast
  // would render under the dialog's top layer, unseen.
  const [dialogError, setDialogError] = useState<string | null>(null);

  const setDialog = (next: Dialog) => {
    setDialogError(null);
    setDialogState(next);
  };
  const closeDialog = () => {
    setDialog({ kind: 'none' });
  };
  const reason = (error: unknown) =>
    isApiError(error) ? error.message : 'Herdr did not answer. Try again.';
  // One mutation for every dialog action: they are all "run it, close the
  // dialog, show herdr's own words on a refusal". The snapshot arrives by event.
  const action = useMutation({
    mutationFn: (run: () => Promise<unknown>) => {
      setDialogError(null);
      return run();
    },
    onSuccess: closeDialog,
    onError: (error) => {
      setDialogError(reason(error));
    },
  });
  // A split has no dialog, so its refusal is a toast.
  const splitAction = useMutation({
    mutationFn: (run: () => Promise<unknown>) => run(),
    onError: (error) => {
      useToastStore.getState().show(reason(error), 'error');
    },
  });

  const servers = snapshot.data?.servers ?? [];
  const rows = herdrPaneRows(agentId, snapshot.data, seen);
  const agentRows = herdrAgentRows(rows);

  const createValues = (server: HerdrServer, values: HerdrCreateValues) => {
    if (dialog.kind !== 'create') return;
    const { placement, workspaceId } = dialog;
    action.mutate(async () => {
      const created = await createHerdrTerminal(agentId, {
        server_id: server.server_id,
        placement,
        workspace_id: workspaceId ?? null,
        cwd: values.cwd,
        label: values.label,
        agent: values.agent,
      });
      // The terminal exists though its agent did not start: say so, and open it.
      if (created.agentError !== null) useToastStore.getState().show(created.agentError, 'error');
      // Straight into the new pane: that is why it was created.
      void navigate(herdrPanePath(agentId, server.server_id, created.paneId));
    });
  };

  const split = (server: HerdrServer, pane: HerdrPane, placement: 'split_right' | 'split_down') => {
    if (splitAction.isPending) return;
    splitAction.mutate(async () => {
      const created = await createHerdrTerminal(agentId, {
        server_id: server.server_id,
        placement,
        pane_id: pane.pane_id,
      });
      void navigate(herdrPanePath(agentId, server.server_id, created.paneId));
    });
  };

  const paneMenu = (server: HerdrServer, row: HerdrPaneRow): MenuItem[] => {
    const { features } = server;
    const { pane } = row;
    const items: MenuItem[] = [];
    const { turn } = pane;
    if (row.status === 'done' && turn !== undefined) {
      items.push({
        id: 'seen',
        label: 'Mark as seen',
        onSelect: () => {
          markSeen(paneSeenKey(agentId, server.server_id, pane.pane_id), turn);
        },
      });
    }
    if (features.split) {
      items.push(
        {
          id: 'split-right',
          label: 'Split right',
          onSelect: () => {
            split(server, pane, 'split_right');
          },
        },
        {
          id: 'split-down',
          label: 'Split down',
          onSelect: () => {
            split(server, pane, 'split_down');
          },
        },
      );
    }
    if (pane.agent === undefined && features.agents.length > 0) {
      items.push({
        id: 'agent',
        label: 'Start an agent…',
        onSelect: () => {
          setDialog({ kind: 'agent', server, paneId: pane.pane_id });
        },
      });
    }
    if (features.rename) {
      items.push({
        id: 'rename',
        label: 'Rename…',
        onSelect: () => {
          setDialog({
            kind: 'rename',
            server,
            target: 'pane',
            id: pane.pane_id,
            current: herdrPaneTitle(pane),
          });
        },
      });
    }
    if (features.close_pane) {
      items.push({
        id: 'close',
        label: 'Close pane…',
        onSelect: () => {
          setDialog({
            kind: 'close',
            server,
            target: 'pane',
            id: pane.pane_id,
            label: herdrPaneTitle(pane),
          });
        },
      });
    }
    return items;
  };

  const groupMenu = (
    server: HerdrServer,
    target: 'workspace' | 'tab',
    id: string,
    label: string,
  ): MenuItem[] => {
    const { features } = server;
    const items: MenuItem[] = [];
    if (target === 'workspace' && features.new_tab) {
      items.push({
        id: 'new-tab',
        label: 'New tab…',
        onSelect: () => {
          setDialog({ kind: 'create', server, placement: 'tab', workspaceId: id });
        },
      });
    }
    if (features.rename) {
      items.push({
        id: 'rename',
        label: 'Rename…',
        onSelect: () => {
          setDialog({ kind: 'rename', server, target, id, current: label });
        },
      });
    }
    if (features.close_group) {
      items.push({
        id: 'close',
        label: `Close ${target}…`,
        onSelect: () => {
          setDialog({ kind: 'close', server, target, id, label });
        },
      });
    }
    return items;
  };

  const paneRow = (server: HerdrServer, row: HerdrPaneRow, withPath: boolean) => {
    const { pane, status } = row;
    const menu = paneMenu(server, row);
    return (
      <li key={`${server.server_id}:${pane.pane_id}`} className="device-card session-card">
        <Link
          className="session-card-link"
          to={herdrPanePath(agentId, server.server_id, pane.pane_id)}
        >
          <HerdrStatusMark status={status} />
          <span className="session-text">
            <span className="session-title">{herdrPaneTitle(pane)}</span>
            <span className="session-kind">
              {pane.agent !== undefined ? herdrAgentLabel(pane.agent) : 'Terminal'}
              {withPath && ` · ${row.workspace.label}`}
            </span>
            {pane.cwd !== undefined && <span className="session-cwd">{pane.cwd}</span>}
          </span>
        </Link>
        <HerdrStatusChip status={status} />
        {menu.length > 0 && (
          <MenuButton
            label={`Actions for ${herdrPaneTitle(pane)}`}
            className="button button-icon"
            groups={[{ id: 'pane', label: 'Pane', items: menu }]}
          >
            ⋯
          </MenuButton>
        )}
      </li>
    );
  };

  const groupHeader = (
    server: HerdrServer,
    target: 'workspace' | 'tab',
    id: string,
    label: string,
  ) => {
    const menu = groupMenu(server, target, id, label);
    const Heading = target === 'workspace' ? 'h3' : 'h4';
    return (
      <div className={`herdr-group-head herdr-group-head-${target}`}>
        <Heading className="herdr-group-title">{label}</Heading>
        {menu.length > 0 && (
          <MenuButton
            label={`Actions for ${target} ${label}`}
            className="button button-icon"
            groups={[{ id: target, label: target === 'workspace' ? 'Workspace' : 'Tab', items: menu }]}
          >
            ⋯
          </MenuButton>
        )}
      </div>
    );
  };

  const tabBlock = (server: HerdrServer, workspace: HerdrWorkspace, tab: HerdrTab) => {
    const tabRows = rows.filter(
      (row) => row.server === server && row.workspace === workspace && row.tab === tab,
    );
    // A workspace with one tab reads as a flat list: the tab level adds nothing.
    const showTab = workspace.tabs.length > 1;
    return (
      <div key={tab.tab_id} className="herdr-tab">
        {showTab && groupHeader(server, 'tab', tab.tab_id, tab.label)}
        <ul className="device-list">{tabRows.map((row) => paneRow(server, row, false))}</ul>
      </div>
    );
  };

  const serverBlock = (server: HerdrServer) => {
    const ready = server.state === HERDR_SERVER_READY;
    return (
      <section key={server.server_id} className="herdr-server">
        <div className="herdr-server-head">
          <h2 className="autoscan-section-title">
            {herdrServerLabel(server, servers)}
            {server.version !== undefined && (
              <span className="herdr-server-version"> {server.version}</span>
            )}
          </h2>
          {ready && server.features.new_workspace && (
            <button
              type="button"
              className="button"
              onClick={() => {
                setDialog({ kind: 'create', server, placement: 'workspace' });
              }}
            >
              {server.product === 'core' ? 'New terminal' : 'New workspace'}
            </button>
          )}
        </div>
        {!ready ? (
          <p className="empty-state">{server.message ?? 'This Herdr server is not ready.'}</p>
        ) : server.workspaces.length === 0 ? (
          <p className="empty-state">Nothing is open in this Herdr server.</p>
        ) : (
          server.workspaces.map((workspace) => (
            <div key={workspace.workspace_id} className="herdr-workspace">
              {groupHeader(server, 'workspace', workspace.workspace_id, workspace.label)}
              {workspace.tabs.map((tab) => tabBlock(server, workspace, tab))}
            </div>
          ))
        )}
      </section>
    );
  };

  return (
    <section className="page herdr-page">
      <header className="page-header">
        <div>
          <h1>Herdr</h1>
          <p className="page-note">
            <Link to="/">Sessions</Link>
            {servers.length > 0 && ` · ${herdrSummary(rows)}`}
          </p>
        </div>
      </header>

      {agents.isPending ? (
        <p className="page-note state-loading">Loading…</p>
      ) : !online ? (
        <p className="empty-state">This agent is offline.</p>
      ) : !supported ? (
        <p className="empty-state">Update Mobaide on your PC to use Herdr from here.</p>
      ) : snapshot.isPending ? (
        <p className="page-note state-loading">Looking for Herdr…</p>
      ) : snapshot.isError ? (
        <ErrorState message="Failed to load Herdr." onRetry={() => void snapshot.refetch()} />
      ) : servers.length === 0 ? (
        <p className="empty-state">
          No Herdr server runs on this computer. Start Herdr there, and it shows up here.
        </p>
      ) : (
        <>
          {agentRows.length > 0 && (
            <section className="herdr-server" aria-labelledby="herdr-agents-heading">
              <h2 id="herdr-agents-heading" className="autoscan-section-title">
                Agents
              </h2>
              <ul className="device-list">
                {agentRows.map((row) => paneRow(row.server, row, true))}
              </ul>
            </section>
          )}
          {servers.map(serverBlock)}
        </>
      )}

      {dialog.kind === 'create' && (
        <HerdrCreateDialog
          agentId={agentId}
          placement={dialog.placement}
          noun={dialog.server.product === 'core' ? 'terminal' : 'workspace'}
          agents={dialog.server.features.agents}
          busy={action.isPending}
          error={dialogError}
          onCreate={(values) => {
            createValues(dialog.server, values);
          }}
          onCancel={closeDialog}
        />
      )}
      {dialog.kind === 'rename' && (
        <HerdrRenameDialog
          target={dialog.target}
          current={dialog.current}
          busy={action.isPending}
          error={dialogError}
          onRename={(label) => {
            action.mutate(() =>
              renameHerdrItem(agentId, {
                server_id: dialog.server.server_id,
                target: dialog.target,
                id: dialog.id,
                label,
              }),
            );
          }}
          onCancel={closeDialog}
        />
      )}
      {dialog.kind === 'close' && (
        <HerdrCloseDialog
          target={dialog.target}
          label={dialog.label}
          busy={action.isPending}
          error={dialogError}
          onClose={() => {
            action.mutate(() =>
              closeHerdrItem(agentId, {
                server_id: dialog.server.server_id,
                target: dialog.target,
                id: dialog.id,
              }),
            );
          }}
          onCancel={closeDialog}
        />
      )}
      {dialog.kind === 'agent' && (
        <HerdrStartAgentDialog
          agents={dialog.server.features.agents}
          busy={action.isPending}
          error={dialogError}
          onStart={(agent) => {
            action.mutate(() =>
              startHerdrAgent(
                agentId,
                { serverId: dialog.server.server_id, paneId: dialog.paneId },
                agent,
              ),
            );
          }}
          onCancel={closeDialog}
        />
      )}
    </section>
  );
}
