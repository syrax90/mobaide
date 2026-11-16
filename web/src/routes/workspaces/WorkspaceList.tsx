import { useCallback, useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router';
import {
  deleteWorkspace,
  listWorkspaces,
  reorderWorkspaces,
  type WorkspaceInfo,
} from '../../api/workspaceRepository';
import { conflictDetailString, isConflict } from '../../api/conflicts';
import { isApiError } from '../../api/errors';
import { queryKeys, wsBackedQueryOptions } from '../../lib/queryKeys';
import { useDeferredCommit } from '../../lib/useDeferredCommit';
import { forceReconnectAgentEvents, useAgentEvents } from '../../realtime';
import { useToastStore } from '../../stores/toastStore';
import { UndoBar } from '../../components/UndoBar';
import { WorkspaceFormDialog } from './WorkspaceFormDialog';
import { WorkspaceRow } from './WorkspaceRow';
import { moveAnnouncement, moveItem, reconcileOrder } from './reorder';

interface WorkspaceListProps {
  agentId: string;
  online: boolean;
}

type Editor = { kind: 'none' } | { kind: 'create' } | { kind: 'edit'; name: string };

export function WorkspaceList({ agentId, online }: WorkspaceListProps) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [editor, setEditor] = useState<Editor>({ kind: 'none' });
  const [pendingOrder, setPendingOrder] = useState<string[] | null>(null);
  const [announcement, setAnnouncement] = useState('');
  const [dragIndex, setDragIndex] = useState<number | null>(null);

  useAgentEvents(agentId);

  const workspaces = useQuery({
    queryKey: queryKeys.workspaces(agentId),
    queryFn: () => listWorkspaces(agentId),
    // An offline agent 503s through the relay on every proxied call.
    enabled: online,
    ...wsBackedQueryOptions,
  });

  const writeWorkspaces = useCallback(
    (next: WorkspaceInfo[]) => {
      queryClient.setQueryData<WorkspaceInfo[]>(queryKeys.workspaces(agentId), next);
    },
    [agentId, queryClient],
  );

  const remove = useMutation({
    mutationFn: (workspace: WorkspaceInfo) => deleteWorkspace(agentId, workspace.name),
    onSuccess: async () => {
      // The agent republishes the whole list, which is the honest source; this
      // only covers the socket being down.
      await queryClient.invalidateQueries({ queryKey: queryKeys.workspaces(agentId) });
    },
    onError: (error: unknown) => {
      // A 409 is the auto-scan guard. The row is already back — the hide-set
      // drops with the window — so all that is left is to say why.
      useToastStore
        .getState()
        .show(
          isConflict(error)
            ? conflictDetailString(error)
            : isApiError(error)
              ? `Delete failed: ${error.message}`
              : 'Delete failed',
          'error',
        );
      void queryClient.invalidateQueries({ queryKey: queryKeys.workspaces(agentId) });
    },
  });

  const deferredDelete = useDeferredCommit<WorkspaceInfo>({
    onCommit: (workspace) => {
      remove.mutate(workspace);
    },
  });

  const reorder = useMutation({
    mutationFn: (names: string[]) => reorderWorkspaces(agentId, names),
    // The 200 body is already the re-sorted list, so write it rather than
    // waiting for the `projects.changed` echo that follows it — and release the
    // override here rather than leaving it to the echo. A create or delete
    // landing inside the round trip makes the echo's order differ from the
    // override forever, which would strand it and make every later reorder
    // (including one performed from a phone) invisible for the life of the mount.
    onSuccess: (next) => {
      writeWorkspaces(next);
      setPendingOrder(null);
    },
    onError: () => {
      setPendingOrder(null);
      useToastStore.getState().show('Reorder failed — reverted.', 'error');
    },
  });

  const view = reconcileOrder(workspaces.data ?? [], pendingOrder);

  // Release the optimistic override once the server's own order matches it.
  // Doing this in an effect rather than during render keeps the reconciliation
  // pure and avoids a set-state-while-rendering warning.
  useEffect(() => {
    if (pendingOrder !== null && view.pendingOrder === null) setPendingOrder(null);
  }, [pendingOrder, view.pendingOrder]);

  // Hidden while the window is open **and** while the DELETE it committed is in
  // flight: `useDeferredCommit` clears its payload before invoking `onCommit`,
  // so without the second term the row paints again for the length of the
  // request — long enough for the user to press Delete a second time and get a
  // spurious "not found".
  const pendingDeleteName =
    deferredDelete.pending?.name ?? (remove.isPending ? (remove.variables?.name ?? null) : null);
  const visible = view.workspaces.filter((workspace) => workspace.name !== pendingDeleteName);

  // Close the editor if its target leaves the list. `editing` is resolved by
  // name, so a workspace deleted or renamed by another client would otherwise
  // turn the open dialog into a *create* form — same title, same dirty fields,
  // but Save now calls `createWorkspace`, Linear key included.
  useEffect(() => {
    if (editor.kind !== 'edit' || workspaces.data === undefined) return;
    if (!workspaces.data.some((workspace) => workspace.name === editor.name)) {
      setEditor({ kind: 'none' });
      useToastStore.getState().show('That workspace no longer exists.', 'info');
    }
  }, [editor, workspaces.data]);

  const move = (from: number, to: number) => {
    if (to < 0 || to >= visible.length) return;
    const moved = visible[from];
    const anchor = visible[to];
    if (moved === undefined || anchor === undefined) return;

    // The request carries the **whole** name set — the server 400s on any
    // missing name — so the move is applied to the full list and the visible
    // neighbour is only used to find the destination. A row hidden by an open
    // undo window must still appear in the payload.
    const names = view.workspaces.map((workspace) => workspace.name);
    const next = moveItem(names, names.indexOf(moved.name), names.indexOf(anchor.name));
    setPendingOrder(next);
    setAnnouncement(moveAnnouncement(moved.name, to, visible.length));
    reorder.mutate(next);
  };

  const editing =
    editor.kind === 'edit'
      ? (workspaces.data?.find((workspace) => workspace.name === editor.name) ?? null)
      : null;

  return (
    <>
      <div className="sessions-toolbar">
        <button
          type="button"
          className="button button-primary"
          onClick={() => {
            setEditor({ kind: 'create' });
          }}
        >
          New workspace
        </button>
        <button
          type="button"
          className="button"
          onClick={() => {
            forceReconnectAgentEvents(agentId);
            // `refetch()` ignores `enabled`, so refetching while offline would
            // fire exactly the 503 the gate exists to prevent.
            if (online) void workspaces.refetch();
          }}
        >
          Refresh
        </button>
      </div>

      {/* Offline is checked before `isPending`: a disabled query with a cold
          cache reports pending forever, which would be a permanent spinner. */}
      {!online ? (
        <p className="empty-state">This agent is offline.</p>
      ) : workspaces.isPending ? (
        <p className="page-note">Loading workspaces…</p>
      ) : workspaces.isError ? (
        <p className="auth-error">Failed to load workspaces.</p>
      ) : visible.length === 0 ? (
        <p className="empty-state">No workspaces yet. Create one to get started.</p>
      ) : (
        <ul className="workspace-card-list">
          {visible.map((workspace, index) => (
            <WorkspaceRow
              key={workspace.name}
              workspace={workspace}
              index={index}
              total={visible.length}
              busy={reorder.isPending}
              onEdit={(target) => {
                setEditor({ kind: 'edit', name: target.name });
              }}
              onDelete={(target) => {
                deferredDelete.schedule(target);
              }}
              onOpenGit={(target) => {
                void navigate(
                  `/agents/${encodeURIComponent(agentId)}/workspaces/${encodeURIComponent(
                    target.name,
                  )}/git`,
                );
              }}
              onMove={move}
              onDragStart={setDragIndex}
              onDropOn={(target) => {
                if (dragIndex !== null && dragIndex !== target) move(dragIndex, target);
                setDragIndex(null);
              }}
            />
          ))}
        </ul>
      )}

      {/* Announcements for the move buttons. Polite, and outside the list so a
          re-render of the rows cannot drop the region and lose the message. */}
      <p className="visually-hidden" role="status" aria-live="polite">
        {announcement}
      </p>

      {deferredDelete.pending !== null && (
        <UndoBar
          message={`Deleted ‘${deferredDelete.pending.name}’`}
          onUndo={deferredDelete.cancel}
          onDismiss={deferredDelete.commitNow}
        />
      )}

      {editor.kind !== 'none' && (
        <WorkspaceFormDialog
          agentId={agentId}
          editing={editing}
          onClose={() => {
            setEditor({ kind: 'none' });
          }}
        />
      )}
    </>
  );
}
