import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  GIT_CODE_FILE_CHANGED,
  GIT_CODE_NOT_CONFLICTED,
  isSessionGitError,
  isTerminalSessionGitError,
  type SessionGitConflict,
  type SessionGitConflictStyle,
} from '../../../api/sessionGitRepository';
import { MenuButton, type MenuGroup } from '../../../components/MenuButton';
import { Modal } from '../../../components/Modal';
import { writeClipboardText } from '../../../lib/clipboard';
import { queryKeys } from '../../../lib/queryKeys';
import { SessionGitState } from './SessionGitState';
import {
  KEEP_EDITS_WARNING,
  START_OVER_LABEL,
  START_OVER_WITH_BASE_LABEL,
  canStartOver,
  conflictCaption,
  conflictKindNotice,
  conflictLegend,
  conflictsLeftLabel,
  draftSavedNotice,
  exceedsByteLimit,
  fileChangedNotice,
  isEditableConflict,
  nextConflictOffset,
  resolvedElsewhereNotice,
  resolvedNotice,
  scanConflictText,
  startOverPrompt,
  takeChoices,
  tooLargeToSaveNotice,
  type ConflictScan,
  type TakeChoice,
} from './sessionGitConflictDisplay';
import { fileName, mutationErrorCopy, sessionGitErrorCopy } from './sessionGitDisplay';
import { useSessionGitConflict, useSessionGitConflictMutations } from './useSessionGit';

export interface ConflictPaneNotice {
  tone: 'ok' | 'danger';
  text: string;
}

interface GitConflictPaneProps {
  agentId: string;
  sessionId: string;
  path: string;
  /** The status still lists the path under Conflicts. */
  conflicted: boolean;
  /** Another mutation or a fetch / pull / push job holds the repository. */
  busy: boolean;
  /** Stacked layout only: back to the file list. The panel guards unsaved edits. */
  onBack: () => void;
  onViewDiff: () => void;
  onDirtyChange: (dirty: boolean) => void;
  /** The conflict is settled — applied or a side taken — and the pane should close. */
  onDone: (notice: ConflictPaneNotice) => void;
  /** Something worth a line in the panel's notice strip while the pane stays open. */
  onNotice: (notice: ConflictPaneNotice | null) => void;
  /** A nested dialog is open, so the panel must leave Escape to it. */
  onDialogChange: (open: boolean) => void;
}

const SCAN_DEBOUNCE_MS = 150;
const NO_SCAN: ConflictScan = {
  conflicts: 0,
  hasMarkers: false,
  hasBase: false,
};

/**
 * What the next load of the file does to the editor: fill it, only adopt the
 * file's new token (after a draft save, or "Keep my edits"), or nothing at
 * all — a load this pane did not ask for must never replace a draft.
 */
type Adopt = 'seed' | 'token' | 'token-warn' | 'ignore';

/**
 * The conflict resolver: the conflicted file, markers and all, in an editor,
 * with the two sides named above it and the whole-file picks behind a menu.
 *
 * The textarea is uncontrolled — a quarter-megabyte string must not round-trip
 * through React state per keystroke — and is remounted with the file's text
 * only on a load the user asked for. The pane renders from its own copy of
 * the answer, so a cache cleared underneath it (an agent reconnect) cannot
 * unmount the editor; the agent's `file_changed` refusal is where a concurrent
 * edit surfaces.
 */
