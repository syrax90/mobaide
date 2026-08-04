import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router';
import { fetchAgentCapabilities } from '../../../api/agentCapabilities';
import { startAutoScan } from '../../../api/autoscanRepository';
import { codexLogin } from '../../../api/codexAuthRepository';
import {
  createCustomAgentType,
  deleteCustomAgentType,
  listCustomAgentTypes,
} from '../../../api/customAgentTypeRepository';
import { listProjects } from '../../../api/projectRepository';
import { createSession, type CreateSessionRequest } from '../../../api/sessionRepository';
import { queryKeys, wsBackedQueryOptions } from '../../../lib/queryKeys';
import { customSessionTypeId } from '../../../protocol/wireSchema';
import { useLastSessionStore } from '../../../stores/lastSessionStore';
import { useToastStore } from '../../../stores/toastStore';
import { terminalPath } from '../terminalPath';
import { CodexLoginDialog } from './CodexLoginDialog';
import { ConfigStep } from './ConfigStep';
import { CreateCustomTypeDialog, DeleteCustomTypeDialog } from './CustomTypeDialogs';
import { ModeStep } from './ModeStep';
import { ProjectStep } from './ProjectStep';
import {
  buildCreateRequest,
  buildStartScanRequest,
  type StartScanRequest,
} from './buildCreateRequest';
import {
  classifyAutoScanError,
  classifyCreateError,
  codexLoginErrorMessage,
  customTypeErrorMessage,
} from './createErrors';
import { buildModeCards } from './modeCatalog';
import {
  INITIAL_WIZARD_STATE,
  isCustomMode,
  validateCustomPath,
  wizardReducer,
  type WizardMode,
  type WizardStep,
} from './wizardState';
import {
  parseWizardLocation,
  resolveWizardLocation,
  serializeWizardLocation,
} from './wizardUrl';

