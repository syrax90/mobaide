import { useState, type ReactNode, type RefObject } from 'react';
import { useNavigate } from 'react-router';
import type { MenuGroup } from '../../../components/MenuButton';
import { NO_GIT_CAPABILITIES, hasGitSync } from '../../../protocol/dto/agentVersion';
import type { SessionResponse } from '../../../protocol/dto/session';
import { GitChangesPanel } from './GitChangesPanel';
import { useSessionGitCapabilities } from './useSessionGit';

interface SessionGitMenuOptions {
  agentId: string;
  sessionId: string;
  /** The session's own row, once the list has synced; `cwd` gates the menu. */
  session: SessionResponse | undefined;
  /** The menu trigger the Changes viewer returns focus to. */
  moreRef: RefObject<HTMLButtonElement | null>;
}

interface SessionGitMenu {
  /** The header menu's *Git* group, or `null` while the row has no cwd. */
  group: MenuGroup | null;
  /** The Changes viewer, while open. */
  panel: ReactNode;
}

/**
 * The session screen's *Git* menu and the Changes viewer it opens.
 *
 * Only once the row carries a cwd, as on both mobile clients: the git routes
 * answer for the directory the session was spawned in, and a row without one
 * has nothing to show.
 */
export function useSessionGitMenu({
  agentId,
  sessionId,
  session,
  moreRef,
}: SessionGitMenuOptions): SessionGitMenu {
  const navigate = useNavigate();
  // Separate from the screen's dialogs: the Changes viewer is non-modal and
  // may stay open under them, and it returns focus to the menu that opened it
  // rather than to the screen's input.
  const [changesOpen, setChangesOpen] = useState(false);
  // A counter, not a flag: the panel may already be open when *Commit…* is
  // picked again, and only a new value re-opens its dialog.
  const [commitRequest, setCommitRequest] = useState(0);
  const [syncRequest, setSyncRequest] = useState(0);
  const [rebaseRequest, setRebaseRequest] = useState(0);
  // The menu's *Commit…* entry exists only on an agent that advertises
  // `capabilities.git.commit`, *Fetch / Pull / Push…* on any of the three
  // job keys; the panel gates its own controls the same way.
  const gitCapabilities =
    useSessionGitCapabilities(agentId, session?.cwd !== undefined).data ?? NO_GIT_CAPABILITIES;
  const canCommit = gitCapabilities.commit;
  const canSync = hasGitSync(gitCapabilities);
  // *Branches…* is a link to the project's Git screen, so it needs a
  // project-backed session on top of the capability.
  const branchesProject =
    gitCapabilities.branches && session?.project_name !== undefined ? session.project_name : null;

  const group: MenuGroup | null =
    session?.cwd === undefined
      ? null
      : {
          id: 'git',
          label: 'Git',
          items: [
            {
              id: 'changes',
              label: 'Changes…',
              onSelect: () => {
                setChangesOpen(true);
              },
            },
            ...(canCommit
              ? [
                  {
                    id: 'commit',
                    label: 'Commit…',
                    onSelect: () => {
                      setChangesOpen(true);
                      setCommitRequest((n) => n + 1);
                    },
                  },
                ]
              : []),
            ...(canSync
              ? [
                  {
                    id: 'sync',
                    label: 'Fetch / Pull / Push…',
                    onSelect: () => {
                      setChangesOpen(true);
                      setSyncRequest((n) => n + 1);
                    },
                  },
                ]
              : []),
            ...(branchesProject !== null
              ? [
                  {
                    id: 'branches',
                    label: 'Branches…',
                    onSelect: () => {
                      void navigate(
                        `/agents/${encodeURIComponent(agentId)}/projects/${encodeURIComponent(branchesProject)}/git`,
                      );
                    },
                  },
                ]
              : []),
            ...(gitCapabilities.rebase
              ? [
                  {
                    id: 'rebase',
                    label: 'Rebase…',
                    onSelect: () => {
                      setChangesOpen(true);
                      setRebaseRequest((n) => n + 1);
                    },
                  },
                ]
              : []),
          ],
        };

  const panel = changesOpen ? (
    <GitChangesPanel
      agentId={agentId}
      sessionId={sessionId}
      commitRequest={commitRequest}
      syncRequest={syncRequest}
      rebaseRequest={rebaseRequest}
      projectName={session?.project_name ?? null}
      onClose={() => {
        setChangesOpen(false);
        setCommitRequest(0);
        setSyncRequest(0);
        setRebaseRequest(0);
        // Back to the menu button: the viewer was reached from the header,
        // and a keyboard user is still up there.
        moreRef.current?.focus();
      }}
    />
  ) : null;

  return { group, panel };
}