export function GitConflictPane({
  agentId,
  sessionId,
  path,
  conflicted,
  busy,
  onBack,
  onViewDiff,
  onDirtyChange,
  onDone,
  onNotice,
  onDialogChange,
}: GitConflictPaneProps) {
  const queryClient = useQueryClient();
  const conflict = useSessionGitConflict(agentId, sessionId, path);
  const mutations = useSessionGitConflictMutations(agentId, sessionId);
  const editorRef = useRef<HTMLTextAreaElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const menuTriggerRef = useRef<HTMLButtonElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const adopt = useRef<Adopt>('seed');
  const baseText = useRef('');
  const scanTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const [loaded, setLoaded] = useState<SessionGitConflict | null>(null);
  const [seed, setSeed] = useState({ version: 0, text: '' });
  const [token, setToken] = useState<string | null>(null);
  const [scan, setScan] = useState<ConflictScan>(NO_SCAN);
  const [dirty, setDirty] = useState(false);
  const [tooLarge, setTooLarge] = useState(false);
  const [stale, setStale] = useState(false);
  const [overwriteWarning, setOverwriteWarning] = useState(false);
  const [gone, setGone] = useState(false);
  const [legendOpen, setLegendOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingTake, setPendingTake] = useState<TakeChoice | null>(null);
  const [startOverOpen, setStartOverOpen] = useState(false);

  const fetched = conflict.data;
  const fetchedAt = conflict.dataUpdatedAt;
  const editable = loaded !== null && isEditableConflict(loaded);
  // A reload in flight counts: until it lands the token is the old file's.
  const working = mutations.busy || busy || (loaded !== null && conflict.isFetching);
  const dialogOpen = pendingTake !== null || startOverOpen;

  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  useEffect(() => {
    onDirtyChange(dirty);
  }, [dirty, onDirtyChange]);

  useEffect(() => {
    onDialogChange(dialogOpen);
  }, [dialogOpen, onDialogChange]);

  useEffect(
    () => () => {
      clearTimeout(scanTimer.current);
    },
    [],
  );

  // Keyed on the fetch time, not the data: structural sharing hands back the
  // same object when a reload finds the file unchanged.
  useEffect(() => {
    if (fetched === undefined || fetchedAt === 0) return;
    const mode = adopt.current;
    if (mode === 'ignore') return;
    adopt.current = 'ignore';
    setLoaded(fetched);
    setStale(false);
    if (!isEditableConflict(fetched)) return;
    setToken(fetched.token ?? null);
    if (mode !== 'seed') {
      setOverwriteWarning(mode === 'token-warn');
      return;
    }
    const text = fetched.content ?? '';
    baseText.current = text;
    setSeed((previous) => ({ version: previous.version + 1, text }));
    setScan(scanConflictText(text));
    setTooLarge(false);
    setDirty(false);
    setOverwriteWarning(false);
  }, [fetched, fetchedAt]);

  const dropQuery = (): void => {
    queryClient.removeQueries({
      queryKey: queryKeys.sessionGitConflict(agentId, sessionId, path),
    });
  };

  const byteLimit = loaded?.byte_limit ?? Number.MAX_SAFE_INTEGER;

  const onInput = (): void => {
    const value = editorRef.current?.value ?? '';
    setDirty(value !== baseText.current);
    clearTimeout(scanTimer.current);
    scanTimer.current = setTimeout(() => {
      const text = editorRef.current?.value ?? '';
      setScan(scanConflictText(text));
      setTooLarge(exceedsByteLimit(text, byteLimit));
    }, SCAN_DEBOUNCE_MS);
  };

  const fail = (failure: unknown): void => {
    if (isSessionGitError(failure) && failure.code === GIT_CODE_FILE_CHANGED) {
      setStale(true);
      return;
    }
    if (isSessionGitError(failure) && failure.code === GIT_CODE_NOT_CONFLICTED) {
      setGone(true);
      return;
    }
    setError(mutationErrorCopy(failure));
  };

  const save = async (markResolved: boolean): Promise<void> => {
    const editor = editorRef.current;
    if (editor === null || token === null) return;
    const content = editor.value;
    setError(null);
    onNotice(null);
    try {
      await mutations.resolve.mutateAsync({
        path,
        token,
        content,
        markResolved,
      });
    } catch (failure: unknown) {
      fail(failure);
      return;
    }
    if (markResolved) {
      dropQuery();
      onDone({ tone: 'ok', text: resolvedNotice(path) });
      return;
    }
    // The draft is now the file: keep the text, take the file's new token.
    baseText.current = content;
    setDirty(false);
    adopt.current = 'token';
    onNotice({ tone: 'ok', text: draftSavedNotice(path) });
    void conflict.refetch();
  };

  const take = async (choice: TakeChoice): Promise<void> => {
    setPendingTake(null);
    setError(null);
    onNotice(null);
    try {
      await mutations.take.mutateAsync({ path, side: choice.side });
    } catch (failure: unknown) {
      fail(failure);
      return;
    }
    dropQuery();
    onDone({ tone: 'ok', text: resolvedNotice(path) });
  };

  const startOver = async (style: SessionGitConflictStyle): Promise<void> => {
    setStartOverOpen(false);
    setError(null);
    onNotice(null);
    try {
      await mutations.restore.mutateAsync({
        path,
        style,
        ...(token === null ? {} : { token }),
      });
    } catch (failure: unknown) {
      fail(failure);
      return;
    }
    adopt.current = 'seed';
    void conflict.refetch();
  };

  const reload = (mode: Adopt): void => {
    adopt.current = mode;
    void conflict.refetch();
  };

  const jumpToNext = (): void => {
    const editor = editorRef.current;
    if (editor === null) return;
    const text = editor.value;
    const offset = nextConflictOffset(text, editor.selectionStart);
    if (offset < 0) return;
    const lineEnd = text.indexOf('\n', offset);
    editor.focus();
    editor.setSelectionRange(offset, lineEnd < 0 ? text.length : lineEnd);
    // A textarea only scrolls a caret into view on typing, so place it by hand.
    const lineHeight = Number.parseFloat(getComputedStyle(editor).lineHeight);
    if (Number.isFinite(lineHeight)) {
      const line = text.slice(0, offset).split('\n').length - 1;
      editor.scrollTop = Math.max(0, line * lineHeight - editor.clientHeight / 3);
    }
  };

  const copyText = (): void => {
    const text = editorRef.current?.value ?? '';
    // The draft may be the only copy left, so a refused write has to be said.
    void writeClipboardText(text).then((ok) => {
      onNotice(
        ok
          ? { tone: 'ok', text: 'Copied your text.' }
          : { tone: 'danger', text: 'Could not copy — select the text instead' },
      );
    });
  };

  // An oversized body is refused by the agent, and past a few MiB it would
  // not fit the relay's tunnel frame at all, so it is never sent.
  const sendable = editable && token !== null && !tooLarge && !working && !gone;
  const canApply = sendable && !scan.hasMarkers;
  const canSaveDraft = sendable && dirty;

  const onEditorKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>): void => {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && canApply) {
      event.preventDefault();
      void save(true);
    }
  };

  const head = (caption: string | null, menu: MenuGroup[] | null = null) => (
    <div className="git-diff-head">
      <div className="git-diff-title">
        <button type="button" className="button git-panel-back" onClick={onBack}>
          ← Changes
        </button>
        <h3 ref={headingRef} tabIndex={-1}>
          {fileName(path)}
        </h3>
        {/* In the head, not beside Apply: the menu opens downward. */}
        {menu !== null && (
          <MenuButton
            label="More conflict actions"
            groups={menu}
            triggerRef={menuTriggerRef}
            className="button button-icon git-diff-action"
          >
            ⋯
          </MenuButton>
        )}
      </div>
      <span className="git-diff-path">{path}</span>
      {caption !== null && <span className="git-conflict-caption">{caption}</span>}
    </div>
  );

  if (loaded === null) {
    if (isTerminalSessionGitError(conflict.error) && isSessionGitError(conflict.error)) {
      const outdated = conflict.error.kind === 'agentOutdated';
      return (
        <>
          {head(null)}
          <SessionGitState
            title={outdated ? 'Update your agent' : 'Session ended'}
            body={
              outdated
                ? 'This agent cannot resolve conflicts yet. Update it, or resolve them in the terminal.'
                : 'This session is no longer running on the agent.'
            }
          />
        </>
      );
    }
    if (conflict.error !== null) {
      const vanished =
        isSessionGitError(conflict.error) && conflict.error.code === GIT_CODE_NOT_CONFLICTED;
      return (
        <>
          {head(null)}
          <SessionGitState
            title={vanished ? 'No longer in conflict' : "Couldn't load the file"}
            body={vanished ? resolvedElsewhereNotice(path) : sessionGitErrorCopy(conflict.error)}
            action={
              vanished
                ? { label: 'Back to changes', onClick: onBack }
                : {
                    label: 'Retry',
                    onClick: () => {
                      void conflict.refetch();
                    },
                  }
            }
          />
        </>
      );
    }
    return (
      <>
        {head(null)}
        <p className="page-note state-loading">Loading the file…</p>
      </>
    );
  }

  const data = loaded;
  const choices = takeChoices(data);
  const kindNotice = conflictKindNotice(data);
  const prompt = startOverPrompt(path);
  const menuGroups: MenuGroup[] = [
    ...(choices.length === 0
      ? []
      : [
          {
            id: 'whole-file',
            label: 'Whole file',
            items: choices.map((choice) => ({
              id: `take-${choice.side}`,
              label: `${choice.label}…`,
              disabled: working,
              onSelect: () => {
                setPendingTake(choice);
              },
            })),
          },
        ]),
    {
      id: 'file',
      label: 'File',
      items: [
        ...(canStartOver(data)
          ? [
              {
                id: 'start-over',
                label: `${START_OVER_LABEL}…`,
                disabled: working,
                onSelect: () => {
                  setStartOverOpen(true);
                },
              },
            ]
          : []),
        { id: 'view-diff', label: 'View diff', onSelect: onViewDiff },
      ],
    },
  ];

  return (
    <>
      {head(conflictCaption(data), menuGroups)}
      {(gone || !conflicted) && (
        <div className="git-panel-notice" data-tone="danger" role="alert">
          <span>{resolvedElsewhereNotice(path)}</span>
          {editable && dirty && (
            <button type="button" className="button" onClick={copyText}>
              Copy my text
            </button>
          )}
          <button type="button" className="button" onClick={onBack}>
            Close
          </button>
        </div>
      )}
      {stale && (
        <div className="git-panel-notice" data-tone="danger" role="alert">
          <span>{fileChangedNotice(path)}</span>
          <button
            type="button"
            className="button"
            disabled={conflict.isFetching}
            onClick={() => {
              reload('seed');
            }}
          >
            Reload file
          </button>
          <button
            type="button"
            className="button"
            disabled={conflict.isFetching}
            onClick={() => {
              reload('token-warn');
            }}
          >
            Keep my edits
          </button>
        </div>
      )}
      {error !== null && (
        <div className="git-panel-notice" data-tone="danger" role="alert">
          <span>{error}</span>
          <button
            type="button"
            className="button button-icon"
            aria-label="Dismiss"
            title="Dismiss"
            onClick={() => {
              setError(null);
            }}
          >
            ✕
          </button>
        </div>
      )}
      {editable ? (
        <>
          <div className="git-conflict-strip">
            <span className="git-conflict-count" role="status">
              {conflictsLeftLabel(scan.conflicts, scan.hasMarkers)}
            </span>
            <button
              type="button"
              className="button"
              disabled={scan.conflicts === 0}
              onClick={jumpToNext}
            >
              Next conflict
            </button>
            <button
              type="button"
              className="button"
              aria-expanded={legendOpen}
              aria-controls="git-conflict-legend"
              onClick={() => {
                setLegendOpen((open) => !open);
              }}
            >
              Which side is which?
            </button>
          </div>
          {legendOpen && (
            <ul id="git-conflict-legend" className="git-conflict-legend">
              {conflictLegend(data, scan.hasBase).map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          )}
          <textarea
            // Remounted per seed: the one way to replace an uncontrolled value.
            key={seed.version}
            ref={editorRef}
            className="git-conflict-editor"
            aria-label={`Contents of ${path}`}
            wrap="off"
            spellCheck={false}
            autoCapitalize="off"
            autoCorrect="off"
            autoComplete="off"
            readOnly={working || gone}
            defaultValue={seed.text}
            onInput={onInput}
            onKeyDown={onEditorKeyDown}
          />
          {overwriteWarning && <p className="git-conflict-warning">{KEEP_EDITS_WARNING}</p>}
          {tooLarge && (
            <p className="git-conflict-warning" role="alert">
              {tooLargeToSaveNotice(byteLimit)}
            </p>
          )}
        </>
      ) : (
        <div className="git-conflict-blocked">
          <p>{kindNotice}</p>
          {choices.length > 0 && (
            <div className="button-row">
              {choices.map((choice) => (
                <button
                  key={choice.side}
                  type="button"
                  className={choice.danger ? 'button button-danger' : 'button'}
                  disabled={working || gone}
                  onClick={() => {
                    setPendingTake(choice);
                  }}
                >
                  {choice.label}…
                </button>
              ))}
            </div>
          )}
        </div>
      )}
      {editable && (
        <div className="git-conflict-actions">
          <button
            type="button"
            className="button button-primary"
            disabled={!canApply}
            title={
              scan.hasMarkers
                ? 'Remove every conflict marker first'
                : 'Save the file and mark it resolved'
            }
            onClick={() => {
              void save(true);
            }}
          >
            {mutations.resolve.isPending ? 'Saving…' : 'Apply & mark resolved'}
          </button>
          <button
            type="button"
            className="button"
            disabled={!canSaveDraft}
            title="Save the file as it is; the conflict stays open"
            onClick={() => {
              void save(false);
            }}
          >
            Save draft
          </button>
        </div>
      )}
      {pendingTake !== null && (
        <Modal
          role="alertdialog"
          labelledBy="git-conflict-take-title"
          initialFocus={cancelRef}
          onClose={() => {
            setPendingTake(null);
          }}
        >
          <h2 id="git-conflict-take-title">{pendingTake.title}</h2>
          <p>{pendingTake.body}</p>
          <div className="button-row">
            <button
              type="button"
              className={pendingTake.danger ? 'button button-danger' : 'button button-primary'}
              onClick={() => {
                void take(pendingTake);
              }}
            >
              {pendingTake.confirm}
            </button>
            <button
              type="button"
              ref={cancelRef}
              className="button"
              onClick={() => {
                setPendingTake(null);
              }}
            >
              Cancel
            </button>
          </div>
        </Modal>
      )}
      {startOverOpen && (
        <Modal
          role="alertdialog"
          labelledBy="git-conflict-start-over-title"
          initialFocus={cancelRef}
          onClose={() => {
            setStartOverOpen(false);
          }}
        >
          <h2 id="git-conflict-start-over-title">{prompt.title}</h2>
          <p>{prompt.body}</p>
          <div className="button-row">
            <button
              type="button"
              className="button button-danger"
              onClick={() => {
                void startOver('merge');
              }}
            >
              {START_OVER_LABEL}
            </button>
            <button
              type="button"
              className="button button-danger"
              onClick={() => {
                void startOver('diff3');
              }}
            >
              {START_OVER_WITH_BASE_LABEL}
            </button>
            <button
              type="button"
              ref={cancelRef}
              className="button"
              onClick={() => {
                setStartOverOpen(false);
              }}
            >
              Cancel
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}
