import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Link, useParams, useSearchParams } from 'react-router';
import { listAgents } from '../../api/agentRepository';
import { isApiError } from '../../api/errors';
import {
  HERDR_GONE_CODES,
  herdrErrorCode,
  openHerdrPane,
  setHerdrControl,
  type HerdrPaneRef,
} from '../../api/herdrRepository';
import { findHerdrPane, herdrPaneTitle, paneSeenKey } from '../../herdr/herdrDisplay';
import { useHerdrSeenStore } from '../../herdr/herdrSeenStore';
import { useHerdr } from '../../herdr/useHerdr';
import { queryKeys, wsBackedQueryOptions } from '../../lib/queryKeys';
import { closeAttentionNotification } from '../../notifications/notificationService';
import { isPaneControlledElsewhere, isPaneController } from '../../protocol/dto/terminalState';
import { sessionChannelName } from '../../realtime/channels';
import { useAgentEvents } from '../../realtime';
import { useToastStore } from '../../stores/toastStore';
import { useVisibleSessionStore } from '../../stores/visibleSessionStore';
import type { TerminalSessionModel } from '../../terminal/sessionModel';
import {
  acquireTerminal,
  clearMountedTerminal,
  setMountedTerminal,
  useTerminalStore,
} from '../../terminal/terminalStore';
import { TerminalView } from '../terminal/TerminalScreen';
import { HerdrHistoryDialog } from './HerdrDialogs';
import { herdrPaneTag, herdrPath } from './herdrPath';

/** `/agents/:agentId/herdr/pane?server=…&pane=…` — one Herdr pane, live. */
export function HerdrPaneScreen() {
  const { agentId } = useParams();
  const [search] = useSearchParams();
  const serverId = search.get('server');
  const paneId = search.get('pane');
  if (agentId === undefined || serverId === null || paneId === null) {
    return (
      <section className="page">
        <h1>Pane not specified</h1>
        <Link to="/">Back to sessions</Link>
      </section>
    );
  }
  return (
    <HerdrPane
      key={herdrPaneTag(agentId, serverId, paneId)}
      agentId={agentId}
      serverId={serverId}
      paneId={paneId}
    />
  );
}

interface HerdrPaneProps {
  agentId: string;
  serverId: string;
  paneId: string;
}

function PaneMessage({
  agentId,
  loading = false,
  children,
}: {
  agentId: string;
  /** Still in progress: the text gets the loading cursor, unlike a final message. */
  loading?: boolean;
  children: string;
}) {
  return (
    <section className="terminal-page terminal-page-empty">
      <p role="status" className={loading ? 'state-loading' : undefined}>
        {children}
      </p>
      <Link to={herdrPath(agentId)}>Back to Herdr</Link>
    </section>
  );
}

function HerdrPane({ agentId, serverId, paneId }: HerdrPaneProps) {
  useAgentEvents(agentId);
  const agents = useQuery({
    queryKey: queryKeys.agents(),
    queryFn: listAgents,
    ...wsBackedQueryOptions,
  });
  const online = agents.data?.find((agent) => agent.agentId === agentId)?.online ?? false;
  const { snapshot } = useHerdr(agentId, online);
  const found = findHerdrPane(snapshot.data, serverId, paneId);
  const pane: HerdrPaneRef = { serverId, paneId };

  const open = useQuery({
    queryKey: queryKeys.herdrPaneOpen(agentId, serverId, paneId),
    queryFn: () => openHerdrPane(agentId, { serverId, paneId }),
    enabled: online,
    // The answer is a live view on the agent, not data: never reused across
    // visits, never refetched behind the terminal's back.
    gcTime: 0,
    staleTime: Infinity,
    retry: false,
    meta: { silent: true },
  });

  // An alert for the pane on screen is noise, and what it announced is seen.
  const tag = herdrPaneTag(agentId, serverId, paneId);
  useEffect(() => {
    const { setVisibleSession, clearVisibleSession } = useVisibleSessionStore.getState();
    setVisibleSession(tag);
    closeAttentionNotification(tag);
    return () => {
      clearVisibleSession(tag);
    };
  }, [tag]);

  const markSeen = useHerdrSeenStore((state) => state.markSeen);
  const status = found?.pane.status;
  const turn = found?.pane.turn;
  useEffect(() => {
    if (status === 'done' && turn !== undefined) {
      markSeen(paneSeenKey(agentId, serverId, paneId), turn);
    }
  }, [status, turn, agentId, serverId, paneId, markSeen]);

  if (agents.isPending) {
    return (
      <PaneMessage agentId={agentId} loading>
        Connecting to your computer
      </PaneMessage>
    );
  }
  if (!online) return <PaneMessage agentId={agentId}>This agent is offline.</PaneMessage>;
  if (open.isError) {
    const code = herdrErrorCode(open.error);
    const message =
      code !== null && HERDR_GONE_CODES.includes(code)
        ? 'This pane is closed.'
        : isApiError(open.error)
          ? open.error.message
          : 'Could not open the pane.';
    return <PaneMessage agentId={agentId}>{message}</PaneMessage>;
  }
  if (open.data === undefined) {
    return (
      <PaneMessage agentId={agentId} loading>
        Opening the pane
      </PaneMessage>
    );
  }

  return (
    <HerdrPaneTerminal
      key={open.data}
      agentId={agentId}
      sessionId={open.data}
      pane={pane}
      title={found === null ? 'Herdr pane' : herdrPaneTitle(found.pane)}
      canControl={found?.server.features.control ?? false}
      typeInWatch={found?.server.features.type_in_watch ?? true}
      reopen={async () => (await open.refetch()).isSuccess}
    />
  );
}

