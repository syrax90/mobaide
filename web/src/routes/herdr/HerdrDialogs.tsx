import { useLayoutEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { listProjects } from '../../api/projectRepository';
import { fetchHerdrPaneText, type HerdrPaneRef } from '../../api/herdrRepository';
import { isApiError } from '../../api/errors';
import { Modal } from '../../components/Modal';
import { SelectField, type SelectOption } from '../../components/form/SelectField';
import { TextField } from '../../components/form/TextField';
import { queryKeys, wsBackedQueryOptions } from '../../lib/queryKeys';
import type { HerdrAgentKind } from '../../protocol/dto/herdr';

const LABEL_MAX_CHARS = 80;
const HISTORY_LINES = 1000;
/** The folder choice that shows the text field instead of a project. */
const CUSTOM_FOLDER = '\u0000custom';

export type HerdrTarget = 'workspace' | 'tab' | 'pane';

/** The reason an action failed, inside its dialog. */
function DialogError({ error }: { error: string | null }) {
  if (error === null) return null;
  return (
    <p className="field-error herdr-dialog-error" role="alert">
      {error}
    </p>
  );
}

const TARGET_NOUN: Readonly<Record<HerdrTarget, string>> = {
  workspace: 'workspace',
  tab: 'tab',
  pane: 'pane',
};

export interface HerdrCreateValues {
  cwd: string | null;
  label: string | null;
  agent: string | null;
}

interface HerdrCreateDialogProps {
  agentId: string;
  /** A workspace needs a folder; a tab opens in its workspace's folder. */
  placement: 'workspace' | 'tab';
  /** What the server calls a top-level group: "workspace", or "terminal" on herdr-core. */
  noun: string;
  agents: readonly HerdrAgentKind[];
  busy: boolean;
  /** Why the last try failed; shown here, above the buttons. */
  error: string | null;
  onCreate: (values: HerdrCreateValues) => void;
  onCancel: () => void;
}

/**
 * New workspace or new tab. The folder comes from the agent's projects, with
 * a free path as the last choice, so the usual case is two clicks.
 */
export function HerdrCreateDialog({
  agentId,
  placement,
  noun,
  agents,
  busy,
  error,
  onCreate,
  onCancel,
}: HerdrCreateDialogProps) {
  const projects = useQuery({
    queryKey: queryKeys.projects(agentId),
    queryFn: () => listProjects(agentId),
    enabled: placement === 'workspace',
    meta: { silent: true },
    ...wsBackedQueryOptions,
  });
  const [folder, setFolder] = useState<string | null>(null);
  const [customPath, setCustomPath] = useState('');
  const [label, setLabel] = useState('');
  const [agent, setAgent] = useState<string | null>(null);

  const available = (projects.data ?? []).filter((project) => project.path_exists);
  const folderOptions: SelectOption[] = [
    { value: null, label: 'Default folder' },
    ...available.map((project) => ({ value: project.path, label: project.name })),
    { value: CUSTOM_FOLDER, label: 'Another folder…' },
  ];
  const agentOptions: SelectOption[] = [
    { value: null, label: 'None (a terminal)' },
    ...agents.map((kind) => ({ value: kind.id, label: kind.label })),
  ];

  const custom = folder === CUSTOM_FOLDER;
  const cwd = custom ? customPath.trim() : folder;
  const pathError =
    custom && customPath.trim() !== '' && !customPath.trim().startsWith('/') &&
    !/^[A-Za-z]:[\\/]/.test(customPath.trim()) && !customPath.trim().startsWith('~')
      ? 'Enter a full path, for example /home/me/project.'
      : undefined;
  const canSubmit = !busy && pathError === undefined && !(custom && customPath.trim() === '');

  return (
    <Modal labelledBy="herdr-create-title" dismissible={!busy} onClose={onCancel}>
      <h2 id="herdr-create-title">New {placement === 'workspace' ? noun : 'tab'}</h2>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (!canSubmit) return;
          onCreate({
            cwd: placement === 'workspace' && cwd !== null && cwd !== '' ? cwd : null,
            label: label.trim() === '' ? null : label.trim(),
            agent,
          });
        }}
      >
        {placement === 'workspace' && (
          <SelectField label="Folder" value={folder} options={folderOptions} onChange={setFolder} />
        )}
        {placement === 'workspace' && custom && (
          <TextField
            label="Folder path"
            value={customPath}
            onChange={setCustomPath}
            placeholder="/home/me/project"
            autoFocus
            error={pathError}
          />
        )}
        <TextField
          label="Name (optional)"
          value={label}
          onChange={(value) => {
            setLabel(value.slice(0, LABEL_MAX_CHARS));
          }}
        />
        {agents.length > 0 && (
          <SelectField
            label="Start an agent"
            value={agent}
            options={agentOptions}
            onChange={setAgent}
          />
        )}
        <DialogError error={error} />
        <div className="button-row">
          <button type="submit" className="button button-primary" disabled={!canSubmit}>
            {busy ? 'Creating…' : 'Create'}
          </button>
          <button type="button" className="button" disabled={busy} onClick={onCancel}>
            Cancel
          </button>
        </div>
      </form>
    </Modal>
  );
}

interface HerdrRenameDialogProps {
  target: HerdrTarget;
  current: string;
  busy: boolean;
  /** Why the last try failed; shown here, above the buttons. */
  error: string | null;
  onRename: (label: string) => void;
  onCancel: () => void;
}