export function NewSession() {
  const { agentId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  // Seeded from the URL once, because the reducer does not survive a remount
  // while the search params do: without this a reload at `?path=/foo` would
  // render an empty path field with Continue disabled, and the user would have
  // to retype a path the URL is still carrying.
  const [state, dispatch] = useReducer(wizardReducer, searchParams, (params) => {
    const { selection } = parseWizardLocation(params);
    if (selection.kind !== 'customPath') return INITIAL_WIZARD_STATE;
    return {
      ...INITIAL_WIZARD_STATE,
      customPathDraft: selection.path,
      customPathError: validateCustomPath(selection.path),
    };
  });
  const [error, setError] = useState<string | null>(null);
  const [codexLoginError, setCodexLoginError] = useState<string | null>(null);
  const [showCodexLogin, setShowCodexLogin] = useState(false);
  const [showAddType, setShowAddType] = useState(false);
  const [typeDialogError, setTypeDialogError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; name: string } | null>(null);
  // The request that hit `codex_auth_required`, re-sent verbatim once the key
  // lands. Captured rather than rebuilt so the retry cannot pick up edits.
  const pendingRequest = useRef<CreateSessionRequest | null>(null);
  const setLastUsedSession = useLastSessionStore((state) => state.setLastUsedSession);

  const resolvedAgentId = agentId ?? '';

  const projects = useQuery({
    queryKey: queryKeys.projects(resolvedAgentId),
    queryFn: () => listProjects(resolvedAgentId),
    enabled: resolvedAgentId !== '',
    ...wsBackedQueryOptions,
  });

  const customTypes = useQuery({
    queryKey: queryKeys.customAgentTypes(),
    queryFn: listCustomAgentTypes,
    ...wsBackedQueryOptions,
  });

  const capabilities = useQuery({
    queryKey: queryKeys.agentCapabilities(resolvedAgentId),
    queryFn: () => fetchAgentCapabilities(resolvedAgentId),
    enabled: resolvedAgentId !== '',
    // Re-probed whenever the picker is entered: installing a CLI while the
    // wizard is open should un-grey its card. Both mobile clients do this.
    refetchOnMount: 'always',
    ...wsBackedQueryOptions,
  });

  const location = useMemo(() => parseWizardLocation(searchParams), [searchParams]);

  const project = useMemo(() => {
    const selection = location.selection;
    if (selection.kind !== 'stored') return null;
    return projects.data?.find((entry) => entry.name === selection.name) ?? null;
  }, [projects.data, location.selection]);

  const cards = useMemo(
    () =>
      buildModeCards({
        capabilities: capabilities.data,
        project,
        customTypes: customTypes.data ?? [],
      }),
    [capabilities.data, project, customTypes.data],
  );

  const isModeSelectable = useCallback(
    (mode: WizardMode) => {
      const card = cards.find((entry) => entry.mode === mode);
      // A mode with no card is one whose custom type vanished mid-wizard. It
      // stays selectable on purpose (iOS): the launch then surfaces the relay's
      // 404 instead of silently degrading to a plain Terminal.
      return card === undefined ? isCustomMode(mode) : card.enabled;
    },
    [cards],
  );

  const go = useCallback(
    (next: { step: WizardStep; mode?: WizardMode | null }, replace = false) => {
      setSearchParams(
        serializeWizardLocation({
          step: next.step,
          mode: next.mode === undefined ? location.mode : next.mode,
          selection: location.selection,
        }),
        { replace },
      );
    },
    [location.mode, location.selection, setSearchParams],
  );

  // Keep the URL honest: a hand-edited or stale deep link is replaced rather
  // than rendered against state that cannot support it.
  useEffect(() => {
    const clamp = resolveWizardLocation({
      requested: location,
      projects: projects.data,
      isModeSelectable,
    });
    if (clamp === null) return;
    // The invalid-custom-path branch clamps to the location it is already at, so
    // without this guard the effect re-writes an identical URL on every dep
    // change — and browsers hard-cap `history.replaceState` (Safari throws at
    // 100 per 30s rather than degrading).
    const next = serializeWizardLocation(clamp);
    if (next.toString() !== searchParams.toString()) {
      setSearchParams(next, { replace: true });
    }
  }, [location, projects.data, isModeSelectable, searchParams, setSearchParams]);

  // Seeded once per project, so re-entering the step never discards edits.
  useEffect(() => {
    if (location.mode === 'auto_scan' && project !== null) {
      dispatch({
        type: 'seedAutoScanPrompt',
        projectName: project.name,
        prompt: project.autoScanPrompt ?? '',
      });
    }
  }, [location.mode, project]);

  const create = useMutation({
    mutationFn: (request: CreateSessionRequest) => createSession(resolvedAgentId, request),
    onSuccess: (session) => {
      setShowCodexLogin(false);
      pendingRequest.current = null;
      setLastUsedSession(resolvedAgentId, session.session_id);
      void navigate(terminalPath(resolvedAgentId, session.session_id), { replace: true });
    },
    onError: (cause, request) => {
      const failure = classifyCreateError(cause, request);
      if (failure.kind === 'codexAuthRequired') {
        pendingRequest.current = request;
        setCodexLoginError(null);
        setShowCodexLogin(true);
        return;
      }
      if (failure.kind === 'customTypeDeleted') {
        // Refresh so the stale card disappears with the message.
        void queryClient.invalidateQueries({ queryKey: queryKeys.customAgentTypes() });
      }
      setError(failure.message);
    },
  });

  const scan = useMutation({
    mutationFn: (request: StartScanRequest) => startAutoScan(resolvedAgentId, request),
    onSuccess: (_result, request) => {
      useToastStore.getState().show(`Auto scan started for "${request.project_name}"`, 'success');
      // The monitor screen is WEB-10; the scan's sessions arrive on the list via
      // `sessions.changed`, so that is the honest destination for now.
      void navigate('/', { replace: true });
    },
    onError: (cause) => {
      setError(classifyAutoScanError(cause));
    },
  });

  const login = useMutation({
    mutationFn: (apiKey: string) => codexLogin(resolvedAgentId, apiKey),
    // TanStack keeps each `mutate` call's variables on the mutation entry for
    // `gcTime` after it settles, which for this one would leave the API key
    // readable app-wide (`getMutationCache().getAll()`) minutes after the dialog
    // closed. Drop the entry immediately instead; the dialog's own error text
    // lives in `codexLoginError`, so nothing reads post-settle state.
    gcTime: 0,
    onSettled: () => {
      login.reset();
    },
    onSuccess: (authenticated) => {
      if (!authenticated) {
        // The agent does not send this today, but the schema allows it and a
        // silent retry would re-fail with the same 409 and loop the dialog.
        setCodexLoginError('Codex rejected that API key.');
        return;
      }
      const request = pendingRequest.current;
      if (request !== null) create.mutate(request);
    },
    onError: (cause) => {
      setCodexLoginError(codexLoginErrorMessage(cause));
    },
  });

  const addType = useMutation({
    mutationFn: createCustomAgentType,
    onSuccess: async () => {
      setShowAddType(false);
      setTypeDialogError(null);
      await queryClient.invalidateQueries({ queryKey: queryKeys.customAgentTypes() });
    },
    onError: (cause) => {
      setTypeDialogError(customTypeErrorMessage(cause));
    },
  });

  const removeType = useMutation({
    mutationFn: deleteCustomAgentType,
    onSuccess: async (_result, typeId) => {
      setDeleteTarget(null);
      setTypeDialogError(null);
      // The selected mode may have just disappeared; the clamp effect handles it.
      if (location.mode === `custom_${typeId}`) go({ step: 'mode', mode: null }, true);
      await queryClient.invalidateQueries({ queryKey: queryKeys.customAgentTypes() });
    },
    onError: (cause) => {
      setTypeDialogError(customTypeErrorMessage(cause));
    },
  });

  const submit = () => {
    if (location.mode === null) return;
    setError(null);
    // The URL owns the selection, not the reducer: the reducer resets on every
    // remount (reload, deep link, Back into the wizard) while the search params
    // survive, so reading `state.selection` here would silently build nothing.
    // `parseWizardLocation` trims the custom path exactly as the reducer does,
    // and `resolveWizardLocation` has already clamped an unknown project or a
    // non-absolute path back to step one, so this cannot be invalid.
    const context = { state: { ...state, selection: location.selection }, mode: location.mode, project };
    const request =
      location.mode === 'auto_scan'
        ? buildStartScanRequest(context)
        : buildCreateRequest(context);
    if (request === null) {
      // Never fail silently: a null here means the selection did not survive,
      // and a dead button with no message is the worst possible symptom.
      setError('Pick a project before launching.');
      return;
    }
    if (location.mode === 'auto_scan') {
      scan.mutate(request as StartScanRequest);
    } else {
      create.mutate(request as CreateSessionRequest);
    }
  };

  if (agentId === undefined) {
    return (
      <section className="page">
        <h1>Agent not specified</h1>
        <Link to="/">Back to sessions</Link>
      </section>
    );
  }

  const submitting = create.isPending || scan.isPending || login.isPending;

  return (
    <section className="page wizard">
      <header className="page-header">
        <h1>New session</h1>
        <Link to="/" className="button">
          Cancel
        </Link>
      </header>

      <ol className="wizard-steps">
        {(['project', 'mode', 'config'] as const).map((step) => (
          <li
            key={step}
            className={`wizard-step${location.step === step ? ' wizard-step-current' : ''}`}
            aria-current={location.step === step ? 'step' : undefined}
          >
            {step === 'project' ? 'Project' : step === 'mode' ? 'Agent' : 'Options'}
          </li>
        ))}
      </ol>

      {projects.isPending ? (
        <p className="page-note">Loading projects…</p>
      ) : projects.isError ? (
        <p className="auth-error">Failed to load projects.</p>
      ) : location.step === 'project' ? (
        <ProjectStep
          projects={projects.data}
          selection={location.selection}
          customPathDraft={state.customPathDraft}
          customPathError={state.customPathError}
          dispatch={(action) => {
            dispatch(action);
            // Picking a project is one discrete act, so it reaches the URL at
            // once. The custom-path field deliberately does not: writing per
            // keystroke is a router navigation and a `history.replaceState` per
            // character, which browsers rate-limit (Safari throws past 100 in
            // 30s). The draft lives in the reducer until Continue.
            if (action.type === 'selectStoredProject') {
              setSearchParams(
                serializeWizardLocation({
                  step: 'project',
                  mode: null,
                  selection: { kind: 'stored', name: action.project.name },
                }),
                { replace: true },
              );
            }
          }}
          onContinue={() => {
            const draft = state.customPathDraft.trim();
            setSearchParams(
              serializeWizardLocation({
                step: 'mode',
                mode: null,
                selection:
                  draft === '' ? location.selection : { kind: 'customPath', path: draft },
              }),
            );
          }}
        />
      ) : location.step === 'mode' ? (
        <ModeStep
          cards={cards}
          onSelect={(mode) => {
            go({ step: 'config', mode });
          }}
          onAddCustomType={() => {
            setTypeDialogError(null);
            setShowAddType(true);
          }}
          onDeleteCustomType={(mode) => {
            const id = customSessionTypeId(mode);
            const match = customTypes.data?.find((type) => type.id === id);
            if (id !== null && match !== undefined) {
              setTypeDialogError(null);
              setDeleteTarget({ id, name: match.name });
            }
          }}
          onBack={() => {
            go({ step: 'project', mode: null });
          }}
        />
      ) : location.mode !== null ? (
        <ConfigStep
          mode={location.mode}
          state={state}
          selection={location.selection}
          project={project}
          capabilities={capabilities.data}
          customTypes={customTypes.data ?? []}
          submitting={submitting}
          error={error}
          dispatch={dispatch}
          onSubmit={submit}
          onBack={() => {
            go({ step: 'mode', mode: null });
          }}
        />
      ) : null}

      {showCodexLogin && (
        <CodexLoginDialog
          busy={login.isPending || create.isPending}
          error={codexLoginError}
          onSubmit={(apiKey) => {
            login.mutate(apiKey);
          }}
          onCancel={() => {
            setShowCodexLogin(false);
            pendingRequest.current = null;
          }}
        />
      )}

      {showAddType && (
        <CreateCustomTypeDialog
          busy={addType.isPending}
          error={typeDialogError}
          onSubmit={(input) => {
            addType.mutate(input);
          }}
          onCancel={() => {
            setShowAddType(false);
          }}
        />
      )}

      {deleteTarget !== null && (
        <DeleteCustomTypeDialog
          name={deleteTarget.name}
          busy={removeType.isPending}
          error={typeDialogError}
          onConfirm={() => {
            removeType.mutate(deleteTarget.id);
          }}
          onCancel={() => {
            setDeleteTarget(null);
          }}
        />
      )}
    </section>
  );
}
