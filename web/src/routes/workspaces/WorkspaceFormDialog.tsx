import { useEffect, useMemo, useState } from 'react';
import { Controller, useForm } from 'react-hook-form';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  createWorkspace,
  listLinearLabels,
  listLinearStates,
  updateWorkspace,
  type WorkspaceInfo,
} from '../../api/workspaceRepository';
import { conflictDetailString, isConflict } from '../../api/conflicts';
import { isApiError } from '../../api/errors';
import { queryKeys } from '../../lib/queryKeys';
import { autoScanFilterSchema, type AutoScanFilter } from '../../protocol/dto/workspaces';
import { Modal } from '../../components/Modal';
import { PasswordField } from '../../components/form/PasswordField';
import { PathField } from '../../components/form/PathField';
import { TextField } from '../../components/form/TextField';
import { AutoScanFilterSection } from './AutoScanFilterSection';
import { FlagsEditor } from './FlagsEditor';
import { flagEntriesFrom, type FlagEntry } from './flagEntry';
import {
  buildWorkspaceUpdate,
  buildWorkspaceWrite,
  canSave,
  filterError,
  nameError,
  pathError,
  showsFilterSection,
  type WorkspaceFormValues,
} from './workspaceForm';

interface WorkspaceFormDialogProps {
  agentId: string;
  /** `null` for create, or the live snapshot of the workspace being edited. */
  editing: WorkspaceInfo | null;
  /** Create only: the saved workspace, called just before `onClose`. */
  onCreated?: (workspace: WorkspaceInfo) => void;
  onClose: () => void;
}

const DEFAULT_FILTER: AutoScanFilter = autoScanFilterSchema.parse({});

function seedValues(editing: WorkspaceInfo | null): WorkspaceFormValues {
  return {
    name: editing?.name ?? '',
    path: editing?.path ?? '',
    condaEnv: editing?.conda_env ?? '',
    // Always blank: the server never echoes the key, so an edit form has
    // nothing to prefill and "leave blank" has to mean "keep".
    linearApiKey: '',
    clearLinearKey: false,
  };
}

/**
 * Create / edit a workspace.
 *
 * The form tracks the **live** snapshot rather than a copy frozen at open:
 * `values` re-seeds every field the user has not touched, `keepDirtyValues`
 * protects the ones they have, and `buildWorkspaceUpdate` diffs against that same
 * snapshot. The three agree by construction — an untouched field equals the
 * snapshot and is therefore omitted from the patch, so a `projects.changed`
 * arriving mid-edit refreshes what the user is not looking at without ever
 * sending a value they did not type.
 */
