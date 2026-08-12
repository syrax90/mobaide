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
import { ErrorState } from '../../../components/ErrorState';
import { queryKeys, wsBackedQueryOptions } from '../../../lib/queryKeys';
import type { SessionResponse } from '../../../protocol/dto/session';
import { customSessionTypeId } from '../../../protocol/wireSchema';
import { useLastSessionStore } from '../../../stores/lastSessionStore';
import {
  readLaunchPrefs,
  useLaunchPrefsStore,
  type LaunchPrefsSnapshot,
} from '../../../stores/launchPrefsStore';
import {
  forgetRecentPath,
  noteRecentPath,
  readRecentPaths,
} from '../../../stores/recentPaths';
import { useToastStore } from '../../../stores/toastStore';
import { useAgentEvents } from '../../../realtime';
import { upsertSession } from '../../../realtime/sessionCache';
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
import { applyLaunchPrefs, launchPrefsFromState } from './launchPrefs';
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

/**
 * File a completed launch as the starting point for the next one.
 *
 * `snapshot` is `null` for the modes that carry no options — Terminal and
 * custom agent types — which is the same set that sends no options block on the
 * wire. `projectName` is absent for an ad-hoc directory; the generated request
 * type allows an explicit `null` there too, so both stand for "no project".
 *
 * Never allowed to throw. `persist` calls `localStorage.setItem` on every `set`
 * with no guard of its own, and this runs inside a mutation's `onSuccess`,
 * which TanStack awaits inside a try that routes any throw to `onError` — so a
 * refused write would report a session the agent has already started as a
 * failed launch, and skip the cache seed and navigation queued behind it. Same
 * rule, and the same set of causes, as `recentPaths.writePaths`.
 */
function persistLaunchPrefs(
  snapshot: LaunchPrefsSnapshot | null,
  projectName: string | null | undefined,
): void {
  // One guard per write, not one around both. `set` lands the in-memory half
  // before `persist` throws, so a shared block would leave the options updated
  // and the project name never written at all — and the next wizard in this
  // same tab would then pre-select the wrong project off the stale value. Each
  // write costs only its own durability.
  try {
    if (snapshot !== null) useLaunchPrefsStore.getState().setLaunchPrefs(snapshot);
  } catch {
    // Storage refused — a partitioned context, a full quota, private mode. A
    // forgotten preference is a cosmetic loss; failing the launch is not.
  }
  try {
    if (typeof projectName === 'string' && projectName !== '') {
      useLaunchPrefsStore.getState().setLastProjectName(projectName);
    }
  } catch {
    // As above.
  }
}