interface HerdrPaneTerminalProps {
  agentId: string;
  sessionId: string;
  pane: HerdrPaneRef;
  title: string;
  canControl: boolean;
  typeInWatch: boolean;
  /** Ask the agent for the pane view again; resolves to whether the pane still exists. */
  reopen: () => Promise<boolean>;
}

function HerdrPaneTerminal(props: HerdrPaneTerminalProps) {
  const { agentId, sessionId } = props;
  const key = sessionChannelName(agentId, sessionId);
  const model = useTerminalStore((state) => state.models[key]);

  useEffect(() => {
    acquireTerminal(agentId, sessionId, true);
  }, [agentId, sessionId, model]);

  useEffect(() => {
    setMountedTerminal(key);
    return () => {
      clearMountedTerminal(key);
      // Not kept like a session's terminal: an attached socket makes the agent
      // stream the pane, and holds control of it.
      useTerminalStore.getState().models[key]?.dispose();
    };
  }, [key]);

  if (model === undefined) {
    return <section className="terminal-page terminal-page-empty">Attaching…</section>;
  }
  return <HerdrPaneView {...props} model={model} />;
}

function HerdrPaneView({
  agentId,
  pane,
  title,
  canControl,
  typeInWatch,
  reopen,
  model,
}: HerdrPaneTerminalProps & { model: TerminalSessionModel }) {
  const status = model.state((state) => state.status);
  // Control belongs to one client. Another device's control leaves this one a
  // watcher: it draws that device's grid and can take control itself.
  const controlling = isPaneController(status);
  const controlledElsewhere = isPaneControlledElsewhere(status);
  const [historyOpen, setHistoryOpen] = useState(false);

  // The agent ends a pane view nobody watched for a while, and one whose herdr
  // server restarted. Ask for it again once; a pane that is really closed
  // answers `pane_not_found` and the parent shows that.
  const reopened = useRef(false);
  const ended = status?.status === 'stopped';
  useEffect(() => {
    if (!ended) {
      reopened.current = false;
      return;
    }
    if (reopened.current) return;
    reopened.current = true;
    void reopen().then((alive) => {
      if (alive) model.reattach();
    });
  }, [ended, reopen, model]);

  const control = useMutation({
    mutationFn: (next: boolean) => setHerdrControl(agentId, pane, next),
    onError: (error) => {
      useToastStore
        .getState()
        .show(isApiError(error) ? error.message : 'Herdr did not answer. Try again.', 'error');
    },
  });

  // Leaving gives the pane back at once, without waiting for the socket to close.
  const controllingRef = useRef(controlling);
  controllingRef.current = controlling;
  useEffect(
    () => () => {
      if (controllingRef.current) void setHerdrControl(agentId, pane, false).catch(() => undefined);
    },
    // `pane` is a fresh object on each render of the parent; its two ids are the identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [agentId, pane.serverId, pane.paneId],
  );

  const notice = controlling
    ? 'You control this pane. It has the size of this window, also on your computer.'
    : controlledElsewhere
      ? 'Another device controls this pane. It has the size of that device.'
      : typeInWatch
        ? null
        : 'Watching. Take control to type more than single keys.';

  return (
    <>
      <TerminalView
        model={model}
        headerLabel={title}
        session={undefined}
        pane={{
          backTo: herdrPath(agentId),
          backLabel: 'Herdr',
          notice,
          controls: (
            <>
              {canControl && (
                <button
                  type="button"
                  className="button"
                  disabled={control.isPending || ended}
                  title={
                    controlling
                      ? 'Give the pane back: it returns to its own size'
                      : 'Resize the pane to this window. The pane on your computer changes size too.'
                  }
                  onClick={() => {
                    control.mutate(!controlling);
                  }}
                >
                  {controlling ? 'Release control' : 'Take control'}
                </button>
              )}
              <button
                type="button"
                className="button"
                title="Read the pane's earlier output as text"
                onClick={() => {
                  setHistoryOpen(true);
                }}
              >
                History
              </button>
            </>
          ),
        }}
      />
      {historyOpen && (
        <HerdrHistoryDialog
          agentId={agentId}
          pane={pane}
          title={title}
          onClose={() => {
            setHistoryOpen(false);
          }}
        />
      )}
    </>
  );
}