export function WorkspaceFormDialog({
  agentId,
  editing,
  onCreated,
  onClose,
}: WorkspaceFormDialogProps) {
  const queryClient = useQueryClient();
  const values = useMemo(() => seedValues(editing), [editing]);

  const form = useForm<WorkspaceFormValues>({
    defaultValues: values,
    values,
    resetOptions: { keepDirtyValues: true },
    mode: 'onTouched',
  });

  // Flags and the filter are arrays, where a merge has no honest field-level
  // answer, so they carry their own touch gate: re-seeded from the live snapshot
  // while untouched, frozen the moment the user edits them.
  const [flags, setFlags] = useState<FlagEntry[]>(() =>
    flagEntriesFrom(editing?.default_flags ?? {}),
  );
  const [flagsTouched, setFlagsTouched] = useState(false);
  const [filter, setFilter] = useState<AutoScanFilter>(
    () => editing?.auto_scan_filter ?? DEFAULT_FILTER,
  );
  const [filterTouched, setFilterTouched] = useState(false);

  useEffect(() => {
    if (!flagsTouched) setFlags(flagEntriesFrom(editing?.default_flags ?? {}));
  }, [editing, flagsTouched]);

  useEffect(() => {
    if (!filterTouched) setFilter(editing?.auto_scan_filter ?? DEFAULT_FILTER);
  }, [editing, filterTouched]);

  const linearApiKey = form.watch('linearApiKey');
  const clearLinearKey = form.watch('clearLinearKey');
  const showFilter = showsFilterSection({ linearApiKey, clearLinearKey }, editing);

  const metadata = useQuery({
    queryKey: queryKeys.linearMetadata(agentId, editing?.name ?? ''),
    queryFn: async () => {
      const name = editing?.name ?? '';
      try {
        const [labels, states] = await Promise.all([
          listLinearLabels(agentId, name),
          listLinearStates(agentId, name),
        ]);
        return { labels, states, failed: false };
      } catch {
        // Swallowed rather than thrown: a rejected query trips the global
        // error toast, and a Linear outage should degrade the chip lists to
        // "what this workspace already selects", not shout at the user.
        return { labels: [], states: [], failed: true };
      }
    },
    enabled: editing?.has_linear_key === true,
  });

  const save = useMutation({
    mutationFn: async (submitted: {
      values: WorkspaceFormValues;
      flags: FlagEntry[];
      filter: AutoScanFilter;
    }) => {
      const draft = {
        ...submitted.values,
        flags: submitted.flags,
        filter: submitted.filter,
      };
      if (editing === null) {
        return createWorkspace(agentId, buildWorkspaceWrite(draft));
      }
      await updateWorkspace(agentId, editing.name, buildWorkspaceUpdate(editing, draft));
      return null;
    },
    // The submitted variables carry the plaintext Linear key, and TanStack keeps
    // them on the mutation entry for `gcTime` after it settles — readable
    // app-wide through `getMutationCache()`. Drop the entry immediately, as the
    // Codex login mutation in `NewSession.tsx` already does for the same reason;
    // the dialog's errors live in react-hook-form state, so nothing reads
    // post-settle state here.
    gcTime: 0,
    onSettled: () => {
      save.reset();
    },
    onSuccess: async (created) => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.workspaces(agentId) });
      if (created !== null) onCreated?.(created);
      onClose();
    },
    onError: (error: unknown) => {
      // A 409 is a duplicate name, a rename collision, or a rename blocked by an
      // active auto-scan — all of them about the name, so both mobile forms show
      // it under that field rather than as a toast.
      form.setError(
        isConflict(error) ? 'name' : 'root',
        {
          message: isConflict(error)
            ? conflictDetailString(error)
            : isApiError(error)
              ? `Save failed: ${error.message}`
              : 'Save failed',
        },
        { shouldFocus: isConflict(error) },
      );
    },
  });

  const state = { ...form.watch(), flags, filter };
  const filterMessage = filterError(filter);
  const submittable = canSave(state) && !save.isPending;
  const titleId = 'workspace-form-title';

  return (
    <Modal labelledBy={titleId} dismissible={!save.isPending} onClose={onClose}>
      <h2 id={titleId}>{editing === null ? 'New workspace' : 'Edit workspace'}</h2>

      <form
        className="workspace-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (!submittable) return;
          save.mutate({ values: form.getValues(), flags, filter });
        }}
      >
        <TextField
          label="Name"
          autoFocus
          error={form.formState.errors.name?.message}
          registration={form.register('name', {
            validate: (value) => nameError(value) ?? true,
          })}
        />
        <Controller
          control={form.control}
          name="path"
          rules={{ validate: (value) => pathError(value) ?? true }}
          render={({ field }) => (
            <PathField
              label="Path"
              agentId={agentId}
              hint={(browse) => `Absolute path on the agent host. ${browse}`}
              error={form.formState.errors.path?.message}
              value={field.value}
              onChange={field.onChange}
              onBlur={field.onBlur}
              inputRef={field.ref}
            />
          )}
        />
        <TextField
          label="Conda environment (optional)"
          hint="Activated before the agent starts. Clear it to stop activating one."
          registration={form.register('condaEnv')}
        />

        {/* Masked with a reveal toggle, like every other secret in this app and
            like both mobile clients' `SecretTextField`. */}
        <PasswordField
          label="Linear API key (optional)"
          autoComplete="off"
          secretNoun="key"
          placeholder={
            editing === null
              ? ''
              : clearLinearKey
                ? 'Will be cleared on save'
                : 'Leave blank to keep current'
          }
          registration={form.register('linearApiKey', {
            // Typing a key cancels a pending clear — the two are contradictory,
            // and the value the user just entered is the newer intent.
            onChange: (event: React.ChangeEvent<HTMLInputElement>) => {
              if (event.target.value !== '') {
                form.setValue('clearLinearKey', false, { shouldDirty: true });
              }
            },
          })}
        />
        {editing?.has_linear_key === true && (
          <button
            type="button"
            className="button"
            disabled={clearLinearKey}
            onClick={() => {
              form.setValue('clearLinearKey', true, { shouldDirty: true });
              form.setValue('linearApiKey', '', { shouldDirty: true });
            }}
          >
            {clearLinearKey ? 'Will clear on save' : 'Clear key'}
          </button>
        )}

        <FlagsEditor
          flags={flags}
          onChange={(next) => {
            setFlagsTouched(true);
            setFlags(next);
          }}
        />

        {showFilter && (
          <AutoScanFilterSection
            filter={filter}
            labels={metadata.data?.labels ?? []}
            states={metadata.data?.states ?? []}
            loading={metadata.isFetching}
            error={
              editing === null
                ? 'Save the workspace first to fetch labels/states from Linear.'
                : metadata.data?.failed === true
                  ? 'Couldn’t load Linear labels/states.'
                  : null
            }
            onChange={(next) => {
              setFilterTouched(true);
              setFilter(next);
            }}
            onRefresh={() => {
              // Nothing to fetch before the workspace exists: discovery is keyed
              // by workspace name and uses that workspace's stored Linear key.
              if (editing !== null) void metadata.refetch();
            }}
          />
        )}

        {/* Only when the section is hidden. The filter is validated either way,
            so emptying it and then clearing the key would otherwise disable Save
            with no reason shown and no control on screen that could fix it —
            while the visible section renders both messages itself. */}
        {filterMessage !== null && !showFilter && (
          <p className="field-error">{filterMessage}</p>
        )}
        {form.formState.errors.root?.message !== undefined && (
          <p className="auth-error" role="alert">
            {form.formState.errors.root.message}
          </p>
        )}

        <div className="button-row">
          <button type="submit" className="button button-primary" disabled={!submittable}>
            {save.isPending ? 'Saving…' : 'Save'}
          </button>
          <button
            type="button"
            className="button"
            disabled={save.isPending}
            onClick={onClose}
          >
            Cancel
          </button>
        </div>
      </form>
    </Modal>
  );
}
