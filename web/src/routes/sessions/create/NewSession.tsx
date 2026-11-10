import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router';
import { fetchAgentCapabilities } from '../../../api/agentCapabilities';
import {
  chatSupport,
  cliModelDetails,
  cliModelsPending,
  gitCapability,
} from '../../../protocol/dto/agentVersion';
import type { ChatStoredSession } from '../../../protocol/dto/chatSessions';
import { startAutoScan } from '../../../api/autoscanRepository';
import { codexLogin } from '../../../api/codexAuthRepository';
import { listWorktreeBases } from '../../../api/gitRepository';
import {
  createCustomAgentType,
  deleteCustomAgentType,
  listCustomAgentTypes,
} from '../../../api/customAgentTypeRepository';
import {
  listProjects,
  reorderProjects,
  type ProjectInfo,
} from '../../../api/projectRepository';
import { createSession, type CreateSessionRequest } from '../../../api/sessionRepository';
import {
  getSessionModeOrder,
  saveSessionModeOrder,
} from '../../../api/sessionModeOrderRepository';
import { ErrorState } from '../../../components/ErrorState';
import { trimTrailingSeparator } from '../../../lib/pathCompletion';
import { queryKeys, wsBackedQueryOptions } from '../../../lib/queryKeys';
import type { SessionResponse } from '../../../protocol/dto/session';
import { customSessionTypeId } from '../../../protocol/wireSchema';
import { useSessionStore } from '../../../auth/sessionStore';
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
import { upsertProject } from '../../../realtime/projectsCache';
import { upsertSession } from '../../../realtime/sessionCache';
import {
  newerSessionModeOrder,
  writeSessionModeOrder,
} from '../../../realtime/sessionModeOrderCache';
import { ProjectFormDialog } from '../../projects/ProjectFormDialog';
import { moveAnnouncement, moveItem, reconcileOrder } from '../../projects/reorder';
import { terminalPath } from '../terminalPath';
import { CodexLoginDialog } from './CodexLoginDialog';
import { ConfigStep, type WorktreeBasesView } from './ConfigStep';
import { CreateCustomTypeDialog, DeleteCustomTypeDialog } from './CustomTypeDialogs';
import { ModeStep } from './ModeStep';
import { ProjectStep } from './ProjectStep';
import { effectiveWorktreeBase } from './worktreeBase';
import {
  buildCreateRequest,
  buildResumeRequest,
  buildStartScanRequest,
  chatAgentKind,
  type StartScanRequest,
} from './buildCreateRequest';
import { ResumeSessionDialog } from './ResumeSessionDialog';
import {
  classifyAutoScanError,
  classifyCreateError,
  codexLoginErrorMessage,
  customTypeErrorMessage,
} from './createErrors';
import { applyLaunchPrefs, launchPrefsFromState } from './launchPrefs';
import { applyModeOrder, buildModeCards } from './modeCatalog';
import {
  INITIAL_WIZARD_STATE,
  isCustomMode,
  validateCustomPath,
  wizardReducer,
  type ProjectSelection,
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

function currentUserId(): string | null {
  return useSessionStore.getState().session?.userId ?? null;
}

function sameOrder(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((item, index) => item === b[index]);
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
  const [showCreateProject, setShowCreateProject] = useState(false);
  const [typeDialogError, setTypeDialogError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; name: string } | null>(null);
  const [resumeOpen, setResumeOpen] = useState(false);
  const [resumeError, setResumeError] = useState<string | null>(null);
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
  // Optimistic orders shown while a drag's save is in flight, and released once
  // the saved order catches up (the `ProjectList` override pattern).
  const [pendingProjectOrder, setPendingProjectOrder] = useState<string[] | null>(null);
  const [pendingModeOrder, setPendingModeOrder] = useState<string[] | null>(null);
  const [reorderAnnouncement, setReorderAnnouncement] = useState('');

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

  // Fetched with the first step, so the mode step already has it; the user
  // channel keeps it fresh after that (`session_mode_order.updated`).
  const modeOrder = useQuery({
    queryKey: queryKeys.sessionModeOrder(),
    // A connect-time refetch can race a save; never let the older answer win.
    // The cache is read after the await, so a save that landed meanwhile counts.
    queryFn: async () => {
      const fetched = await getSessionModeOrder();
      return newerSessionModeOrder(
        queryClient.getQueryData(queryKeys.sessionModeOrder()),
        fetched,
      );
    },
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
    // The cache dies with the visit: `refetchOnMount` alone would serve the
    // PREVIOUS visit's response while the re-probe is in flight, and treating
    // that as authoritative lets a since-downgraded agent's stale
    // `model_details` keep catalog efforts selectable (and reconciled-to)
    // until the refetch lands. With no cross-visit entry, re-entry starts
    // from `undefined` — the same optimistic absent-probe posture as a failed
    // probe, and the same fresh-state-per-visit the mobile wizards get from
    // their per-entry view models. This is the key's only observer, so the
    // zero gcTime leaks into no other screen.
    gcTime: 0,
    // A `models_pending` answer means the agent's startup enumeration was
    // still warming — without a re-probe, `staleTime: Infinity` would pin
    // that provisional shape for the whole visit. Interval refetches ignore
    // staleness, so this polls only until the warmed answer arrives.
    refetchInterval: (query) =>
      cliModelsPending(query.state.data, 'codex') ? 2000 : false,
    ...wsBackedQueryOptions,
  });

  const location = useMemo(() => parseWizardLocation(searchParams), [searchParams]);

  const project = useMemo(() => {
    const selection = location.selection;
    if (selection.kind !== 'stored') return null;
    return projects.data?.find((entry) => entry.name === selection.name) ?? null;
  }, [projects.data, location.selection]);

  // Fetched only while the picker can show: the config step of a launch mode,
  // a stored git project with a worktree requested, and an agent that has the
  // route. Re-read on each visit; `git.changed` invalidates it (see the key).
  const worktreeBaseProject =
    location.step === 'config' &&
    location.mode !== null &&
    location.mode !== 'auto_scan' &&
    project?.is_git === true &&
    state.git.worktree &&
    gitCapability(capabilities.data, 'worktree_base')
      ? project.name
      : null;
  const worktreeBasesQuery = useQuery({
    queryKey: queryKeys.worktreeBases(resolvedAgentId, worktreeBaseProject ?? ''),
    queryFn: () => listWorktreeBases(resolvedAgentId, worktreeBaseProject ?? ''),
    enabled: resolvedAgentId !== '' && worktreeBaseProject !== null,
  });
  // A failed refetch keeps the last good list usable.
  const worktreeBases: WorktreeBasesView = {
    status:
      worktreeBasesQuery.data !== undefined
        ? 'ready'
        : worktreeBasesQuery.isError
          ? 'error'
          : 'loading',
    bases: worktreeBasesQuery.data,
    retry: () => {
      void worktreeBasesQuery.refetch();
    },
  };

  const cards = useMemo(
    () =>
      buildModeCards({
        capabilities: capabilities.data,
        project,
        customTypes: customTypes.data ?? [],
      }),
    [capabilities.data, project, customTypes.data],
  );

  const orderedCards = useMemo(
    () => applyModeOrder(cards, pendingModeOrder ?? modeOrder.data?.order ?? []),
    [cards, pendingModeOrder, modeOrder.data],
  );

  const projectView = reconcileOrder(projects.data ?? [], pendingProjectOrder);
  useEffect(() => {
    if (pendingProjectOrder !== null && projectView.pendingOrder === null) {
      setPendingProjectOrder(null);
    }
  }, [pendingProjectOrder, projectView.pendingOrder]);

  // One save at a time per list, in the order the drops happened: a later drop
  // can never be overtaken by an earlier one's slower round trip.
  // The newest order each list asked for. A queued save that a later move
  // replaced is skipped, so holding an arrow key on a handle costs two writes
  // (the one in flight and the last), not one per row passed.
  const latestModeOrder = useRef<string[] | null>(null);
  // Keyed by agent: the router keeps this one component across a change of
  // `:agentId`, and a save queued for one agent must not be skipped because the
  // user then dropped on another.
  const latestProjectOrder = useRef(new Map<string, string[]>());

  // The save carries the agent it was made on: the options of a pending
  // mutation follow the current render, so reading `resolvedAgentId` here could
  // send one agent's names to another, or cache them under its key.
  const saveProjectOrder = useMutation({
    mutationFn: ({ names, agentId }: { names: string[]; agentId: string }) =>
      latestProjectOrder.current.get(agentId) === names
        ? reorderProjects(agentId, names)
        : Promise.resolve(null),
    // A constant id: TanStack copies the current options into a pending
    // mutation on every render, so an id built from the route would move a
    // queued save into another agent's queue and strand it there.
    scope: { id: 'project-order' },
    onSuccess: (next, { names, agentId }) => {
      if (next === null) return;
      queryClient.setQueryData<ProjectInfo[]>(queryKeys.projects(agentId), next);
      setPendingProjectOrder((current) =>
        current !== null && sameOrder(current, names) ? null : current,
      );
    },
    onError: (_cause, { names }) => {
      setPendingProjectOrder((current) =>
        current !== null && sameOrder(current, names) ? null : current,
      );
      useToastStore.getState().show('Reorder failed — reverted.', 'error');
    },
  });

  const saveModeOrder = useMutation({
    mutationFn: ({ order, userId }: { order: string[]; userId: string | null }) =>
      // Also skipped once the account changed: the queued save would otherwise
      // write this account's order with the next account's token.
      latestModeOrder.current === order && currentUserId() === userId
        ? saveSessionModeOrder(order)
        : Promise.resolve(null),
    scope: { id: 'session-mode-order' },
    onSuccess: (saved, { order, userId }) => {
      if (saved === null || currentUserId() !== userId) return;
      writeSessionModeOrder(queryClient, saved);
      setPendingModeOrder((current) =>
        current !== null && sameOrder(current, order) ? null : current,
      );
    },
    onError: (_cause, { order }) => {
      setPendingModeOrder((current) =>
        current !== null && sameOrder(current, order) ? null : current,
      );
      useToastStore.getState().show('Reorder failed — reverted.', 'error');
    },
  });

  const moveProject = (from: number, to: number) => {
    const names = projectView.projects.map((entry) => entry.name);
    const moved = names[from];
    if (moved === undefined) return;
    const next = moveItem(names, from, to);
    setPendingProjectOrder(next);
    setReorderAnnouncement(moveAnnouncement(moved, to, names.length));
    latestProjectOrder.current.set(resolvedAgentId, next);
    saveProjectOrder.mutate({ names: next, agentId: resolvedAgentId });
  };

  const moveModeCard = (from: number, to: number) => {
    const moved = orderedCards[from];
    if (moved === undefined) return;
    const next = moveItem(
      orderedCards.map((card) => card.mode),
      from,
      to,
    );
    setPendingModeOrder(next);
    setReorderAnnouncement(moveAnnouncement(moved.title, to, orderedCards.length));
    latestModeOrder.current = next;
    saveModeOrder.mutate({ order: next, userId: currentUserId() });
  };

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

  /**
   * Leave the project step.
   *
   * One **push**, never a replace of this entry followed by a push of the next:
   * two `setSearchParams` in one handler race the data router, which aborts the
   * in-flight navigation when the second arrives — and the dropped one would be
   * the replace, silently degrading to this anyway. `MemoryRouter` is
   * declarative and synchronous, so the tests would not show it either.
   *
   * The cost is that browser Back returns to the project step as it was
   * *before* the click, which can name a different project than the one just
   * picked. The wizard's own Back carries the selection (`go` preserves it), so
   * the row comes back highlighted and a picked directory comes back in the
   * field.
   */
  const advanceToMode = useCallback(
    (selection: ProjectSelection) => {
      setSearchParams(serializeWizardLocation({ step: 'mode', mode: null, selection }));
    },
    [setSearchParams],
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
    //
    // Picking a recent directory dispatches the draft *and* navigates, so this
    // re-dispatches the value the click already set. Idempotent, one extra
    // render, and not a reason to drop either half: the click's dispatch is
    // what keeps a directory that fails validation on screen, where no
    // navigation happens at all.
  }, [urlPath]);

  // Once the probe resolves, reconcile a restored Codex effort against what
  // this agent can accept: an agent advertising no `model_details` may still
  // 422 catalog efforts (pre-catalog wire schema), so the reducer drops an
  // out-of-vocabulary value back to the default. Keyed on the probe result —
  // a failed probe (undefined) reconciles nothing, same optimistic posture as
  // the availability flags. A `models_pending` response reconciles nothing
  // either: the agent's enumeration is still warming, so the absent details
  // are provisional — and the marker itself proves a catalog-era agent, so
  // keeping the persisted effort is safe, not just optimistic. The
  // refetchInterval below re-probes until the warmup answer arrives.
  const codexDetails = useMemo(() => {
    if (capabilities.data === undefined) return undefined;
    if (cliModelsPending(capabilities.data, 'codex')) return undefined;
    return cliModelDetails(capabilities.data, 'codex');
  }, [capabilities.data]);
  useEffect(() => {
    if (codexDetails === undefined) return;
    dispatch({ type: 'reconcileCodexEffort', details: codexDetails });
  }, [codexDetails]);

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
  // selection. It only *highlights*: writing `step: 'project'` explicitly
  // rather than going through `advanceToMode` is what keeps a remembered
  // project from launching the wizard past a screen the user never saw, now
  // that picking one advances. One shot per mount, and the latch arms only
  // once an empty project step has actually been reached: the clamp effect
  // above runs first and may still be redirecting a stale deep link, so arming
  // on the list's arrival alone would spend the one attempt on a step that is
  // about to be redirected away. A
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
        // A Codex resume, too: the sign-in re-sends that same request.
        pendingRequest.current = request;
        setResumeOpen(false);
        setCodexLoginError(null);
        setShowCodexLogin(true);
        return;
      }
      // A resume failure belongs to the picker, which is still open over the
      // config step and would hide a message written there.
      if (request.chat_resume_id !== undefined && request.chat_resume_id !== null) {
        setResumeError(failure.message);
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
    // The picker's shown value is what goes out; `null` (agent default) while
    // the list is loading or failed, or when the picker is hidden.
    const worktreeBase =
      worktreeBaseProject === null
        ? null
        : effectiveWorktreeBase(state.worktreeBasePick, worktreeBaseProject, worktreeBases.bases);
    const context = {
      state: { ...state, selection: location.selection },
      mode: location.mode,
      project,
      chat: chatSupport(capabilities.data, chatAgentKind(location.mode) ?? 'claude'),
      worktreeBase,
    };
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

  const resume = (stored: ChatStoredSession) => {
    setResumeError(null);
    const open = stored.open_session_id;
    if (open !== undefined) {
      setResumeOpen(false);
      try {
        setLastUsedSession(resolvedAgentId, open);
      } catch {
        // Storage refused; auto-resume simply will not return here.
      }
      void navigate(terminalPath(resolvedAgentId, open));
      return;
    }
    const kind = chatAgentKind(location.mode);
    if (location.mode === null || kind === null) return;
    const context = {
      state: { ...state, selection: location.selection },
      mode: location.mode,
      project,
    };
    pendingPrefs.current = launchPrefsFromState(state, kind);
    create.mutate(buildResumeRequest(context, kind, stored));
  };
  const resumeKind = chatAgentKind(location.mode);
  const resumeChat = chatSupport(capabilities.data, resumeKind ?? 'claude');

  const resumeFolder =
    location.selection.kind === 'stored'
      ? (project?.path ?? null)
      : location.selection.kind === 'customPath'
        ? location.selection.path
        : null;
  const resuming =
    create.isPending && typeof create.variables.chat_resume_id === 'string'
      ? create.variables.chat_resume_id
      : null;

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
          agentId={resolvedAgentId}
          projects={projectView.projects}
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
          onSelectProject={(project) => {
            // The reducer still runs, for what the URL does not carry: it
            // clears a half-typed path and drops the worktree bucket for a
            // project that is not a repo. `syncCustomPathFromUrl` cannot do the
            // first — its key stays `null` across this navigation, so it never
            // fires.
            dispatch({ type: 'selectStoredProject', project });
            advanceToMode({ kind: 'stored', name: project.name });
          }}
          onSelectRecentPath={(path) => {
            // Fill the field first, so a directory that cannot be launched
            // stays on screen under its own error instead of flashing the next
            // step and being bounced back by the clamp. Only a hand-edited
            // storage blob can hold one: `noteRecentPath` is fed cwds the
            // launch already validated.
            dispatch({ type: 'setCustomPathDraft', draft: path });
            const trimmed = path.trim();
            if (validateCustomPath(trimmed) !== null) return;
            advanceToMode({ kind: 'customPath', path: trimmed });
          }}
          onCreateProject={() => {
            setShowCreateProject(true);
          }}
          onMoveProject={moveProject}
          // The custom-path field writes the reducer only. Per keystroke it
          // would be a router navigation and a `history.replaceState` per
          // character, which browsers rate-limit (Safari throws past 100 in
          // 30s); the draft reaches the URL once, on the act that leaves the
          // step.
          dispatch={dispatch}
          onContinue={() => {
            // Reachable only for a path typed by hand — a project row and a
            // recent both advance on their own click. The child's `disabled` is
            // an affordance, not an invariant this container may lean on, and
            // `validateCustomPath` refuses the empty draft under the same call.
            const draft = trimTrailingSeparator(state.customPathDraft.trim());
            if (validateCustomPath(draft) !== null) return;
            advanceToMode({ kind: 'customPath', path: draft });
          }}
        />
      ) : location.step === 'mode' ? (
        <ModeStep
          cards={orderedCards}
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
          onMoveCard={moveModeCard}
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
          worktreeBases={worktreeBases}
          submitting={submitting}
          error={error}
          dispatch={dispatch}
          onSubmit={submit}
          onBack={() => {
            go({ step: 'mode', mode: null });
          }}
          onContinueSession={() => {
            setResumeError(null);
            setResumeOpen(true);
          }}
        />
      ) : null}

      {resumeOpen && resumeKind !== null && (
        <ResumeSessionDialog
          agentId={resolvedAgentId}
          kind={resumeKind}
          live={resumeChat.kind === 'available' && resumeChat.live}
          folder={resumeFolder}
          opening={resuming}
          error={resumeError}
          onPick={resume}
          onClose={() => {
            setResumeOpen(false);
          }}
        />
      )}

      {/* Outside the steps, so a re-render of the rows cannot drop the region. */}
      <p className="visually-hidden" role="status" aria-live="polite">
        {reorderAnnouncement}
      </p>

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

      {showCreateProject && (
        <ProjectFormDialog
          agentId={resolvedAgentId}
          editing={null}
          onCreated={(created) => {
            // Seed before advancing: the clamp effect sends a `?project=` the
            // list does not hold back to step one, and neither the dialog's
            // refetch nor the `projects.changed` echo is guaranteed to have
            // landed. Idempotently replaced by whichever arrives next, as the
            // session seed in `create.onSuccess` is.
            queryClient.setQueryData<ProjectInfo[]>(
              queryKeys.projects(resolvedAgentId),
              (current) => upsertProject(current ?? [], created),
            );
            dispatch({ type: 'selectStoredProject', project: created });
            advanceToMode({ kind: 'stored', name: created.name });
          }}
          onClose={() => {
            setShowCreateProject(false);
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