export function HerdrRenameDialog({
  target,
  current,
  busy,
  error,
  onRename,
  onCancel,
}: HerdrRenameDialogProps) {
  const [label, setLabel] = useState(current);
  const trimmed = label.trim();
  const canSubmit = !busy && trimmed !== '' && trimmed !== current;
  return (
    <Modal labelledBy="herdr-rename-title" dismissible={!busy} onClose={onCancel}>
      <h2 id="herdr-rename-title">Rename {TARGET_NOUN[target]}</h2>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (canSubmit) onRename(trimmed);
        }}
      >
        <TextField
          label="Name"
          value={label}
          autoFocus
          onChange={(value) => {
            setLabel(value.slice(0, LABEL_MAX_CHARS));
          }}
        />
        <DialogError error={error} />
        <div className="button-row">
          <button type="submit" className="button button-primary" disabled={!canSubmit}>
            {busy ? 'Renaming…' : 'Rename'}
          </button>
          <button type="button" className="button" disabled={busy} onClick={onCancel}>
            Cancel
          </button>
        </div>
      </form>
    </Modal>
  );
}

interface HerdrCloseDialogProps {
  target: HerdrTarget;
  label: string;
  busy: boolean;
  /** Why the last try failed; shown here, above the buttons. */
  error: string | null;
  onClose: () => void;
  onCancel: () => void;
}

/**
 * Herdr closes at once and cannot undo it, so the question is asked here. It
 * says what is lost: the programs in the panes stop.
 */
export function HerdrCloseDialog({
  target,
  label,
  busy,
  error,
  onClose,
  onCancel,
}: HerdrCloseDialogProps) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  return (
    <Modal
      role="alertdialog"
      labelledBy="herdr-close-title"
      dismissible={!busy}
      initialFocus={cancelRef}
      onClose={onCancel}
    >
      <h2 id="herdr-close-title">Close {TARGET_NOUN[target]}?</h2>
      <p>
        Close &quot;{label}&quot;?{' '}
        {target === 'pane'
          ? 'The program in this pane stops.'
          : `Every pane in this ${TARGET_NOUN[target]} closes and its programs stop.`}
      </p>
      <DialogError error={error} />
      <div className="button-row">
        <button type="button" className="button button-danger" disabled={busy} onClick={onClose}>
          {busy ? 'Closing…' : 'Close'}
        </button>
        <button type="button" ref={cancelRef} className="button" disabled={busy} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </Modal>
  );
}

interface HerdrStartAgentDialogProps {
  agents: readonly HerdrAgentKind[];
  busy: boolean;
  /** Why the last try failed; shown here, above the buttons. */
  error: string | null;
  onStart: (agent: string) => void;
  onCancel: () => void;
}

export function HerdrStartAgentDialog({
  agents,
  busy,
  error,
  onStart,
  onCancel,
}: HerdrStartAgentDialogProps) {
  return (
    <Modal labelledBy="herdr-agent-title" dismissible={!busy} onClose={onCancel}>
      <h2 id="herdr-agent-title">Start an agent</h2>
      <p className="page-note">The agent starts in this pane&apos;s terminal.</p>
      <div className="herdr-agent-choices">
        {agents.map((kind) => (
          <button
            key={kind.id}
            type="button"
            className="button"
            disabled={busy}
            onClick={() => {
              onStart(kind.id);
            }}
          >
            {kind.label}
          </button>
        ))}
      </div>
      <DialogError error={error} />
      <div className="button-row">
        <button type="button" className="button" disabled={busy} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </Modal>
  );
}

interface HerdrHistoryDialogProps {
  agentId: string;
  pane: HerdrPaneRef;
  title: string;
  onClose: () => void;
}

/**
 * The pane's recent output as plain text. The live view shows only the
 * screen, so this is how earlier output is read and copied.
 */
export function HerdrHistoryDialog({ agentId, pane, title, onClose }: HerdrHistoryDialogProps) {
  const text = useQuery({
    queryKey: queryKeys.herdrPaneText(agentId, pane.serverId, pane.paneId),
    queryFn: () => fetchHerdrPaneText(agentId, pane, HISTORY_LINES),
    // Read on each open: the text is a moment of a pane that keeps printing.
    gcTime: 0,
    staleTime: 0,
    meta: { silent: true },
  });
  // A terminal's news is at its end: the first read and each Refresh show it.
  const textRef = useRef<HTMLPreElement>(null);
  const { dataUpdatedAt } = text;
  useLayoutEffect(() => {
    const element = textRef.current;
    if (element !== null) element.scrollTop = element.scrollHeight;
  }, [dataUpdatedAt]);
  return (
    <Modal labelledBy="herdr-history-title" className="herdr-history" onClose={onClose}>
      <div className="herdr-history-head">
        <h2 id="herdr-history-title">{title}</h2>
        <div className="button-row">
          <button
            type="button"
            className="button"
            disabled={text.isFetching}
            onClick={() => void text.refetch()}
          >
            Refresh
          </button>
          <button type="button" className="button" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
      {text.isPending ? (
        <p className="page-note state-loading">Reading the pane…</p>
      ) : text.isError ? (
        <p className="field-error" role="alert">
          {isApiError(text.error) ? text.error.message : 'Could not read the pane.'}
        </p>
      ) : (
        <>
          {text.data.truncated && (
            <p className="page-note">Older lines exist. This is the last part.</p>
          )}
          {/* Focusable, so the keyboard can scroll it and select-all stays inside. */}
          <pre ref={textRef} className="herdr-history-text" tabIndex={0}>
            {text.data.text === '' ? 'The pane is empty.' : text.data.text}
          </pre>
        </>
      )}
    </Modal>
  );
}