export function NewSession() {
  const { agentId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  // Seeded from two places the reducer itself cannot see. The **URL**, because
  // the reducer does not survive a remount while the search params do: without
  // this a reload at `?path=/foo` would render an empty path field with
  // Continue disabled, and the user would have to retype a path the URL is
  // still carrying. And the **last successful launch of each kind**, which is
  // the whole of the prefill feature — `applyLaunchPrefs` leaves a bucket that
  // was never launched exactly as `INITIAL_WIZARD_STATE` had it, so Auto Scan
  // keeps its `skipPermissions: true` on a fresh profile. Read here rather than
  // in an effect because `persist` over `localStorage` hydrates synchronously
  // during store creation, so the first render already has the stored values;
  // and unlike `recentPaths` below there is nothing to re-read when `:agentId`
  // changes under the one component instance the router keeps, because the
  // buckets are global.
  const [state, dispatch] = useReducer(wizardReducer, searchParams, (params) => {
    const base = applyLaunchPrefs(INITIAL_WIZARD_STATE, readLaunchPrefs());
    const { selection } = parseWizardLocation(params);
    if (selection.kind !== 'customPath') return base;
    return {
      ...base,
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
  // The options the in-flight launch was built from, pinned by `submit` before
  // the round trip. The config step's controls are *not* disabled while
  // submitting (only Back, Create and Cancel are), so reading live state in
  // `onSuccess` would remember a toggle the launch never carried. Deliberately
  // not cleared on error: the Codex-login retry re-sends `pendingRequest`
  // verbatim, and the prefs it should file are the original submit's.
  const pendingPrefs = useRef<LaunchPrefsSnapshot | null>(null);
  const setLastUsedSession = useLastSessionStore((state) => state.setLastUsedSession);

  const resolvedAgentId = agentId ?? '';

  // Read through an effect rather than once at mount: the router keeps this one
  // component instance across a change of `:agentId`, so a lazy initializer
  // would leave the previous agent's directories on screen.
  const [recentPaths, setRecentPaths] = useState<string[]>([]);
  useEffect(() => {
    setRecentPaths(readRecentPaths(resolvedAgentId));
  }, [resolvedAgentId]);

  // The wizard keeps the agent channel open for its own visit: without it
  // `projects.changed` is missed for the whole time it is mounted (and a
  // deep-linked wizard has no channel at all), and the socket would be torn
  // down and re-handshaked on every list <-> wizard transition.
  useAgentEvents(resolvedAgentId);

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
    // Re-probed on every wizard entry, so installing a CLI between visits
    // un-greys its card. Per *mount*, not per step: a CLI installed during a
    // single visit is not picked up until the wizard is re-entered, which the
    // disabled card's explicit reason makes survivable.
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

  // The URL owns the selection, so a history navigation that changes it has to
  // reach the draft too — otherwise Back can land on a project step whose URL
  // says `path=/foo` while the field is blank and Continue is disabled.
  const urlPath =
    location.selection.kind === 'customPath' ? location.selection.path : null;
  useEffect(() => {
    dispatch({ type: 'syncCustomPathFromUrl', path: urlPath });
    // Keyed on `urlPath` alone, deliberately. Typing never writes the URL, so
    // this fires only when a navigation actually changed the selection — which
    // is why clearing on `null` cannot wipe what the user is typing, and why
    // adding `customPathDraft` to the deps would make it do exactly that.
  }, [urlPath]);

  // Seeded once per project, so re-entering the step never discards edits.
  useEffect(() => {
    if (location.mode === 'auto_scan' && project !== null) {
      dispatch({
        type: 'seedAutoScanPrompt',
        projectName: project.name,
        prompt: project.auto_scan_prompt ?? '',
      });
    }
  }, [location.mode, project]);

  // Pre-select the project the last session was launched against — iOS's
  // `applyProjectPrefill`, translated to the model where the URL owns the
  // selection. One shot per mount, and the latch arms only once an empty
  // project step has actually been reached: the clamp effect above runs first
  // and may still be redirecting a stale deep link, so arming on the list's
  // arrival alone would spend the one attempt on a step the user never saw. A
  // name the agent no longer has is silently skipped, exactly as on mobile, and
  // `replace` keeps the pre-fill out of history so Back still leaves the wizard.
  // Keyed by agent rather than a bare flag, for the reason `recentPaths` reads
  // through an effect: the router keeps this one component instance across a
  // change of `:agentId`, so a boolean latch would leave the second agent
  // without a pre-fill for the rest of the visit.
  const projectPrefilledFor = useRef<string | null>(null);
  useEffect(() => {
    if (projectPrefilledFor.current === resolvedAgentId || projects.data === undefined) return;
    if (location.step !== 'project' || location.selection.kind !== 'none') return;
    projectPrefilledFor.current = resolvedAgentId;
    const name = readLaunchPrefs().lastProjectName;
    if (name === null) return;
    if (!projects.data.some((entry) => entry.name === name)) return;
    setSearchParams(
      serializeWizardLocation({
        step: 'project',
        mode: null,
        selection: { kind: 'stored', name },
      }),
      { replace: true },
    );
  }, [projects.data, location.step, location.selection.kind, resolvedAgentId, setSearchParams]);

  const create = useMutation({
    mutationFn: (request: CreateSessionRequest) => createSession(resolvedAgentId, request),
    onSuccess: (session, request) => {
      setShowCodexLogin(false);
      pendingRequest.current = null;
      // Guarded for the reason spelled out on `persistLaunchPrefs`: this is a
      // `persist`-backed store too, so a refused write here would abort the
      // rest of this handler and report the launch as failed.
      try {
        setLastUsedSession(resolvedAgentId, session.session_id);
      } catch {
        // Storage refused; auto-resume simply will not return here.
      }
      // Recorded off the request the mutation actually sent, never off live
      // state: the wizard stays interactive while the POST is in flight, so a
      // path typed meanwhile would otherwise be filed as a directory nobody
      // launched (iOS pins both halves before the round trip). `cwd` is
      // undefined for a stored project, which is what keeps those out of the
      // bucket; the re-validation covers a `?path=` deep link.
      if (typeof request.cwd === 'string' && validateCustomPath(request.cwd) === null) {
        noteRecentPath(resolvedAgentId, request.cwd);
      }
      // Remember how this kind was launched, for the next wizard. The values
      // are the pinned ones; the *project* comes off the request, which is
      // present only for a stored selection — an ad-hoc directory stays a
      // recent and never becomes the last-used project, as on both mobile
      // clients. Terminal and custom types pin `null` and so file nothing.
      persistLaunchPrefs(pendingPrefs.current, request.project_name);
      // Seed the row synchronously, before navigating. The terminal treats
      // "absent from a synced list" as proof the session is gone and leaves, so
      // the gap between this create and the reconciling snapshot below would
      // otherwise bounce the user straight back out of the session they just
      // made. Idempotently replaced by whichever of the two arrives next.
      queryClient.setQueryData<SessionResponse[]>(
        queryKeys.sessions(resolvedAgentId),
        (current) => upsertSession(current ?? [], session),
      );
      // The agent's `sessions.changed` normally carries the new row; this covers
      // the socket being down, where REST still works and the list would
      // otherwise be missing the session the user is now sitting in. Same
      // fallback the stop mutation carries.
      void queryClient.invalidateQueries({ queryKey: queryKeys.sessions(resolvedAgentId) });
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
      // Close the login dialog before reporting anything else. It is a native
      // `<dialog>` on the top layer, so the page behind it is inert — a message
      // written to the config step would be invisible, and the dialog would sit
      // there re-enabled with no feedback, inviting the user to re-submit a key
      // that was never the problem (session cap, agent update required, project
      // deleted between login and launch).
      setShowCodexLogin(false);
      pendingRequest.current = null;
      setError(failure.message);
    },
  });

  const scan = useMutation({
    mutationFn: (request: StartScanRequest) => startAutoScan(resolvedAgentId, request),
    onSuccess: (_result, request) => {
      persistLaunchPrefs(pendingPrefs.current, request.project_name);
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
    // Pinned in the same synchronous pass that builds the request, so the two
    // are provably the same snapshot of the options.
    pendingPrefs.current = launchPrefsFromState(state, location.mode);
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
        {/*
          Gated like ConfigStep's Back and Create: a mutation's `onSuccess` runs
          even after this unmounts, so leaving mid-launch would yank the user out
          of the list and into a session they thought they had cancelled.
        */}
        <Link
          to="/"
          className="button"
          aria-disabled={submitting}
          onClick={(event) => {
            if (submitting) event.preventDefault();
          }}
        >
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
        <p className="page-note state-loading">Loading projects…</p>
      ) : projects.isError ? (
        <ErrorState
          message="Failed to load projects."
          onRetry={() => void projects.refetch()}
        />
      ) : location.step === 'project' ? (
        <ProjectStep
          projects={projects.data}
          selection={location.selection}
          customPathDraft={state.customPathDraft}
          customPathError={state.customPathError}
          recentPaths={recentPaths}
          onForgetRecent={(path) => {
            forgetRecentPath(resolvedAgentId, path);
            // Re-read rather than filter locally, so what the list shows is
            // always what storage holds (iOS `refreshRecentCustomPaths`).
            setRecentPaths(readRecentPaths(resolvedAgentId));
          }}
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
