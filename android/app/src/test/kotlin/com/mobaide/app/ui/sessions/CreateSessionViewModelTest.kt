package com.mobaide.app.ui.sessions

import app.cash.turbine.test
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.local.AntigravityLaunchPrefs
import com.mobaide.app.data.local.ClaudeLaunchPrefs
import com.mobaide.app.data.local.CodexLaunchPrefs
import com.mobaide.app.data.local.OpenCodeLaunchPrefs
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.dto.ChatStoredSession
import com.mobaide.app.data.network.dto.ClaudeOptionsModel
import com.mobaide.app.data.network.dto.CliModelDetail
import com.mobaide.app.data.network.dto.CodexLoginResponse
import com.mobaide.app.data.network.dto.CreateSessionRequest
import com.mobaide.app.data.network.dto.CustomAgentTypeDto
import com.mobaide.app.data.network.dto.FsDirsResponse
import com.mobaide.app.data.network.dto.ProjectInfo
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.data.network.dto.VersionInfoResponse
import com.mobaide.app.data.network.dto.WorktreeBaseRef
import com.mobaide.app.data.network.dto.WorktreeBasesResponse
import com.mobaide.app.data.repository.AutoScanRepository
import com.mobaide.app.data.repository.CustomAgentTypesRepository
import com.mobaide.app.data.repository.GitRepository
import com.mobaide.app.data.repository.PathSuggestionRepository
import com.mobaide.app.data.repository.ProjectsRepository
import com.mobaide.app.data.repository.SessionRepository
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.Runs
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/**
 * Unit tests for [CreateSessionViewModel] — exercises the wizard step flow
 * (ProjectSelect -> ModePick -> StandardConfig / AutoScanConfig), worktree
 * clamping when the selected project is non-git, the `submit()` ->
 * [CreateSessionRequest] payload shape, the AutoScan preset defaults, the
 * navigation SharedFlow emissions on success, and the error state on
 * repository failure.
 *
 * [MainDispatcherRule] installs a StandardTestDispatcher so that the two
 * `viewModelScope.launch { ... }` blocks in `init { }` and the `submit*`
 * paths only run when the test yields via `advanceUntilIdle()`.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class CreateSessionViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    // ---- Helpers ------------------------------------------------------------

    private fun project(
        name: String,
        isGit: Boolean = true,
    ): ProjectInfo = ProjectInfo(name = name, path = "/p/$name", is_git = isGit)

    private class Fixture(
        val projectsRepo: ProjectsRepository,
        val sessionRepo: SessionRepository,
        val autoScanRepo: AutoScanRepository,
        val prefs: PreferencesStore,
        val tokenStore: TokenStore,
        val projectsFlow: MutableStateFlow<List<ProjectInfo>>,
        val customTypesRepo: CustomAgentTypesRepository,
        val customTypesFlow: MutableStateFlow<List<CustomAgentTypeDto>>,
        val restApi: RestApi,
        val gitRepo: GitRepository,
        val gitChanged: MutableSharedFlow<String>,
    )

    private fun newFixture(
        initialProjects: List<ProjectInfo> = listOf(
            project("alpha", isGit = true),
            project("beta", isGit = false),
        ),
        lastUsedProject: String? = null,
        autoScanPrompt: String? = "default auto scan prompt",
        agentId: String? = "agent-1",
        agentUrl: String? = null,
        recentCustomPaths: List<String> = emptyList(),
    ): Fixture {
        val projectsRepo = mockk<ProjectsRepository>(relaxed = true)
        val sessionRepo = mockk<SessionRepository>(relaxed = true)
        val autoScanRepo = mockk<AutoScanRepository>(relaxed = true)
        val prefs = mockk<PreferencesStore>(relaxed = true)
        val tokenStore = mockk<TokenStore>(relaxed = true)
        val projectsFlow = MutableStateFlow(initialProjects)

        every { projectsRepo.observeProjects() } returns projectsFlow
        coEvery { projectsRepo.listProjects() } returns initialProjects
        coEvery { prefs.getLastUsedProjectName() } returns lastUsedProject
        coEvery { prefs.setLastUsedProjectName(any()) } just Runs
        coEvery { prefs.setLastUsedSessionId(any()) } just Runs
        coEvery { prefs.getLastUsedClaudeOptions() } returns null
        coEvery { prefs.setLastUsedClaudeOptions(any()) } just Runs
        coEvery { prefs.getLastUsedAutoScanOptions() } returns null
        coEvery { prefs.setLastUsedAutoScanOptions(any()) } just Runs
        coEvery { prefs.getLastUsedCodexOptions() } returns null
        coEvery { prefs.setLastUsedCodexOptions(any()) } just Runs
        coEvery { prefs.getLastUsedAntigravityOptions() } returns null
        coEvery { prefs.setLastUsedAntigravityOptions(any()) } just Runs
        coEvery { prefs.getLastUsedOpenCodeOptions() } returns null
        coEvery { prefs.setLastUsedOpenCodeOptions(any()) } just Runs
        coEvery { prefs.getRecentCustomPaths(any()) } returns recentCustomPaths
        coEvery { prefs.addRecentCustomPath(any(), any()) } just Runs
        coEvery { prefs.removeRecentCustomPath(any(), any()) } just Runs
        every { autoScanRepo.getAutoScanPrompt() } returns autoScanPrompt
        every { tokenStore.getAgentId() } returns agentId
        every { tokenStore.getAgentUrl() } returns agentUrl

        // Strict mock on purpose: the init block must only observe + refresh;
        // CRUD calls are stubbed per-test.
        val customTypesFlow = MutableStateFlow<List<CustomAgentTypeDto>>(emptyList())
        val customTypesRepo = mockk<CustomAgentTypesRepository>()
        every { customTypesRepo.observeTypes() } returns customTypesFlow
        every { customTypesRepo.observeModeOrder() } returns MutableStateFlow(emptyList())
        coEvery { customTypesRepo.refreshTypes() } just Runs
        coEvery { customTypesRepo.refreshModeOrder() } just Runs

        // Strict too: the custom-path folder suggestions reach the agent only
        // once their field gains focus.
        val restApi = mockk<RestApi>()

        val gitRepo = mockk<GitRepository>()
        val gitChanged = MutableSharedFlow<String>()
        every { gitRepo.changed } returns gitChanged

        return Fixture(
            projectsRepo, sessionRepo, autoScanRepo, prefs, tokenStore, projectsFlow,
            customTypesRepo, customTypesFlow, restApi, gitRepo, gitChanged,
        )
    }

    private fun Fixture.buildVm(): CreateSessionViewModel =
        CreateSessionViewModel(
            projectsRepo, sessionRepo, autoScanRepo, prefs, tokenStore, customTypesRepo,
            PathSuggestionRepository(restApi, tokenStore),
            gitRepo,
        )

    // ---- Wizard step flow --------------------------------------------------

    @Test
    fun wizardFlow_ProjectSelect_to_ModePick_to_StandardConfig() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        assertEquals(CreateSessionStep.ProjectSelect, vm.uiState.value.step)

        vm.selectProject("alpha")
        vm.confirmProject()
        assertEquals(CreateSessionStep.ModePick, vm.uiState.value.step)
        assertEquals("alpha", vm.uiState.value.selectedProjectName)

        vm.selectStandardMode()
        assertEquals(CreateSessionStep.StandardConfig, vm.uiState.value.step)
    }

    @Test
    fun goBack_fromStandardConfig_returnsToModePick_andFromModePickClearsSelectedMode() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.pickMode(CreateSessionMode.Standard)
        vm.selectStandardMode()

        vm.goBack()
        assertEquals(CreateSessionStep.ModePick, vm.uiState.value.step)

        vm.goBack()
        assertEquals(CreateSessionStep.ProjectSelect, vm.uiState.value.step)
        assertNull(vm.uiState.value.selectedMode)
    }

    // ---- Worktree clamping on non-git project ------------------------------

    @Test
    fun selectProject_nonGit_clampsWorktreeOff_andClearsBranchName() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        // User toggled worktree on + set a branch name while a git project
        // was implicitly active...
        vm.toggleWorktree(true)
        vm.setBranchName("feature/x")
        assertTrue(vm.uiState.value.worktree)
        assertEquals("feature/x", vm.uiState.value.branchName)

        // ...then picks a non-git project. The VM must clamp both off.
        vm.selectProject("beta")
        assertEquals("beta", vm.uiState.value.selectedProjectName)
        assertFalse(vm.uiState.value.worktree)
        assertEquals("", vm.uiState.value.branchName)
    }

    // ---- Standard submit path ----------------------------------------------

    @Test
    fun submit_happyPath_sendsCorrectPayload_andEmitsNavigateToSession() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val requestSlot = slot<CreateSessionRequest>()
        coEvery { fx.sessionRepo.createSession(capture(requestSlot)) } returns
            SessionResponse(
                session_id = "sess-123",
                state = "starting",
                created_at = "2026-04-11T00:00:00Z",
            )

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectStandardMode()
        vm.toggleNoMemory(true)
        vm.toggleWorktree(true)
        vm.setBranchName("feature/x")

        vm.navigateToSession.test {
            vm.submit()
            advanceUntilIdle()

            assertEquals("sess-123", awaitItem())
            cancelAndIgnoreRemainingEvents()
        }

        val req = requestSlot.captured
        assertEquals("alpha", req.project_name)
        val opts = req.claude_options
        assertNotNull(opts)
        assertTrue(opts!!.no_memory)
        assertFalse(opts.skip_permissions)
        assertEquals("effort defaults to high", "high", opts.effort)
        assertNull("model defaults to the account default (null)", opts.model)
        assertFalse(opts.fast_mode)
        assertFalse(opts.ultracode)
        assertNull(opts.append_system_prompt)
        assertTrue(opts.worktree)
        assertEquals("feature/x", opts.branch_name)
        coVerify(exactly = 1) { fx.prefs.setLastUsedProjectName("alpha") }
        coVerify(exactly = 1) { fx.prefs.setLastUsedClaudeOptions(any()) }
        coVerify(exactly = 1) { fx.prefs.setLastUsedSessionId("sess-123") }
        assertFalse(vm.uiState.value.submitting)
    }

    @Test
    fun submit_withClaudeReasoningOptions_sendsThem_andPersistsLastUsed() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val requestSlot = slot<CreateSessionRequest>()
        coEvery { fx.sessionRepo.createSession(capture(requestSlot)) } returns
            SessionResponse("sess-m", "starting", "2026-04-21T00:00:00Z")
        val prefsSlot = slot<ClaudeLaunchPrefs>()
        coEvery { fx.prefs.setLastUsedClaudeOptions(capture(prefsSlot)) } just Runs

        vm.selectProject("alpha")
        vm.setClaudeModel("opus")
        vm.setClaudeEffort("max")
        vm.setClaudeAppendSystemPrompt("be terse")
        vm.toggleClaudeUltracode(true)
        vm.toggleSkipPermissions(true)
        vm.toggleNoMemory(true)
        vm.submit()
        advanceUntilIdle()

        val opts = requestSlot.captured.claude_options
        assertNotNull(opts)
        assertEquals("opus", opts!!.model)
        assertEquals("max", opts.effort)
        assertTrue(opts.ultracode)
        assertEquals("be terse", opts.append_system_prompt)
        // The same selections are persisted for the next launch — including
        // the skip-permissions / no-memory toggles that used to reset.
        assertEquals("opus", prefsSlot.captured.model)
        assertEquals("max", prefsSlot.captured.effort)
        assertTrue(prefsSlot.captured.ultracode)
        assertEquals("be terse", prefsSlot.captured.appendSystemPrompt)
        assertTrue(prefsSlot.captured.skipPermissions)
        assertTrue(prefsSlot.captured.noMemory)
    }

    @Test
    fun submit_claudeDebug_defaultsOff_andReachesRequestAndPrefsWhenToggledOn() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val requestSlot = slot<CreateSessionRequest>()
        coEvery { fx.sessionRepo.createSession(capture(requestSlot)) } returns
            SessionResponse("sess-dbg", "starting", "2026-08-11T00:00:00Z")
        val prefsSlot = slot<ClaudeLaunchPrefs>()
        coEvery { fx.prefs.setLastUsedClaudeOptions(capture(prefsSlot)) } just Runs

        vm.selectProject("alpha")
        vm.submit()
        advanceUntilIdle()

        assertFalse(
            "Claude Code's --debug is opt-in now — an untouched wizard must not ask for it",
            requestSlot.captured.claude_options!!.debug,
        )
        assertFalse(prefsSlot.captured.debug)

        vm.toggleClaudeDebug(true)
        vm.submit()
        advanceUntilIdle()

        assertTrue(requestSlot.captured.claude_options!!.debug)
        assertTrue("the choice is remembered for the next Claude launch", prefsSlot.captured.debug)
    }

    @Test
    fun submit_focusMode_defaultsOn_andReachesRequestAndPrefsWhenToggledOff() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val requestSlot = slot<CreateSessionRequest>()
        coEvery { fx.sessionRepo.createSession(capture(requestSlot)) } returns
            SessionResponse("sess-focus", "starting", "2026-10-19T00:00:00Z")
        val prefsSlot = slot<ClaudeLaunchPrefs>()
        coEvery { fx.prefs.setLastUsedClaudeOptions(capture(prefsSlot)) } just Runs

        vm.selectProject("alpha")
        vm.submit()
        advanceUntilIdle()

        assertEquals(true, requestSlot.captured.claude_options!!.focus_mode)
        assertTrue(prefsSlot.captured.focusMode)

        vm.toggleFocusMode(false)
        vm.submit()
        advanceUntilIdle()

        assertEquals(
            "off is sent explicitly, never as an absent key",
            false,
            requestSlot.captured.claude_options!!.focus_mode,
        )
        assertFalse(prefsSlot.captured.focusMode)
    }

    @Test
    fun toggleClaudeFastMode_locksModelToOpus() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.setClaudeModel("sonnet")
        vm.toggleClaudeFastMode(true)

        assertEquals("opus", vm.uiState.value.claudeModel)
        assertTrue(vm.uiState.value.claudeFastMode)
    }

    @Test
    fun setClaudeModel_toNonXhighModel_clampsEffortToHigh() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.setClaudeModel("opus")
        vm.setClaudeEffort("max")
        // haiku doesn't support xhigh/max, so the invalid level must clamp.
        vm.setClaudeModel("haiku")

        assertEquals("high", vm.uiState.value.claudeEffort)
    }

    @Test
    fun init_seedsClaudeControlsFromPersistedPrefs() = runTest {
        val fx = newFixture()
        coEvery { fx.prefs.getLastUsedClaudeOptions() } returns
            ClaudeLaunchPrefs(
                model = "fable",
                effort = "xhigh",
                ultracode = true,
                skipPermissions = true,
                noMemory = true,
            )
        val vm = fx.buildVm()
        advanceUntilIdle()

        val s = vm.uiState.value
        assertEquals("fable", s.claudeModel)
        assertEquals("xhigh", s.claudeEffort)
        assertTrue(s.claudeUltracode)
        assertTrue(s.skipPermissions)
        assertTrue(s.noMemory)
    }

    @Test
    fun init_seedsAutoScanControlsFromPersistedPrefs() = runTest {
        // The Auto Scan bucket seeds only the autoScan* fields — the Standard
        // Claude controls keep their own defaults (per-kind isolation).
        val fx = newFixture()
        coEvery { fx.prefs.getLastUsedAutoScanOptions() } returns
            ClaudeLaunchPrefs(
                model = "sonnet",
                effort = "low",
                appendSystemPrompt = "scan sys",
                skipPermissions = false,
                noMemory = true,
                planMode = true,
            )
        val vm = fx.buildVm()
        advanceUntilIdle()

        val s = vm.uiState.value
        assertEquals("sonnet", s.autoScanClaudeModel)
        assertEquals("low", s.autoScanClaudeEffort)
        assertEquals("scan sys", s.autoScanClaudeAppendSystemPrompt)
        assertFalse("a saved OFF choice must override the ON default", s.autoScanSkipPermissions)
        assertTrue(s.autoScanNoMemory)
        assertTrue(s.autoScanPlanMode)
        // No bleed into the Standard Claude controls.
        assertNull(s.claudeModel)
        assertEquals("high", s.claudeEffort)
        assertFalse(s.skipPermissions)
        assertFalse(s.noMemory)
    }

    @Test
    fun init_noSavedAutoScanPrefs_skipPermissionsDefaultsOn() = runTest {
        // Unattended scans default to bypass; the compile-time state default
        // applies when nothing was ever persisted for the Auto Scan bucket.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val s = vm.uiState.value
        assertTrue(s.autoScanSkipPermissions)
        // The Standard toggle stays off-by-default — asymmetric on purpose.
        assertFalse(s.skipPermissions)
    }

    @Test
    fun init_seedsClaudeDebugFromItsOwnBucket_withoutBleedingIntoAutoScan() = runTest {
        val fx = newFixture()
        coEvery { fx.prefs.getLastUsedClaudeOptions() } returns ClaudeLaunchPrefs(debug = true)
        coEvery { fx.prefs.getLastUsedAutoScanOptions() } returns ClaudeLaunchPrefs(debug = false)
        val vm = fx.buildVm()
        advanceUntilIdle()

        val s = vm.uiState.value
        assertTrue(s.claudeDebug)
        assertFalse("the Auto Scan bucket keeps its own saved OFF choice", s.autoScanClaudeDebug)
    }

    @Test
    fun init_seedsFocusModePerBucket_andDefaultsOnWithoutSavedPrefs() = runTest {
        val fresh = newFixture().buildVm()
        advanceUntilIdle()
        assertTrue(fresh.uiState.value.focusMode)
        assertTrue(fresh.uiState.value.autoScanFocusMode)

        val fx = newFixture()
        coEvery { fx.prefs.getLastUsedClaudeOptions() } returns ClaudeLaunchPrefs(focusMode = false)
        val vm = fx.buildVm()
        advanceUntilIdle()

        val s = vm.uiState.value
        assertFalse(s.focusMode)
        assertTrue("the Auto Scan bucket was never saved, so it keeps the default", s.autoScanFocusMode)
    }

    @Test
    fun init_seedsCodexControlsFromPersistedPrefs() = runTest {
        val fx = newFixture()
        coEvery { fx.prefs.getLastUsedCodexOptions() } returns
            CodexLaunchPrefs(
                model = "gpt-5.4-mini",
                sandbox = "read-only",
                approval = "on-request",
                fullAuto = false,
                reasoningEffort = "medium",
            )
        val vm = fx.buildVm()
        advanceUntilIdle()

        val s = vm.uiState.value
        assertEquals("gpt-5.4-mini", s.codexModel)
        assertEquals("read-only", s.codexSandbox)
        assertEquals("medium", s.codexReasoningEffort)
        // The restored sandbox/approval combo re-derives the preset highlight.
        assertEquals(CodexAutonomyPreset.ReadOnly, s.codexPreset)
    }

    @Test
    fun submit_blankBranchName_sendsNullBranchName() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val requestSlot = slot<CreateSessionRequest>()
        coEvery { fx.sessionRepo.createSession(capture(requestSlot)) } returns
            SessionResponse("sess-1", "starting", "2026-04-11T00:00:00Z")

        vm.selectProject("alpha")
        vm.setBranchName("   ") // whitespace-only -> takeIf { it.isNotBlank() } drops it
        vm.submit()
        advanceUntilIdle()

        assertNull(requestSlot.captured.claude_options?.branch_name)
    }

    @Test
    fun submit_onException_setsErrorMessage_andClearsSubmittingFlag() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.sessionRepo.createSession(any()) } throws RuntimeException("boom")

        vm.selectProject("alpha")
        vm.submit()
        advanceUntilIdle()

        assertEquals("Failed to create session: boom", vm.uiState.value.error)
        assertFalse(vm.uiState.value.submitting)
    }

    // ---- AutoScan preset + submit ------------------------------------------

    @Test
    fun selectAutoScanMode_thenGoBack_leavesSharedWorktreeUntouched() = runTest {
        // Regression guard for the worktree-bleed: entering AutoScan config
        // must not flip the shared worktree flag that Standard mode reads —
        // the Auto Scan request hardcodes worktree=true on its own.
        val fx = newFixture(autoScanPrompt = "seed")
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectAutoScanMode()
        vm.goBack()

        assertFalse(
            "bouncing through AutoScan config must not leave worktree forced on",
            vm.uiState.value.worktree,
        )
    }

    @Test
    fun selectAutoScanMode_secondCall_sameProject_doesNotOverwriteUserEdits() = runTest {
        val fx = newFixture(autoScanPrompt = "seed")
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectAutoScanMode() // seeds defaults
        vm.setInitialPrompt("user edit")
        vm.toggleAutoScanNoMemory(true)
        vm.goBack()
        vm.selectAutoScanMode() // same project → must NOT re-seed (preserve edits)

        val s = vm.uiState.value
        assertEquals("user edit", s.initialPrompt)
        assertTrue(s.autoScanNoMemory)
    }

    @Test
    fun selectAutoScanMode_afterProjectChange_reseedsPromptForNewProject() = runTest {
        // Switching to a different project mid-wizard must re-seed the prompt
        // from the new project's saved value, not retain the first project's.
        val fx = newFixture(
            initialProjects = listOf(
                ProjectInfo(name = "alpha", path = "/p/alpha", is_git = true, auto_scan_prompt = "alpha prompt"),
                ProjectInfo(name = "beta", path = "/p/beta", is_git = true, auto_scan_prompt = "beta prompt"),
            ),
            autoScanPrompt = "agent wide default",
        )
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectAutoScanMode()
        assertEquals("alpha prompt", vm.uiState.value.initialPrompt)

        // User goes back, picks a different project, re-enters AutoScan config.
        vm.selectProject("beta")
        vm.selectAutoScanMode()

        val s = vm.uiState.value
        assertEquals("beta prompt", s.initialPrompt)
        assertEquals("beta", s.autoScanConfigInitializedFor)
    }

    @Test
    fun selectAutoScanMode_firstCall_fallsBackToAgentDefault_whenProjectHasNoSavedPrompt() = runTest {
        // A project that has never customized its prompt (auto_scan_prompt = null)
        // must fall back to the agent-wide default from the AutoScan repository.
        val fx = newFixture(
            initialProjects = listOf(
                ProjectInfo(
                    name = "alpha",
                    path = "/p/alpha",
                    is_git = true,
                    auto_scan_prompt = null,
                ),
            ),
            autoScanPrompt = "agent wide default",
        )
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectAutoScanMode()

        assertEquals("agent wide default", vm.uiState.value.initialPrompt)
    }

    @Test
    fun submitAutoScan_happyPath_sendsOptions_andEmitsNavigateToAutoScan() = runTest {
        val fx = newFixture(autoScanPrompt = "seed")
        val vm = fx.buildVm()
        advanceUntilIdle()

        val optsSlot = slot<ClaudeOptionsModel>()
        coEvery { fx.autoScanRepo.startScan("alpha", capture(optsSlot)) } just Runs

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectAutoScanMode() // seeds initialPrompt="seed"; request hardcodes worktree=true

        vm.navigateToAutoScan.test {
            vm.submitAutoScan()
            advanceUntilIdle()

            assertEquals("alpha", awaitItem())
            cancelAndIgnoreRemainingEvents()
        }

        val opts = optsSlot.captured
        assertTrue(
            "with nothing persisted, Auto Scan skip_permissions defaults ON " +
                "(unattended scans) — the live toggle can turn it off per scan",
            opts.skip_permissions,
        )
        assertEquals("Auto Scan effort defaults to high", "high", opts.effort)
        assertNull(opts.model)
        assertTrue(opts.worktree)
        assertNull(opts.branch_name)
        assertEquals("seed", opts.initial_prompt)
        coVerify(exactly = 1) { fx.prefs.setLastUsedProjectName("alpha") }
        // Auto Scan persists into its own bucket, never the Claude Code one.
        coVerify(exactly = 1) { fx.prefs.setLastUsedAutoScanOptions(any()) }
        coVerify(exactly = 0) { fx.prefs.setLastUsedClaudeOptions(any()) }
        assertFalse(vm.uiState.value.autoScanStarting)
    }

    @Test
    fun submitAutoScan_skipPermissionsOff_sendsFalse_andPersistsIt() = runTest {
        val fx = newFixture(autoScanPrompt = "seed")
        val vm = fx.buildVm()
        advanceUntilIdle()

        val optsSlot = slot<ClaudeOptionsModel>()
        coEvery { fx.autoScanRepo.startScan("alpha", capture(optsSlot)) } just Runs
        val prefsSlot = slot<ClaudeLaunchPrefs>()
        coEvery { fx.prefs.setLastUsedAutoScanOptions(capture(prefsSlot)) } just Runs

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectAutoScanMode()
        vm.toggleAutoScanSkipPermissions(false)
        vm.toggleAutoScanPlanMode(true)
        vm.submitAutoScan()
        advanceUntilIdle()

        assertFalse(
            "the toggle is live now — the agent installs attention hooks, so " +
                "an OFF choice must reach the wire instead of being forced true",
            optsSlot.captured.skip_permissions,
        )
        assertTrue(optsSlot.captured.plan_mode)
        assertFalse(prefsSlot.captured.skipPermissions)
        assertTrue(prefsSlot.captured.planMode)
    }

    @Test
    fun submitAutoScan_claudeDebug_readsItsOwnBucket_notTheStandardToggle() = runTest {
        val fx = newFixture(autoScanPrompt = "seed")
        val vm = fx.buildVm()
        advanceUntilIdle()

        val optsSlot = slot<ClaudeOptionsModel>()
        coEvery { fx.autoScanRepo.startScan("alpha", capture(optsSlot)) } just Runs
        val prefsSlot = slot<ClaudeLaunchPrefs>()
        coEvery { fx.prefs.setLastUsedAutoScanOptions(capture(prefsSlot)) } just Runs

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectAutoScanMode()
        vm.toggleClaudeDebug(true) // the STANDARD toggle, a different kind
        vm.submitAutoScan()
        advanceUntilIdle()

        assertFalse(
            "Auto Scan reads autoScanClaudeDebug — a Standard-mode edit must not bleed across kinds",
            optsSlot.captured.debug,
        )
        assertFalse(prefsSlot.captured.debug)

        vm.toggleAutoScanClaudeDebug(true)
        vm.submitAutoScan()
        advanceUntilIdle()

        assertTrue(optsSlot.captured.debug)
        assertTrue(prefsSlot.captured.debug)
        coVerify(exactly = 0) { fx.prefs.setLastUsedClaudeOptions(any()) }
    }

    @Test
    fun standardAndAutoScanClaudeControls_areIsolated() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.setClaudeModel("haiku")
        vm.setAutoScanClaudeModel("opus")
        vm.setAutoScanClaudeEffort("xhigh")
        vm.toggleNoMemory(true)

        val s = vm.uiState.value
        assertEquals("haiku", s.claudeModel)
        assertEquals("opus", s.autoScanClaudeModel)
        assertEquals("xhigh", s.autoScanClaudeEffort)
        assertEquals("Standard effort untouched by the autoScan setter", "high", s.claudeEffort)
        assertTrue(s.noMemory)
        assertFalse("Standard no-memory must not flip the autoScan copy", s.autoScanNoMemory)
    }

    @Test
    fun setAutoScanClaudeModel_toNonXhighModel_clampsEffortToHigh() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.setAutoScanClaudeModel("opus")
        vm.setAutoScanClaudeEffort("max")
        // haiku doesn't support xhigh/max, so the invalid level must clamp.
        vm.setAutoScanClaudeModel("haiku")

        assertEquals("high", vm.uiState.value.autoScanClaudeEffort)
    }

    @Test
    fun submitAutoScan_onException_setsAutoScanError() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.autoScanRepo.startScan(any(), any()) } throws RuntimeException("capacity")

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectAutoScanMode()
        vm.submitAutoScan()
        advanceUntilIdle()

        assertEquals("Failed to start Auto Scan: capacity", vm.uiState.value.error)
        assertFalse(vm.uiState.value.autoScanStarting)
    }

    @Test
    fun onProjectCreated_nonGitProject_clampsWorktreeOff_andClearsBranchName() = runTest {
        val fx = newFixture(initialProjects = emptyList())
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.toggleWorktree(true)
        vm.setBranchName("feature/x")

        vm.onProjectCreated(project("gamma", isGit = false))

        val s = vm.uiState.value
        assertEquals("gamma", s.selectedProjectName)
        assertFalse(
            "Worktree must clamp off for a non-git newly-created project",
            s.worktree,
        )
        assertEquals("", s.branchName)
    }

    @Test
    fun onProjectCreated_doesNotConsultProjectsList_ignoringStaleEntry() = runTest {
        // Even when `state.projects` is populated AND happens to
        // contain a same-named project with a CONFLICTING `is_git` (e.g.
        // the projects flow hasn't refreshed yet with the latest server
        // snapshot), `onProjectCreated` must trust the passed-in
        // ProjectInfo, never `state.projects`. This pins the contract that
        // `selectProject(name)` would violate.
        val fx = newFixture(
            // Stale fixture says "gamma" is non-git...
            initialProjects = listOf(project("gamma", isGit = false)),
        )
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.toggleWorktree(true)
        vm.setBranchName("feature/x")

        // ...but the freshly-returned ProjectInfo from the create call says
        // it IS git. The new method must believe the argument, not the list.
        vm.onProjectCreated(project("gamma", isGit = true))

        val s = vm.uiState.value
        assertEquals("gamma", s.selectedProjectName)
        assertTrue(
            "Worktree must follow the passed ProjectInfo.is_git, not state.projects",
            s.worktree,
        )
        assertEquals("feature/x", s.branchName)
    }

    // ---- CustomShell (Terminal mode, CLA-59) -------------------------------

    @Test
    fun submitCustomShell_sendsShellRequest_andEmitsNavigateToSession() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val requestSlot = slot<CreateSessionRequest>()
        coEvery { fx.sessionRepo.createSession(capture(requestSlot)) } returns
            SessionResponse(
                session_id = "sess-shell",
                state = "starting",
                created_at = "2026-05-20T00:00:00Z",
            )

        vm.selectProject("alpha")
        vm.confirmProject()

        vm.navigateToSession.test {
            vm.submitCustomShell()
            advanceUntilIdle()

            assertEquals("sess-shell", awaitItem())
            cancelAndIgnoreRemainingEvents()
        }

        val req = requestSlot.captured
        assertEquals("alpha", req.project_name)
        assertNull("Terminal mode must not send claude_options", req.claude_options)
        assertNull("Terminal mode must let server pick _DEFAULT_SHELL", req.command)
        coVerify(exactly = 1) { fx.prefs.setLastUsedProjectName("alpha") }
        coVerify(exactly = 1) { fx.prefs.setLastUsedSessionId("sess-shell") }
    }

    @Test
    fun selectTerminalMode_gitStoredProject_advancesToTerminalConfig_noSubmit() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.selectProject("alpha") // is_git = true
        vm.confirmProject()
        vm.pickMode(CreateSessionMode.CustomShell)

        vm.selectTerminalMode()
        advanceUntilIdle()

        // A git stored project opens the Terminal session page (worktree
        // toggle) rather than creating the shell immediately.
        assertEquals(CreateSessionStep.TerminalConfig, vm.uiState.value.step)
        coVerify(exactly = 0) { fx.sessionRepo.createSession(any()) }
    }

    @Test
    fun selectTerminalMode_nonGitStoredProject_submitsShellImmediately_noStepChange() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val requestSlot = slot<CreateSessionRequest>()
        coEvery { fx.sessionRepo.createSession(capture(requestSlot)) } returns
            SessionResponse("sess-nongit", "starting", "2026-05-20T00:00:00Z")

        vm.selectProject("beta") // is_git = false
        vm.confirmProject()
        vm.pickMode(CreateSessionMode.CustomShell)

        vm.selectTerminalMode()
        advanceUntilIdle()

        // Non-git stored project has nothing to configure -> shell session is
        // created immediately, no empty TerminalConfig sheet.
        assertEquals(CreateSessionStep.ModePick, vm.uiState.value.step)
        coVerify(exactly = 1) { fx.sessionRepo.createSession(any()) }
        val req = requestSlot.captured
        assertNull("Terminal mode must keep claude_options null", req.claude_options)
        assertFalse("Non-git project must never request a worktree", req.worktree)
    }

    @Test
    fun terminalConfig_worktreeOn_submitsShellRequest_withWorktreeAndBranch() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val requestSlot = slot<CreateSessionRequest>()
        coEvery { fx.sessionRepo.createSession(capture(requestSlot)) } returns
            SessionResponse("sess-wt", "starting", "2026-05-20T00:00:00Z")

        vm.selectProject("alpha") // git
        vm.confirmProject()
        vm.pickMode(CreateSessionMode.CustomShell)
        vm.selectTerminalMode()
        assertEquals(CreateSessionStep.TerminalConfig, vm.uiState.value.step)

        vm.toggleWorktree(true)
        vm.setBranchName("feature/x")
        vm.submitCustomShell()
        advanceUntilIdle()

        val req = requestSlot.captured
        assertEquals("alpha", req.project_name)
        assertNull("Terminal worktree must travel outside claude_options", req.claude_options)
        assertNull("Terminal mode must let server pick _DEFAULT_SHELL", req.command)
        assertTrue("worktree toggle must propagate to the request", req.worktree)
        assertEquals("feature/x", req.branch_name)
    }

    // A path that ``Paths.get(...).isAbsolute`` returns ``true`` for on the
    // host JVM running these tests. The validator combines
    // ``Paths.get(input).isAbsolute`` with a Windows-shape regex, so a
    // POSIX path like ``/usr/local/bin`` only validates as absolute on a
    // Linux JVM — on a Windows JVM ``Paths.get("/usr/local/bin").isAbsolute``
    // returns ``false`` and the regex doesn't fire either (no drive letter).
    // Using a host-appropriate path lets these tests assert the validator's
    // "valid path" contract regardless of where they run. The Windows-shape
    // path tests (separate cases below) use the regex branch and therefore
    // pass on every host.
    private val hostAbsolutePath: String =
        if (System.getProperty("os.name").orEmpty().lowercase().contains("win"))
            "C:/Work/sandbox"
        else
            "/srv/work"

    @Test
    fun selectCustomPath_transitionsSelection_andLoadsRecentsFromPreferences() = runTest {
        // The "Custom path…" dropdown entry switches the picker into a typed-
        // path flow. The VM must (a) flip `selection` to `CustomPath("")`,
        // (b) load the per-agent recents into `recentCustomPaths` so the
        // chip row renders, and (c) clear any prior draft / error.
        val fx = newFixture(
            agentId = "agent-1",
            recentCustomPaths = listOf("/tmp/foo", "/var/log"),
        )
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.selectCustomPath()
        advanceUntilIdle()

        val s = vm.uiState.value
        assertTrue(
            "selection must flip to CustomPath after selectCustomPath()",
            s.selection is ProjectSelection.CustomPath,
        )
        assertEquals("", (s.selection as ProjectSelection.CustomPath).path)
        assertEquals("", s.customPathDraft)
        assertNull(s.customPathError)
        assertEquals(listOf("/tmp/foo", "/var/log"), s.recentCustomPaths)
        coVerify(exactly = 1) { fx.prefs.getRecentCustomPaths("agent-1") }
    }

    @Test
    fun selectCustomPath_usesAgentUrlFallback_whenAgentIdIsNull() = runTest {
        // Per the plan: DIRECT-mode peers have no `agent_id`; we fall back
        // to `agent_url` for the recents scope key. Both null ⇒ no key,
        // and the recents load is skipped (rare; the screen wouldn't load).
        val fx = newFixture(
            agentId = null,
            agentUrl = "https://agent.local:8080",
            recentCustomPaths = listOf("/home/me"),
        )
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.selectCustomPath()
        advanceUntilIdle()

        coVerify(exactly = 1) { fx.prefs.getRecentCustomPaths("https://agent.local:8080") }
        assertEquals(listOf("/home/me"), vm.uiState.value.recentCustomPaths)
    }

    @Test
    fun setCustomPathDraft_relativePath_setsError_andClampsSelectionPath() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.selectCustomPath()
        advanceUntilIdle()

        vm.setCustomPathDraft("relative/foo")

        val s = vm.uiState.value
        assertEquals("relative/foo", s.customPathDraft)
        assertNotNull("relative path must produce an inline error", s.customPathError)
        // When the draft is invalid the selection.path is held at "" so
        // `submit()` cannot ship an invalid request even if the user
        // races through the Continue button.
        assertEquals("", (s.selection as ProjectSelection.CustomPath).path)
    }

    @Test
    fun setCustomPathDraft_blank_setsError() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.selectCustomPath()
        advanceUntilIdle()

        vm.setCustomPathDraft("   ")

        assertNotNull(
            "blank-after-trim path must surface an error",
            vm.uiState.value.customPathError,
        )
    }

    @Test
    fun setCustomPathDraft_posixAbsolute_clearsError_andUpdatesSelection() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.selectCustomPath()
        advanceUntilIdle()

        // First set an invalid path so we can observe the error -> ok transition.
        vm.setCustomPathDraft("nope")
        assertNotNull(vm.uiState.value.customPathError)

        vm.setCustomPathDraft(hostAbsolutePath)

        val s = vm.uiState.value
        assertNull(s.customPathError)
        assertEquals(hostAbsolutePath, s.customPathDraft)
        assertEquals(hostAbsolutePath, (s.selection as ProjectSelection.CustomPath).path)
    }

    @Test
    fun setCustomPathDraft_trailingSeparator_isDroppedFromTheSelectionOnly_butNotFromARoot() = runTest {
        // A picked folder suggestion leaves `C:/srv/app/` in the field; the cwd
        // and the stored recent must be `C:/srv/app`, or recents list both
        // spellings. Windows-shape so the validator passes on any host JVM.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.selectCustomPath()
        advanceUntilIdle()

        vm.setCustomPathDraft("  C:/srv/app/ ")

        assertEquals("  C:/srv/app/ ", vm.uiState.value.customPathDraft)
        assertEquals("C:/srv/app", (vm.uiState.value.selection as ProjectSelection.CustomPath).path)

        vm.setCustomPathDraft("C:\\")

        assertEquals("C:\\", (vm.uiState.value.selection as ProjectSelection.CustomPath).path)
    }

    @Test
    fun pickCustomPathSuggestion_writesTheFolderWithASeparator_andListsItsChildrenNext() = runTest {
        val fx = newFixture()
        coEvery { fx.restApi.getAgentVersion() } returns VersionInfoResponse(
            name = "mobaide_agent",
            server_version = "0.2.16",
            protocol_versions = listOf(7),
            min_client_version = 5,
            capabilities = Json.parseToJsonElement("""{"fs":{"dirs":true}}""").jsonObject,
        )
        coEvery { fx.restApi.listDirs("C:/", null) } returns FsDirsResponse("C:/", listOf("srv", "usr"), false)
        coEvery { fx.restApi.listDirs("C:/srv/", null) } returns FsDirsResponse("C:/srv/", listOf("app"), false)
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.selectCustomPath()
        vm.onCustomPathFocusChange(true)
        vm.setCustomPathDraft("C:/")
        advanceTimeBy(201)

        assertEquals(listOf("srv", "usr"), vm.customPathSuggestionState.value.names)

        vm.pickCustomPathSuggestion("srv")
        advanceTimeBy(201)

        assertEquals("C:/srv/", vm.uiState.value.customPathDraft)
        assertEquals("C:/srv", (vm.uiState.value.selection as ProjectSelection.CustomPath).path)
        assertEquals(listOf("app"), vm.customPathSuggestionState.value.names)
    }

    @Test
    fun setCustomPathDraft_windowsAbsoluteForwardSlash_isAccepted() = runTest {
        // The plan calls out that Android's JVM `Paths.get("C:\\foo").isAbsolute`
        // returns false on Linux — so the VM uses a regex fallback for the
        // Windows shape. Forward slashes after `<letter>:` count as
        // Windows-absolute too (mobaide_agent runs on Windows hosts).
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.selectCustomPath()
        advanceUntilIdle()

        vm.setCustomPathDraft("C:/ML/Projects")

        val s = vm.uiState.value
        assertNull(
            "Windows-style absolute path with forward slash must validate as absolute",
            s.customPathError,
        )
        assertEquals("C:/ML/Projects", (s.selection as ProjectSelection.CustomPath).path)
    }

    @Test
    fun setCustomPathDraft_windowsAbsoluteBackslash_isAccepted() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.selectCustomPath()
        advanceUntilIdle()

        vm.setCustomPathDraft("D:\\ML\\Projects")

        val s = vm.uiState.value
        assertNull(s.customPathError)
        assertEquals("D:\\ML\\Projects", (s.selection as ProjectSelection.CustomPath).path)
    }

    @Test
    fun setCustomPathDraft_uncPath_isAccepted() = runTest {
        // UNC paths (`\\server\share`) start with two backslashes and are
        // absolute by definition.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.selectCustomPath()
        advanceUntilIdle()

        vm.setCustomPathDraft("\\\\server\\share")

        assertNull(vm.uiState.value.customPathError)
    }

    @Test
    fun setCustomPathDraft_leadingWhitespace_validatesAsAbsolute_andTrimsIntoSelection() = runTest {
        // Validation must judge the same string that gets launched. Leading
        // whitespace used to reach `Paths.get(...)` untrimmed, so " /srv/work"
        // reported "Path must be absolute" and held the selection at "" — while
        // iOS accepted it (CreateSessionModel.validateCustomPath trims first).
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.selectCustomPath()
        advanceUntilIdle()

        val typed = "  $hostAbsolutePath"
        vm.setCustomPathDraft(typed)

        val s = vm.uiState.value
        assertNull(
            "a leading space must not make an absolute path look relative",
            s.customPathError,
        )
        assertEquals("the field must not fight the user mid-keystroke", typed, s.customPathDraft)
        assertEquals(hostAbsolutePath, (s.selection as ProjectSelection.CustomPath).path)
    }

    @Test
    fun setCustomPathDraft_leadingWhitespaceWindowsPath_validatesViaRegexFallback() = runTest {
        // The Windows shape is matched by an `^`-anchored regex, so untrimmed
        // input misses it on every host — this is the second of the two
        // absolute-ness checks and it has to see the trimmed string too.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.selectCustomPath()
        advanceUntilIdle()

        vm.setCustomPathDraft("  C:/ML/Projects")

        val s = vm.uiState.value
        assertNull(s.customPathError)
        assertEquals("C:/ML/Projects", (s.selection as ProjectSelection.CustomPath).path)
    }

    @Test
    fun confirmProject_customPathWithLeadingWhitespace_advancesToModePick() = runTest {
        // The user-visible half of the fix: Continue gates on
        // `selection.path.isNotBlank() && customPathError == null`, so before
        // the trim a leading-space paste left the wizard stuck on
        // ProjectSelect with a disabled button and no way forward but editing.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.selectCustomPath()
        advanceUntilIdle()

        vm.setCustomPathDraft("  $hostAbsolutePath  ")
        vm.confirmProject()

        assertEquals(CreateSessionStep.ModePick, vm.uiState.value.step)
    }

    @Test
    fun removeRecentCustomPath_deletesFromStore_andRefreshesStateFromTheReRead() = runTest {
        val fx = newFixture(recentCustomPaths = listOf("/tmp/gone", "/tmp/kept"))
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.selectCustomPath()
        advanceUntilIdle()
        assertEquals(listOf("/tmp/gone", "/tmp/kept"), vm.uiState.value.recentCustomPaths)

        // "/tmp/fresh" is unreachable by filtering the old list locally, so
        // this pins that the chip row comes from the post-write store read.
        coEvery { fx.prefs.getRecentCustomPaths("agent-1") } returns
            listOf("/tmp/kept", "/tmp/fresh")

        vm.removeRecentCustomPath("/tmp/gone")
        advanceUntilIdle()

        coVerify(exactly = 1) { fx.prefs.removeRecentCustomPath("agent-1", "/tmp/gone") }
        assertEquals(listOf("/tmp/kept", "/tmp/fresh"), vm.uiState.value.recentCustomPaths)
    }

    @Test
    fun removeRecentCustomPath_noScopeKey_isSilentNoOp() = runTest {
        // No agent_id AND no agent_url ⇒ there is no bucket to write to. The
        // VM must skip the store entirely rather than key off null.
        val fx = newFixture(agentId = null, agentUrl = null)
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.selectCustomPath()
        advanceUntilIdle()

        vm.removeRecentCustomPath("/tmp/gone")
        advanceUntilIdle()

        coVerify(exactly = 0) { fx.prefs.removeRecentCustomPath(any(), any()) }
        assertTrue(vm.uiState.value.recentCustomPaths.isEmpty())
    }

    @Test
    fun confirmProject_customPathInvalid_staysOnProjectSelect() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.selectCustomPath()
        advanceUntilIdle()
        // No draft set yet — error is null but selection.path is "".
        vm.confirmProject()
        assertEquals(CreateSessionStep.ProjectSelect, vm.uiState.value.step)

        // With an invalid draft, still must not advance.
        vm.setCustomPathDraft("nope")
        vm.confirmProject()
        assertEquals(CreateSessionStep.ProjectSelect, vm.uiState.value.step)
    }

    @Test
    fun submit_customPath_buildsAdHocRequest_andAddsToRecents_notLastUsedProject() = runTest {
        // The defining contract of custom-path submit:
        // (a) send `project_name = null` + `cwd = <path>`,
        // (b) clamp `worktree=false` + `branch_name=null` so the agent
        //     doesn't try a worktree dance on a non-project path,
        // (c) call `addRecentCustomPath(scopeKey, path)`,
        // (d) NOT call `setLastUsedProjectName` — recents are a separate
        //     concept; `last_used_project_name` should remain untouched.
        val fx = newFixture(agentId = "agent-1")
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.selectCustomPath()
        advanceUntilIdle()
        vm.setCustomPathDraft(hostAbsolutePath)

        // Even if the user previously toggled worktree on while a stored
        // project was active, the CustomPath branch must clamp it off.
        vm.toggleWorktree(true)
        vm.setBranchName("feature/x")

        val requestSlot = slot<CreateSessionRequest>()
        coEvery { fx.sessionRepo.createSession(capture(requestSlot)) } returns
            SessionResponse("sess-adhoc", "starting", "2026-05-20T00:00:00Z")

        vm.confirmProject()
        vm.selectStandardMode()
        vm.submit()
        advanceUntilIdle()

        val req = requestSlot.captured
        assertNull(req.project_name)
        assertEquals(hostAbsolutePath, req.cwd)
        val opts = req.claude_options
        assertNotNull(opts)
        assertFalse("worktree must be forced false for CustomPath submits", opts!!.worktree)
        assertNull("branch_name must be null for CustomPath submits", opts.branch_name)
        coVerify(exactly = 1) { fx.prefs.addRecentCustomPath("agent-1", hostAbsolutePath) }
        coVerify(exactly = 0) { fx.prefs.setLastUsedProjectName(any()) }
        coVerify(exactly = 1) { fx.prefs.setLastUsedSessionId("sess-adhoc") }
    }

    @Test
    fun submit_customPath_withoutAgentScope_skipsRecentsButStillSucceeds() = runTest {
        // No agent_id and no agent_url ⇒ recents won't persist. The submit
        // path must NOT crash — it just skips the `addRecentCustomPath`
        // call and proceeds to navigation.
        val fx = newFixture(agentId = null, agentUrl = null)
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.selectCustomPath()
        advanceUntilIdle()
        vm.setCustomPathDraft(hostAbsolutePath)

        coEvery { fx.sessionRepo.createSession(any()) } returns
            SessionResponse("sess-no-scope", "starting", "2026-05-20T00:00:00Z")

        vm.confirmProject()
        vm.submit()
        advanceUntilIdle()

        coVerify(exactly = 0) { fx.prefs.addRecentCustomPath(any(), any()) }
        coVerify(exactly = 0) { fx.prefs.setLastUsedProjectName(any()) }
        coVerify(exactly = 1) { fx.sessionRepo.createSession(any()) }
    }

    @Test
    fun submit_customPath_invalidDraft_isNoop() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.selectCustomPath()
        advanceUntilIdle()
        vm.setCustomPathDraft("relative/no-good")

        vm.submit()
        advanceUntilIdle()

        coVerify(exactly = 0) { fx.sessionRepo.createSession(any()) }
        assertFalse(vm.uiState.value.submitting)
    }

    @Test
    fun submitCustomShell_customPath_buildsAdHocTerminalRequest() = runTest {
        // Terminal-mode over a CustomPath: same dispatch as the
        // standard Claude branch, but `claude_options = null` and
        // `command = null` (server picks _DEFAULT_SHELL). Recents still
        // get updated.
        val fx = newFixture(agentId = "agent-1")
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.selectCustomPath()
        advanceUntilIdle()
        vm.setCustomPathDraft(hostAbsolutePath)

        val requestSlot = slot<CreateSessionRequest>()
        coEvery { fx.sessionRepo.createSession(capture(requestSlot)) } returns
            SessionResponse("sess-adhoc-shell", "starting", "2026-05-20T00:00:00Z")

        vm.confirmProject()
        vm.submitCustomShell()
        advanceUntilIdle()

        val req = requestSlot.captured
        assertNull(req.project_name)
        assertEquals(hostAbsolutePath, req.cwd)
        assertNull("Terminal mode must not send claude_options", req.claude_options)
        assertNull("Terminal mode must let server pick _DEFAULT_SHELL", req.command)
        coVerify(exactly = 1) { fx.prefs.addRecentCustomPath("agent-1", hostAbsolutePath) }
        coVerify(exactly = 0) { fx.prefs.setLastUsedProjectName(any()) }
    }

    @Test
    fun selectProject_afterCustomPath_clearsDraft_andSwitchesToStored() = runTest {
        // The dropdown lets the user flip back from "Custom path…" to a
        // stored project. The CustomPath state (draft, error) must clear
        // so a subsequent CustomPath re-select starts fresh.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.selectCustomPath()
        advanceUntilIdle()
        vm.setCustomPathDraft(hostAbsolutePath)
        assertNull(vm.uiState.value.customPathError)

        vm.selectProject("alpha")

        val s = vm.uiState.value
        assertTrue(s.selection is ProjectSelection.Stored)
        assertEquals("alpha", (s.selection as ProjectSelection.Stored).name)
        assertEquals("", s.customPathDraft)
        assertNull(s.customPathError)
    }

    @Test
    fun setCodexApproval_customCombo_clearsPresetHighlight() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        // workspace-write + never matches no preset (presets use on-request),
        // so the segmented control highlights nothing (Custom).
        vm.setCodexApproval("never")
        assertNull(vm.uiState.value.codexPreset)
    }

    @Test
    fun submitCodex_happyPath_sendsCliCodex_andCodexOptions() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val requestSlot = slot<CreateSessionRequest>()
        coEvery { fx.sessionRepo.createSession(capture(requestSlot)) } returns
            SessionResponse("sess-codex", "starting", "2026-06-23T00:00:00Z")

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectCodexMode()

        vm.navigateToSession.test {
            vm.submitCodex()
            advanceUntilIdle()
            assertEquals("sess-codex", awaitItem())
            cancelAndIgnoreRemainingEvents()
        }

        val req = requestSlot.captured
        assertEquals("alpha", req.project_name)
        assertEquals("codex", req.cli)
        assertNull("Codex must not send claude_options", req.claude_options)
        val opts = req.codex_options
        assertNotNull(opts)
        assertEquals("workspace-write", opts!!.sandbox)
        assertEquals("on-request", opts.approval)
        assertEquals("high", opts.reasoning_effort)
        assertFalse(opts.full_auto)
        coVerify(exactly = 1) { fx.prefs.setLastUsedProjectName("alpha") }
        coVerify(exactly = 1) { fx.prefs.setLastUsedSessionId("sess-codex") }
        coVerify(exactly = 1) { fx.prefs.setLastUsedCodexOptions(any()) }
        assertFalse(vm.uiState.value.submitting)
    }

    @Test
    fun submitCodex_persistsLastUsedCodexOptions_andNotClaude() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.sessionRepo.createSession(any()) } returns
            SessionResponse("sess-codex-persist", "starting", "2026-06-23T00:00:00Z")
        val prefsSlot = slot<CodexLaunchPrefs>()
        coEvery { fx.prefs.setLastUsedCodexOptions(capture(prefsSlot)) } just Runs

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectCodexMode()
        vm.setCodexModel("gpt-5.4-mini")
        vm.setCodexReasoningEffort("low")
        vm.selectCodexPreset(CodexAutonomyPreset.ReadOnly)
        vm.submitCodex()
        advanceUntilIdle()

        // The same selections are persisted for the next Codex launch.
        val saved = prefsSlot.captured
        assertEquals("gpt-5.4-mini", saved.model)
        assertEquals("read-only", saved.sandbox)
        assertEquals("on-request", saved.approval)
        assertEquals("low", saved.reasoningEffort)
        assertFalse(saved.fullAuto)
        // A Codex submit must not write the Claude blob.
        coVerify(exactly = 0) { fx.prefs.setLastUsedClaudeOptions(any()) }
    }

    @Test
    fun submitCodex_fullAccessPreset_sendsFullAuto() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val requestSlot = slot<CreateSessionRequest>()
        coEvery { fx.sessionRepo.createSession(capture(requestSlot)) } returns
            SessionResponse("sess-yolo", "starting", "2026-06-23T00:00:00Z")

        vm.selectProject("alpha")
        vm.selectCodexPreset(CodexAutonomyPreset.FullAccess)
        vm.submitCodex()
        advanceUntilIdle()

        assertTrue(
            "Full-access preset must send full_auto=true",
            requestSlot.captured.codex_options?.full_auto == true,
        )
    }

    @Test
    fun submitCodex_gitProject_withWorktree_carriesWorktreeAndBranchInCodexOptions() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val requestSlot = slot<CreateSessionRequest>()
        coEvery { fx.sessionRepo.createSession(capture(requestSlot)) } returns
            SessionResponse("sess-wt", "starting", "2026-06-23T00:00:00Z")

        vm.selectProject("alpha") // is_git = true
        vm.confirmProject()
        vm.selectCodexMode()
        vm.toggleWorktree(true)
        vm.setBranchName("feature/x")
        vm.submitCodex()
        advanceUntilIdle()

        val opts = requestSlot.captured.codex_options
        assertNotNull(opts)
        assertTrue("worktree must travel inside codex_options", opts!!.worktree)
        assertEquals("feature/x", opts.branch_name)
        assertFalse(
            "Codex worktree travels in codex_options, not the top-level request field",
            requestSlot.captured.worktree,
        )
    }

    @Test
    fun submitCodex_customPath_clampsWorktreeAndBranch() = runTest {
        val fx = newFixture(agentId = "agent-1")
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.selectCustomPath()
        advanceUntilIdle()
        vm.setCustomPathDraft(hostAbsolutePath)

        // Even with worktree toggled on, the custom-path branch must clamp it.
        vm.toggleWorktree(true)
        vm.setBranchName("feature/x")

        val requestSlot = slot<CreateSessionRequest>()
        coEvery { fx.sessionRepo.createSession(capture(requestSlot)) } returns
            SessionResponse("sess-codex-adhoc", "starting", "2026-06-23T00:00:00Z")

        vm.confirmProject()
        vm.selectCodexMode()
        vm.submitCodex()
        advanceUntilIdle()

        val req = requestSlot.captured
        assertNull(req.project_name)
        assertEquals(hostAbsolutePath, req.cwd)
        assertEquals("codex", req.cli)
        val opts = req.codex_options
        assertNotNull(opts)
        assertFalse("worktree must clamp false for custom path", opts!!.worktree)
        assertNull("branch_name must be null for custom path", opts.branch_name)
    }

    // ---- Antigravity config + submit ----------------------------------------

    @Test
    fun setAntigravitySkipPermissions_forcesSandboxOff() = runTest {
        // Skip-permissions and Sandbox are mutually exclusive (the server
        // rejects the pair with a 422), so enabling either must force-disable
        // the other.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.setAntigravitySandbox(true)
        assertTrue(vm.uiState.value.antigravitySandbox)

        vm.setAntigravitySkipPermissions(true)

        val s = vm.uiState.value
        assertTrue(s.antigravitySkipPermissions)
        assertFalse("enabling skip-permissions must clear sandbox", s.antigravitySandbox)
    }

    @Test
    fun init_seedsAntigravityControlsFromPersistedPrefs() = runTest {
        val fx = newFixture()
        coEvery { fx.prefs.getLastUsedAntigravityOptions() } returns
            AntigravityLaunchPrefs(
                model = "Gemini 3.1 Pro (High)",
                skipPermissions = false,
                sandbox = true,
            )
        val vm = fx.buildVm()
        advanceUntilIdle()

        val s = vm.uiState.value
        assertEquals("Gemini 3.1 Pro (High)", s.antigravityModel)
        assertFalse(s.antigravitySkipPermissions)
        assertTrue(s.antigravitySandbox)
    }

    @Test
    fun init_seedsAntigravityPrefs_conflictingBlob_dropsSandbox() = runTest {
        // A cached blob carrying both toggles (older build / hand-edited
        // store) must seed mutually-exclusively: skip-permissions wins and
        // sandbox is dropped (seeded as saved.sandbox && !saved.skipPermissions).
        val fx = newFixture()
        coEvery { fx.prefs.getLastUsedAntigravityOptions() } returns
            AntigravityLaunchPrefs(skipPermissions = true, sandbox = true)
        val vm = fx.buildVm()
        advanceUntilIdle()

        val s = vm.uiState.value
        assertTrue(s.antigravitySkipPermissions)
        assertFalse(
            "a conflicting saved pair must never seed both toggles on",
            s.antigravitySandbox,
        )
    }

    @Test
    fun submitAntigravity_happyPath_sendsCliAntigravity_andAntigravityOptions() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val requestSlot = slot<CreateSessionRequest>()
        coEvery { fx.sessionRepo.createSession(capture(requestSlot)) } returns
            SessionResponse("sess-agy", "starting", "2026-07-02T00:00:00Z")

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectAntigravityMode()

        vm.navigateToSession.test {
            vm.submitAntigravity()
            advanceUntilIdle()
            assertEquals("sess-agy", awaitItem())
            cancelAndIgnoreRemainingEvents()
        }

        val req = requestSlot.captured
        assertEquals("alpha", req.project_name)
        assertEquals("antigravity", req.cli)
        assertNull("Antigravity must not send claude_options", req.claude_options)
        assertNull("Antigravity must not send codex_options", req.codex_options)
        val opts = req.antigravity_options
        assertNotNull(opts)
        assertNull("model is omitted (null) for the Auto default", opts!!.model)
        assertFalse("skip_permissions defaults off", opts.skip_permissions)
        assertFalse("sandbox defaults off", opts.sandbox)
        assertTrue("add_dirs defaults empty", opts.add_dirs.isEmpty())
        coVerify(exactly = 1) { fx.prefs.setLastUsedProjectName("alpha") }
        coVerify(exactly = 1) { fx.prefs.setLastUsedSessionId("sess-agy") }
        coVerify(exactly = 1) { fx.prefs.setLastUsedAntigravityOptions(any()) }
        assertFalse(vm.uiState.value.submitting)
    }

    @Test
    fun submitAntigravity_withModelAndSkipPermissions_sendsThem() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val requestSlot = slot<CreateSessionRequest>()
        coEvery { fx.sessionRepo.createSession(capture(requestSlot)) } returns
            SessionResponse("sess-agy-model", "starting", "2026-07-02T00:00:00Z")

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectAntigravityMode()
        vm.setAntigravityModel("Gemini 3.1 Pro (High)")
        vm.setAntigravitySkipPermissions(true)
        vm.submitAntigravity()
        advanceUntilIdle()

        val opts = requestSlot.captured.antigravity_options
        assertNotNull(opts)
        assertEquals("Gemini 3.1 Pro (High)", opts!!.model)
        assertTrue(opts.skip_permissions)
        assertFalse("the mutually-exclusive sandbox must stay off", opts.sandbox)
    }

    @Test
    fun submitAntigravity_parsesAddDirsDraft_onNewlinesAndCommas() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val requestSlot = slot<CreateSessionRequest>()
        coEvery { fx.sessionRepo.createSession(capture(requestSlot)) } returns
            SessionResponse("sess-agy-dirs", "starting", "2026-07-02T00:00:00Z")

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectAntigravityMode()
        vm.setAntigravityAddDirsDraft("/a\n/b, /c\n\n")
        vm.submitAntigravity()
        advanceUntilIdle()

        assertEquals(
            "the draft must split on newlines and commas, trim, and drop blanks",
            listOf("/a", "/b", "/c"),
            requestSlot.captured.antigravity_options?.add_dirs,
        )
    }

    @Test
    fun submitAntigravity_persistsLastUsedAntigravityOptions_andNotCodexOrClaude() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.sessionRepo.createSession(any()) } returns
            SessionResponse("sess-agy-persist", "starting", "2026-07-02T00:00:00Z")
        val prefsSlot = slot<AntigravityLaunchPrefs>()
        coEvery { fx.prefs.setLastUsedAntigravityOptions(capture(prefsSlot)) } just Runs

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectAntigravityMode()
        vm.setAntigravityModel("Claude Sonnet 4.6 (Thinking)")
        vm.setAntigravitySandbox(true)
        vm.submitAntigravity()
        advanceUntilIdle()

        // The same selections are persisted for the next Antigravity launch.
        val saved = prefsSlot.captured
        assertEquals("Claude Sonnet 4.6 (Thinking)", saved.model)
        assertTrue(saved.sandbox)
        assertFalse(saved.skipPermissions)
        // An Antigravity submit must write neither the Claude nor the Codex blob.
        coVerify(exactly = 0) { fx.prefs.setLastUsedClaudeOptions(any()) }
        coVerify(exactly = 0) { fx.prefs.setLastUsedCodexOptions(any()) }
    }

    @Test
    fun submitAntigravity_gitProject_withWorktree_carriesWorktreeAndBranchInAntigravityOptions() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val requestSlot = slot<CreateSessionRequest>()
        coEvery { fx.sessionRepo.createSession(capture(requestSlot)) } returns
            SessionResponse("sess-agy-wt", "starting", "2026-07-02T00:00:00Z")

        vm.selectProject("alpha") // is_git = true
        vm.confirmProject()
        vm.selectAntigravityMode()
        vm.toggleWorktree(true)
        vm.setBranchName("feature/x")
        vm.submitAntigravity()
        advanceUntilIdle()

        val opts = requestSlot.captured.antigravity_options
        assertNotNull(opts)
        assertTrue("worktree must travel inside antigravity_options", opts!!.worktree)
        assertEquals("feature/x", opts.branch_name)
        assertFalse(
            "Antigravity worktree travels in antigravity_options, not the top-level request field",
            requestSlot.captured.worktree,
        )
    }

    // ---- Codex auth-required login flow (CLA-71) ---------------------------

    @Test
    fun submitCodex_authRequired409_nestedDetailMarker_opensLoginDialog() = runTest {
        // Direct-REST shape: the marker is nested under `detail`, status 409.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.sessionRepo.createSession(any()) } throws
            ApiException(409, """{"detail":{"codex_auth_required":true}}""")

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectCodexMode()
        vm.submitCodex()
        advanceUntilIdle()

        val s = vm.uiState.value
        assertTrue("auth-required 409 must open the login dialog", s.codexLoginVisible)
        assertNull("the generic error must NOT be set on the auth path", s.error)
        assertFalse(s.submitting)
    }

    @Test
    fun submitCodex_authRequired409_topLevelMarker_opensLoginDialog() = runTest {
        // Relay shape: the marker is top-level, status 409.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.sessionRepo.createSession(any()) } throws
            ApiException(409, """{"codex_auth_required":true,"detail":"Codex sign-in required."}""")

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectCodexMode()
        vm.submitCodex()
        advanceUntilIdle()

        assertTrue(vm.uiState.value.codexLoginVisible)
        assertNull(vm.uiState.value.error)
    }

    @Test
    fun submitCodex_401WithoutMarker_setsGenericError_doesNotOpenDialog() = runTest {
        // A 401 that is NOT the codex marker (e.g. an expired JWT) must fall
        // through to the generic error, never the Codex login dialog.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.sessionRepo.createSession(any()) } throws
            ApiException(401, """{"detail":"Not authenticated"}""")

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectCodexMode()
        vm.submitCodex()
        advanceUntilIdle()

        val s = vm.uiState.value
        assertFalse(s.codexLoginVisible)
        assertNotNull(s.error)
    }

    @Test
    fun submitCodex_401WithGarbageBody_setsGenericError_doesNotOpenDialog() = runTest {
        // A non-JSON 401 body must be tolerated (no crash) and routed to the
        // generic error — `isCodexAuthRequired` defaults to false on parse failure.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.sessionRepo.createSession(any()) } throws
            ApiException(401, "not json at all <<<")

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectCodexMode()
        vm.submitCodex()
        advanceUntilIdle()

        val s = vm.uiState.value
        assertFalse(s.codexLoginVisible)
        assertNotNull(s.error)
    }

    @Test
    fun submitCodexLogin_success_wipesKey_closesDialog_andRetriesSubmitCodex() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.sessionRepo.codexLogin("sk-user-key") } returns
            CodexLoginResponse(authenticated = true)
        val requestSlot = slot<CreateSessionRequest>()
        coEvery { fx.sessionRepo.createSession(capture(requestSlot)) } returns
            SessionResponse("sess-after-login", "starting", "2026-06-23T00:00:00Z")

        // The user is on the Codex step with the login dialog open.
        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectCodexMode()
        vm.setCodexApiKeyDraft("sk-user-key")

        vm.submitCodexLogin()
        advanceUntilIdle()

        val s = vm.uiState.value
        assertFalse("dialog must close after a successful login", s.codexLoginVisible)
        assertEquals("the key must be wiped from memory", "", s.codexApiKeyDraft)
        assertFalse(s.codexLoginSubmitting)
        assertNull(s.codexLoginError)
        // The original Codex create is retried -> repo login then create.
        coVerify(exactly = 1) { fx.sessionRepo.codexLogin("sk-user-key") }
        coVerify(exactly = 1) { fx.sessionRepo.createSession(any()) }
        assertEquals("codex", requestSlot.captured.cli)
    }

    @Test
    fun submitCodexLogin_failure_setsErrorWithoutEchoingKey_keepsDialogOpen() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val secretKey = "sk-super-secret-key-value"
        coEvery { fx.sessionRepo.codexLogin(secretKey) } throws
            ApiException(400, """{"detail":"Codex login failed: bad key"}""")

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectCodexMode()
        vm.setCodexApiKeyDraft(secretKey)

        vm.submitCodexLogin()
        advanceUntilIdle()

        val s = vm.uiState.value
        assertNotNull("a failed login must surface an inline error", s.codexLoginError)
        assertFalse(s.codexLoginSubmitting)
        // The submitted key must never appear in the surfaced error text.
        assertFalse(
            "login error must never echo the submitted key",
            s.codexLoginError!!.contains(secretKey),
        )
        // No session is created when login fails.
        coVerify(exactly = 0) { fx.sessionRepo.createSession(any()) }
    }

    @Test
    fun dismissCodexLogin_wipesDraftAndClosesDialog() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.setCodexApiKeyDraft("sk-typed-then-cancelled")
        vm.toggleCodexApiKeyVisible()

        vm.dismissCodexLogin()

        val s = vm.uiState.value
        assertFalse(s.codexLoginVisible)
        assertEquals("", s.codexApiKeyDraft)
        assertFalse(s.codexApiKeyVisible)
        assertNull(s.codexLoginError)
    }

    // ---- CLI-availability gate (ModePick: gray out an uninstalled CLI) ------

    /**
     * Builds a [VersionInfoResponse] whose `capabilities.cli` carries the given
     * per-CLI availability flags and model lists, mirroring the agent's wire
     * shape. A `null` flag/list omits that field (the "key absent" case); an id
     * with neither field is omitted entirely, and the whole `cli` map is omitted
     * when every id is empty so the response models an old agent's
     * `capabilities:{}`.
     */
    private fun versionResponse(
        claude: Boolean? = null,
        codex: Boolean? = null,
        antigravity: Boolean? = null,
        codexModels: List<String>? = null,
        antigravityModels: List<String>? = null,
        codexModelDetails: List<CliModelDetail>? = null,
        codexModelsPending: Boolean = false,
    ): VersionInfoResponse {
        fun cliEntry(
            id: String,
            available: Boolean?,
            models: List<String>? = null,
            details: List<CliModelDetail>? = null,
            pending: Boolean = false,
        ): String? {
            val fields = buildList {
                if (available != null) add("\"available\":$available")
                if (models != null) add("\"models\":[${models.joinToString(",") { "\"$it\"" }}]")
                if (details != null) {
                    val entries = details.joinToString(",") { detail ->
                        buildList {
                            add("\"id\":\"${detail.id}\"")
                            add("\"efforts\":[${detail.efforts.joinToString(",") { "\"$it\"" }}]")
                            // Omitted — never null — on the wire when unknown.
                            detail.defaultEffort?.let { add("\"default_effort\":\"$it\"") }
                        }.joinToString(",", prefix = "{", postfix = "}")
                    }
                    add("\"model_details\":[$entries]")
                }
                // Omitted — never false — on the wire.
                if (pending) add("\"models_pending\":true")
            }
            return if (fields.isEmpty()) null else "\"$id\":{${fields.joinToString(",")}}"
        }
        val entries = listOfNotNull(
            cliEntry("claude", claude),
            cliEntry("codex", codex, codexModels, codexModelDetails, codexModelsPending),
            cliEntry("antigravity", antigravity, antigravityModels),
        )
        val capabilitiesJson =
            if (entries.isEmpty()) "{}"
            else "{\"cli\":{${entries.joinToString(",")}}}"
        val capabilities: JsonObject = Json.parseToJsonElement(capabilitiesJson).jsonObject
        return VersionInfoResponse(
            name = "mobaide_agent",
            server_version = "0.2.16",
            protocol_versions = listOf(5),
            min_client_version = 5,
            capabilities = capabilities,
        )
    }

    @Test
    fun refreshCliAvailability_codexMissing_disablesCodex_keepsClaudeEnabled() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.sessionRepo.getAgentVersion() } returns
            versionResponse(claude = true, codex = false)

        vm.refreshCliAvailability()
        advanceUntilIdle()

        val s = vm.uiState.value
        assertEquals(true, s.claudeAvailable)
        assertEquals(false, s.codexAvailable)
        assertTrue("claude stays enabled when installed", s.claudeEnabled)
        assertFalse("codex card grays out when its CLI is missing", s.codexEnabled)
    }

    @Test
    fun refreshCliAvailability_unknownCapabilities_leavesBothNull_optimisticallyEnabled() = runTest {
        // Old agent (capabilities: {}) → cliAvailable returns null for both.
        // null must read as "enabled" so a probe that can't see the field never
        // disables a working mode.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.sessionRepo.getAgentVersion() } returns versionResponse()

        vm.refreshCliAvailability()
        advanceUntilIdle()

        val s = vm.uiState.value
        assertNull(s.claudeAvailable)
        assertNull(s.codexAvailable)
        assertTrue("null availability must stay optimistically enabled", s.claudeEnabled)
        assertTrue(s.codexEnabled)
    }

    @Test
    fun refreshCliAvailability_fetchThrows_keepsStateNull_andSetsNoError() = runTest {
        // Best-effort probe: a failed version fetch must NOT surface a user-
        // facing error and must reset the loading flag, leaving the optimistic
        // (null → enabled) state intact. The create-time 503 stays the backstop.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.sessionRepo.getAgentVersion() } throws RuntimeException("network down")

        vm.refreshCliAvailability()
        advanceUntilIdle()

        val s = vm.uiState.value
        assertNull(s.claudeAvailable)
        assertNull(s.codexAvailable)
        assertNull("a passive probe failure must never set the error banner", s.error)
        assertFalse(s.cliAvailabilityLoading)
        assertTrue(s.claudeEnabled)
        assertTrue(s.codexEnabled)
    }

    @Test
    fun refreshCliAvailability_failureAfterPriorSuccess_doesNotClobberLastKnown() = runTest {
        // A later transient failure must preserve the last good availability —
        // the catch block only resets the loading flag, it must not null out
        // the previously-resolved flags.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.sessionRepo.getAgentVersion() } returns
            versionResponse(claude = true, codex = false)
        vm.refreshCliAvailability()
        advanceUntilIdle()
        assertEquals(false, vm.uiState.value.codexAvailable)

        coEvery { fx.sessionRepo.getAgentVersion() } throws RuntimeException("blip")
        vm.refreshCliAvailability()
        advanceUntilIdle()

        val s = vm.uiState.value
        assertEquals("last-known claude availability must survive a failed re-probe", true, s.claudeAvailable)
        assertEquals("last-known codex availability must survive a failed re-probe", false, s.codexAvailable)
        assertNull(s.error)
    }

    @Test
    fun refreshCliAvailability_capturesPerCliModelLists() = runTest {
        // The probe also captures the agent's dynamic model enumeration
        // (capabilities.cli.<id>.models) so the wizard dropdowns can offer
        // live lists instead of the hardcoded companion constants.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.sessionRepo.getAgentVersion() } returns versionResponse(
            claude = true,
            codex = true,
            antigravity = true,
            codexModels = listOf("gpt-5.5", "gpt-5.4"),
            antigravityModels = listOf("Gemini 3.5 Flash (Medium)", "Claude Sonnet 4.6 (Thinking)"),
        )

        vm.refreshCliAvailability()
        advanceUntilIdle()

        val s = vm.uiState.value
        assertEquals(true, s.antigravityAvailable)
        assertEquals(listOf("gpt-5.5", "gpt-5.4"), s.codexModels)
        assertEquals(
            listOf("Gemini 3.5 Flash (Medium)", "Claude Sonnet 4.6 (Thinking)"),
            s.antigravityModels,
        )
    }

    @Test
    fun refreshCliAvailability_modelsAbsent_leavesModelListsNull() = runTest {
        // An agent without enumeration (older agent / failed probe) omits the
        // models arrays -> cliModels returns null, and null must land in the
        // state so the dropdowns fall back to the hardcoded constants.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.sessionRepo.getAgentVersion() } returns
            versionResponse(claude = true, codex = true, antigravity = true)

        vm.refreshCliAvailability()
        advanceUntilIdle()

        val s = vm.uiState.value
        assertNull("absent codex models array must map to null", s.codexModels)
        assertNull("absent antigravity models array must map to null", s.antigravityModels)
        assertNull("absent model_details array must map to null", s.codexModelDetails)
    }

    @Test
    fun refreshCliAvailability_noModelDetails_reconcilesCatalogEffortToDefault() = runTest {
        // A persisted catalog effort (e.g. "ultra" from a newer agent) aimed at
        // an agent that advertises no model_details must drop to the default:
        // that agent may be a pre-catalog build whose wire schema still 422s
        // efforts outside the static vocabulary.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.setCodexReasoningEffort("ultra")

        coEvery { fx.sessionRepo.getAgentVersion() } returns versionResponse(codex = true)
        vm.refreshCliAvailability()
        advanceUntilIdle()

        assertEquals("high", vm.uiState.value.codexReasoningEffort)
    }

    @Test
    fun refreshCliAvailability_reconcileKeepsInVocabAndDetailAdvertisedEfforts() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        // In-vocabulary effort survives a detail-less agent untouched.
        vm.setCodexReasoningEffort("medium")
        coEvery { fx.sessionRepo.getAgentVersion() } returns versionResponse(codex = true)
        vm.refreshCliAvailability()
        advanceUntilIdle()
        assertEquals("medium", vm.uiState.value.codexReasoningEffort)

        // An agent advertising details provably accepts free-form efforts —
        // a catalog effort stands even when unadvertised (keep-selected).
        vm.setCodexReasoningEffort("ultra")
        coEvery { fx.sessionRepo.getAgentVersion() } returns versionResponse(
            codex = true,
            codexModelDetails = listOf(CliModelDetail("gpt-5.6-sol", listOf("low"), "low")),
        )
        vm.refreshCliAvailability()
        advanceUntilIdle()
        assertEquals("ultra", vm.uiState.value.codexReasoningEffort)
    }

    @Test
    fun refreshCliAvailability_modelsPending_keepsEffort_andRetriesUntilWarm() = runTest {
        // A models_pending answer (agent warmup still running) is provisional:
        // no clamp fires on it, and delayed re-probes continue past a SECOND
        // pending answer (a slow-but-legitimate warmup can outlive several
        // probes) until the warmed answer arrives, whose result IS
        // authoritative.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.setCodexReasoningEffort("ultra")

        coEvery { fx.sessionRepo.getAgentVersion() } returnsMany listOf(
            versionResponse(codex = true, codexModelsPending = true),
            versionResponse(codex = true, codexModelsPending = true),
            versionResponse(codex = true),
        )
        vm.refreshCliAvailability()
        advanceTimeBy(1_000)

        assertEquals(
            "a provisional (pending) answer must not clamp",
            "ultra",
            vm.uiState.value.codexReasoningEffort,
        )
        coVerify(exactly = 1) { fx.sessionRepo.getAgentVersion() }

        advanceUntilIdle()

        coVerify(exactly = 3) { fx.sessionRepo.getAgentVersion() }
        assertEquals(
            "the warmed (non-pending, detail-less) answer clamps as usual",
            "high",
            vm.uiState.value.codexReasoningEffort,
        )
    }

    @Test
    fun refreshCliAvailability_modelsPendingForever_stopsAfterTheRetryBudget() = runTest {
        // A truly wedged agent-side warmup must not become an endless poll
        // loop: the initial probe plus WARMUP_MAX_RETRIES re-probes, then stop.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.sessionRepo.getAgentVersion() } returns
            versionResponse(codex = true, codexModelsPending = true)
        vm.refreshCliAvailability()
        advanceUntilIdle()

        coVerify(exactly = 1 + CreateSessionViewModel.WARMUP_MAX_RETRIES) {
            fx.sessionRepo.getAgentVersion()
        }
    }

    @Test
    fun refreshCliAvailability_selectedModelCatalogOmitsEffort_reconcilesToModelDefault() = runTest {
        // Details advertised AND the selected model's entry omits the current
        // effort: the catalog says the model cannot serve it, so the probe
        // applies the same re-default setCodexModel uses on a switch.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.setCodexModel("gpt-5.6-luna")
        vm.setCodexReasoningEffort("ultra")
        coEvery { fx.sessionRepo.getAgentVersion() } returns versionResponse(
            codex = true,
            codexModelDetails = listOf(
                CliModelDetail("gpt-5.6-luna", listOf("low", "medium", "xhigh"), "medium"),
            ),
        )

        vm.refreshCliAvailability()
        advanceUntilIdle()

        assertEquals("medium", vm.uiState.value.codexReasoningEffort)
    }

    @Test
    fun prefsRestore_landingAfterProbe_reconcilesInsteadOfStompingTheClamp() = runTest {
        // The DataStore reads race the ModePick probe: when the probe resolves
        // first (no model_details -> pre-catalog agent), a late restore of a
        // persisted catalog effort must pass through the same reconcile
        // instead of writing "ultra" over the clamp and 422ing the create.
        val fx = newFixture()
        val gate = CompletableDeferred<CodexLaunchPrefs?>()
        coEvery { fx.prefs.getLastUsedCodexOptions() } coAnswers { gate.await() }
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.sessionRepo.getAgentVersion() } returns versionResponse(codex = true)
        vm.refreshCliAvailability()
        advanceUntilIdle()

        gate.complete(
            CodexLaunchPrefs(
                model = null,
                sandbox = "workspace-write",
                approval = "on-request",
                fullAuto = false,
                reasoningEffort = "ultra",
            ),
        )
        advanceUntilIdle()

        assertEquals("high", vm.uiState.value.codexReasoningEffort)
    }

    @Test
    fun prefsRestore_beforeProbe_keepsSavedEffortForRefreshToReconcile() = runTest {
        // Probe not yet resolved at restore time: the saved value must be kept
        // untouched (the agent may well advertise it) — the later probe owns
        // the reconcile.
        val fx = newFixture()
        coEvery { fx.prefs.getLastUsedCodexOptions() } returns CodexLaunchPrefs(
            model = null,
            sandbox = "workspace-write",
            approval = "on-request",
            fullAuto = false,
            reasoningEffort = "ultra",
        )
        val vm = fx.buildVm()
        advanceUntilIdle()

        assertEquals("ultra", vm.uiState.value.codexReasoningEffort)
    }

    @Test
    fun codexEffortsFor_advertisedModel_overridesStaticList() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.sessionRepo.getAgentVersion() } returns versionResponse(
            codex = true,
            codexModelDetails = listOf(
                CliModelDetail("gpt-5.6-sol", listOf("low", "medium", "high", "ultra"), "low"),
            ),
        )
        vm.refreshCliAvailability()
        advanceUntilIdle()

        val s = vm.uiState.value
        assertEquals(listOf("low", "medium", "high", "ultra"), s.codexEffortsFor("gpt-5.6-sol"))
        // Unknown model and the Default (null) model both take the fallback —
        // their capabilities are unknown here.
        assertEquals(CreateSessionViewModel.REASONING_EFFORTS, s.codexEffortsFor("gpt-5.5"))
        assertEquals(CreateSessionViewModel.REASONING_EFFORTS, s.codexEffortsFor(null))
    }

    @Test
    fun setCodexModel_unsupportedEffort_resetsToModelDefaultEffort() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.sessionRepo.getAgentVersion() } returns versionResponse(
            codex = true,
            codexModelDetails = listOf(
                CliModelDetail("gpt-5.6-sol", listOf("low", "medium", "ultra"), "low"),
            ),
        )
        vm.refreshCliAvailability()
        advanceUntilIdle()

        vm.setCodexReasoningEffort("high")
        vm.setCodexModel("gpt-5.6-sol")

        val s = vm.uiState.value
        assertEquals("gpt-5.6-sol", s.codexModel)
        assertEquals("stranded effort must re-default to the catalog default", "low", s.codexReasoningEffort)
    }

    @Test
    fun setCodexModel_missingDefaultEffort_fallsBackWithinAdvertisedSet() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.sessionRepo.getAgentVersion() } returns versionResponse(
            codex = true,
            codexModelDetails = listOf(
                CliModelDetail("with-high", listOf("medium", "high"), null),
                CliModelDetail("without-high", listOf("low", "medium"), null),
            ),
        )
        vm.refreshCliAvailability()
        advanceUntilIdle()

        vm.setCodexReasoningEffort("minimal")
        vm.setCodexModel("with-high")
        assertEquals("'high' wins when advertised", "high", vm.uiState.value.codexReasoningEffort)

        vm.setCodexReasoningEffort("minimal")
        vm.setCodexModel("without-high")
        assertEquals("else the first advertised entry", "low", vm.uiState.value.codexReasoningEffort)
    }

    @Test
    fun setCodexModel_supportedEffort_andUnknownModel_keepEffort() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.sessionRepo.getAgentVersion() } returns versionResponse(
            codex = true,
            codexModelDetails = listOf(
                CliModelDetail("gpt-5.6-sol", listOf("low", "medium", "ultra"), "low"),
            ),
        )
        vm.refreshCliAvailability()
        advanceUntilIdle()

        vm.setCodexReasoningEffort("ultra")
        vm.setCodexModel("gpt-5.6-sol")
        assertEquals("a still-supported effort survives the switch", "ultra", vm.uiState.value.codexReasoningEffort)

        vm.setCodexReasoningEffort("minimal")
        vm.setCodexModel("mystery-model")
        assertEquals(
            "a model with no catalog entry keeps the effort — never downshift on a guess",
            "minimal",
            vm.uiState.value.codexReasoningEffort,
        )

        vm.setCodexModel(null)
        assertEquals("the Default model keeps the effort too", "minimal", vm.uiState.value.codexReasoningEffort)
    }

    @Test
    fun refreshCliAvailability_whileInFlight_dedupes_probesAgentOnce() = runTest {
        // Under the StandardTestDispatcher the launched probe body stays queued
        // until advanceUntilIdle(), but the loading flag is set synchronously
        // BEFORE the launch — so a second call while the first is in flight
        // must bail on the guard instead of double-probing.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.sessionRepo.getAgentVersion() } returns
            versionResponse(claude = true, codex = true)

        vm.refreshCliAvailability()
        vm.refreshCliAvailability()
        advanceUntilIdle()

        coVerify(exactly = 1) { fx.sessionRepo.getAgentVersion() }
        assertFalse("loading flag must reset after the deduped probe", vm.uiState.value.cliAvailabilityLoading)
    }

    @Test
    fun selectAutoScanMode_claudeUnavailable_isNoop_doesNotSeedOrAdvance() = runTest {
        // Auto Scan launches `claude` under the hood, so it's gated on claude
        // too. A disabled-claude tap must not advance, must not seed worktree,
        // and must not set autoScanConfigInitializedFor.
        val fx = newFixture(autoScanPrompt = "seed")
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.sessionRepo.getAgentVersion() } returns
            versionResponse(claude = false, codex = true)

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.refreshCliAvailability()
        advanceUntilIdle()

        vm.selectAutoScanMode()

        val s = vm.uiState.value
        assertEquals(CreateSessionStep.ModePick, s.step)
        assertNull("Auto Scan must not seed its config when claude is unavailable", s.autoScanConfigInitializedFor)
    }

    @Test
    fun selectCodexMode_codexUnknownNull_advancesOptimistically() = runTest {
        // No probe run (or an old agent) leaves codexAvailable null. The guard
        // keys off `codexEnabled` (null != false → enabled), so the mode still
        // opens and the create-time path remains the authority.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectCodexMode()

        assertEquals(CreateSessionStep.CodexConfig, vm.uiState.value.step)
    }

    @Test
    fun claudeEffortsFor_opus_offersFullLadderIncludingXhighAndMax() {
        val efforts = CreateSessionViewModel.claudeEffortsFor("opus")
        assertEquals(CreateSessionViewModel.CLAUDE_EFFORTS, efforts)
        assertTrue("opus is xhigh-capable", efforts.contains("xhigh"))
        assertTrue("opus is max-capable", efforts.contains("max"))
    }

    @Test
    fun claudeEffortsFor_haiku_capsAtHigh() {
        val efforts = CreateSessionViewModel.claudeEffortsFor("haiku")
        assertEquals(listOf("low", "medium", "high"), efforts)
        assertFalse("haiku must never be offered xhigh", efforts.contains("xhigh"))
        assertFalse("haiku must never be offered max", efforts.contains("max"))
    }

    @Test
    fun claudeEffortsFor_nullDefaultModel_capsAtHigh() {
        // The account-default (null) model's resolved capability is unknown, so
        // the ladder caps at high — never offer a level that silently downshifts.
        assertEquals(
            listOf("low", "medium", "high"),
            CreateSessionViewModel.claudeEffortsFor(null),
        )
    }

    @Test
    fun claudeEffortsFor_unknownModel_capsAtHigh() {
        // A model alias outside the curated set defaults to the safe base ladder
        // rather than optimistically offering xhigh/max.
        assertEquals(
            listOf("low", "medium", "high"),
            CreateSessionViewModel.claudeEffortsFor("gpt-nonsense"),
        )
    }

    // ---- Custom agent types -------------------------------------------------

    private fun customType(
        id: String = "ab12cd34ef56",
        name: String = "Aider",
        command: String = "aider --model sonnet",
    ): CustomAgentTypeDto = CustomAgentTypeDto(
        id = id,
        name = name,
        command = command,
        created_at = "2026-07-28T12:00:00Z",
        updated_at = "2026-07-28T12:00:00Z",
    )

    @Test
    fun customTypeDeletedRemotely_clearsItsSelectedMode() = runTest {
        // A type deleted on another device while the picker is open must not
        // stay selected — its card is gone and a launch would 404.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        fx.customTypesFlow.value = listOf(customType(id = "t1"))
        advanceUntilIdle()

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.pickMode(CreateSessionMode.Custom("t1"))
        assertEquals(CreateSessionMode.Custom("t1"), vm.uiState.value.selectedMode)

        fx.customTypesFlow.value = emptyList()
        advanceUntilIdle()

        assertNull(vm.uiState.value.selectedMode)
    }

    @Test
    fun selectCustomAgentMode_nonGitProject_submitsImmediately_withCustomCli() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val requestSlot = slot<CreateSessionRequest>()
        coEvery { fx.sessionRepo.createSession(capture(requestSlot)) } returns
            SessionResponse("sess-custom", "starting", "2026-07-28T00:00:00Z")

        vm.selectProject("beta") // is_git = false
        vm.confirmProject()
        vm.pickMode(CreateSessionMode.Custom("ab12cd34ef56"))
        vm.selectCustomAgentMode("ab12cd34ef56")
        advanceUntilIdle()

        coVerify(exactly = 1) { fx.sessionRepo.createSession(any()) }
        val req = requestSlot.captured
        assertEquals("custom_ab12cd34ef56", req.cli)
        assertNull("only the type id goes on the wire, never the command", req.command)
        assertNull("custom types must not send claude_options", req.claude_options)
        assertFalse("non-git project must never request a worktree", req.worktree)
    }

    @Test
    fun submitFromTerminalConfig_customMode_submitsCustomCli_withWorktreePassthrough() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val requestSlot = slot<CreateSessionRequest>()
        coEvery { fx.sessionRepo.createSession(capture(requestSlot)) } returns
            SessionResponse("sess-custom-wt", "starting", "2026-07-28T00:00:00Z")

        vm.selectProject("alpha") // git
        vm.confirmProject()
        vm.pickMode(CreateSessionMode.Custom("ab12cd34ef56"))
        vm.selectCustomAgentMode("ab12cd34ef56")
        assertEquals(CreateSessionStep.TerminalConfig, vm.uiState.value.step)

        vm.toggleWorktree(true)
        vm.setBranchName("feature/x")
        vm.submitFromTerminalConfig()
        advanceUntilIdle()

        val req = requestSlot.captured
        assertEquals("custom_ab12cd34ef56", req.cli)
        assertTrue("worktree toggle must propagate to a custom launch", req.worktree)
        assertEquals("feature/x", req.branch_name)
        assertNull(req.command)
    }

    @Test
    fun submitFromTerminalConfig_customShellMode_stillSubmitsPlainShell() = runTest {
        // The TerminalConfig step is shared between Terminal mode and custom
        // agent types — the dispatch must not send a cli for plain Terminal.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val requestSlot = slot<CreateSessionRequest>()
        coEvery { fx.sessionRepo.createSession(capture(requestSlot)) } returns
            SessionResponse("sess-shell2", "starting", "2026-07-28T00:00:00Z")

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.pickMode(CreateSessionMode.CustomShell)
        vm.selectTerminalMode()
        vm.submitFromTerminalConfig()
        advanceUntilIdle()

        assertNull("plain Terminal must not carry a custom cli", requestSlot.captured.cli)
    }

    // ---- Custom type editor state machine -----------------------------------

    @Test
    fun saveCustomAgentType_clientValidation_setsInlineError_withoutRequest() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.openTypeEditor(null)

        vm.saveCustomAgentType("   ", "aider")
        assertEquals("Name is required", vm.uiState.value.typeEditorError)

        vm.saveCustomAgentType("n".repeat(61), "aider")
        assertEquals(
            "Name must be at most 60 characters",
            vm.uiState.value.typeEditorError,
        )

        vm.saveCustomAgentType("Aider", "   ")
        assertEquals("Command is required", vm.uiState.value.typeEditorError)

        vm.saveCustomAgentType("Aider", "c".repeat(1001))
        assertEquals(
            "Command must be at most 1000 characters",
            vm.uiState.value.typeEditorError,
        )

        advanceUntilIdle()
        coVerify(exactly = 0) { fx.customTypesRepo.createType(any(), any()) }
        coVerify(exactly = 0) { fx.customTypesRepo.updateType(any(), any(), any()) }
    }

    @Test
    fun saveCustomAgentType_supplementaryPlaneAtLimit_countsCodePoints_andCreates() = runTest {
        // 60 / 1000 code points but 120 / 2000 UTF-16 units: counting
        // String.length would falsely reject input the server's
        // per-character limits accept.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        val name = "🚀".repeat(60)
        val command = "🚀".repeat(1000)
        coEvery { fx.customTypesRepo.createType(name, command) } returns customType()

        vm.openTypeEditor(null)
        vm.saveCustomAgentType(name, command)
        advanceUntilIdle()

        coVerify(exactly = 1) { fx.customTypesRepo.createType(name, command) }
        assertEquals(TypeEditorState.Hidden, vm.uiState.value.typeEditor)
        assertNull(vm.uiState.value.typeEditorError)
    }

    @Test
    fun saveCustomAgentType_create_trimsFields_andDismissesOnSuccess() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        coEvery { fx.customTypesRepo.createType("Aider", "aider") } returns customType()

        vm.openTypeEditor(null)
        vm.saveCustomAgentType("  Aider  ", "  aider  ")
        advanceUntilIdle()

        coVerify(exactly = 1) { fx.customTypesRepo.createType("Aider", "aider") }
        assertEquals(TypeEditorState.Hidden, vm.uiState.value.typeEditor)
        assertNull(vm.uiState.value.typeEditorError)
        assertFalse(vm.uiState.value.typeEditorSaving)
    }

    @Test
    fun saveCustomAgentType_editTarget_updatesWithBothFields() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        val target = customType(id = "t9")
        coEvery { fx.customTypesRepo.updateType("t9", "New", "new-cmd") } returns
            customType(id = "t9", name = "New", command = "new-cmd")

        vm.openTypeEditor(target)
        vm.saveCustomAgentType("New", "new-cmd")
        advanceUntilIdle()

        coVerify(exactly = 1) { fx.customTypesRepo.updateType("t9", "New", "new-cmd") }
        coVerify(exactly = 0) { fx.customTypesRepo.createType(any(), any()) }
        assertEquals(TypeEditorState.Hidden, vm.uiState.value.typeEditor)
    }

    @Test
    fun saveCustomAgentType_serverConflict409_surfacesDetailInline_keepsEditorOpen() = runTest {
        // 409 detail strings (20-type cap, name conflict) are user-grade copy
        // and must surface verbatim — never the raw "HTTP 409: {json}" leak.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        coEvery { fx.customTypesRepo.createType(any(), any()) } throws
            ApiException(409, """{"detail":"Custom agent type limit reached (20)"}""")

        vm.openTypeEditor(null)
        vm.saveCustomAgentType("Aider", "aider")
        advanceUntilIdle()

        assertEquals(
            "Custom agent type limit reached (20)",
            vm.uiState.value.typeEditorError,
        )
        assertTrue(vm.uiState.value.typeEditor is TypeEditorState.Open)
        assertFalse(vm.uiState.value.typeEditorSaving)
    }

    @Test
    fun saveCustomAgentType_staleSuccessAfterReopen_keepsNewEditorOpen() = runTest {
        // A save resolving after the sheet was dismissed and reopened for a
        // different target must only release the saving flag — never close
        // the editor it did not come from.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        val gate = CompletableDeferred<CustomAgentTypeDto>()
        coEvery { fx.customTypesRepo.createType("Aider", "aider") } coAnswers { gate.await() }

        vm.openTypeEditor(null)
        vm.saveCustomAgentType("Aider", "aider")
        advanceUntilIdle()
        assertTrue(vm.uiState.value.typeEditorSaving)

        val other = customType(id = "t2", name = "Goose", command = "goose")
        vm.dismissTypeEditor()
        vm.openTypeEditor(other)

        gate.complete(customType())
        advanceUntilIdle()

        assertEquals(TypeEditorState.Open(other), vm.uiState.value.typeEditor)
        assertNull(vm.uiState.value.typeEditorError)
        assertFalse(vm.uiState.value.typeEditorSaving)
    }

    @Test
    fun saveCustomAgentType_staleFailureAfterReopen_noStaleErrorInNewEditor() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        val gate = CompletableDeferred<CustomAgentTypeDto>()
        coEvery { fx.customTypesRepo.createType("Aider", "aider") } coAnswers { gate.await() }

        vm.openTypeEditor(null)
        vm.saveCustomAgentType("Aider", "aider")
        advanceUntilIdle()
        assertTrue(vm.uiState.value.typeEditorSaving)

        val other = customType(id = "t2", name = "Goose", command = "goose")
        vm.dismissTypeEditor()
        vm.openTypeEditor(other)

        gate.completeExceptionally(
            ApiException(409, """{"detail":"An agent type named \"Aider\" already exists"}"""),
        )
        advanceUntilIdle()

        assertEquals(TypeEditorState.Open(other), vm.uiState.value.typeEditor)
        assertNull(
            "a stale save's error must not surface in an editor it did not come from",
            vm.uiState.value.typeEditorError,
        )
        assertFalse(vm.uiState.value.typeEditorSaving)
    }

    // ---- Custom type delete flow --------------------------------------------

    @Test
    fun confirmDeleteType_success_deletesAndDismissesDialog() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        coEvery { fx.customTypesRepo.deleteType("t1") } just Runs

        vm.requestDeleteType(customType(id = "t1"))
        vm.confirmDeleteType()
        advanceUntilIdle()

        coVerify(exactly = 1) { fx.customTypesRepo.deleteType("t1") }
        assertNull(vm.uiState.value.deleteTypeTarget)
        assertFalse(vm.uiState.value.deleteTypeInFlight)
        assertNull(vm.uiState.value.error)
    }

    @Test
    fun confirmDeleteType_403_treatedAsAlreadyGone_dismissesAndRefreshes() = runTest {
        // 403 means the type is already gone elsewhere — success semantics
        // plus a reconcile refresh, never an error banner.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        coEvery { fx.customTypesRepo.deleteType("t1") } throws
            ApiException(403, """{"detail":"Not found"}""")

        vm.requestDeleteType(customType(id = "t1"))
        vm.confirmDeleteType()
        advanceUntilIdle()

        assertNull(vm.uiState.value.deleteTypeTarget)
        assertNull(vm.uiState.value.error)
        // Once from init's cold-start seed + once from the 403 reconcile.
        coVerify(exactly = 2) { fx.customTypesRepo.refreshTypes() }
    }

    @Test
    fun confirmDeleteType_serverError_keepsDialogClosed_setsErrorBanner() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        coEvery { fx.customTypesRepo.deleteType("t1") } throws
            ApiException(500, """{"detail":"boom"}""")

        vm.requestDeleteType(customType(id = "t1"))
        vm.confirmDeleteType()
        advanceUntilIdle()

        assertFalse(vm.uiState.value.deleteTypeInFlight)
        assertNotNull(vm.uiState.value.error)
    }

    // ---- Custom launch error mapping ----------------------------------------

    @Test
    fun submitCustomAgent_agentUpdateRequired409_setsUpdateAgentMessage() = runTest {
        // Relay-side gate: the agent's live tunnel negotiated protocol < 7.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        coEvery { fx.sessionRepo.createSession(any()) } throws
            ApiException(409, """{"agent_update_required":true,"detail":"agent too old"}""")

        vm.selectProject("beta") // non-git -> submits immediately
        vm.confirmProject()
        vm.pickMode(CreateSessionMode.Custom("t1"))
        vm.selectCustomAgentMode("t1")
        advanceUntilIdle()

        assertEquals(
            "Update your agent to launch custom agent types",
            vm.uiState.value.error,
        )
        assertFalse(vm.uiState.value.submitting)
    }

    @Test
    fun submitCustomAgent_404_setsDeletedMessage_andRefreshesTypes() = runTest {
        // The type was deleted between rendering the card and the launch —
        // recognized by the relay's exact detail string, not the status alone.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        coEvery { fx.sessionRepo.createSession(any()) } throws
            ApiException(404, """{"detail":"Custom agent type not found"}""")

        vm.selectProject("beta")
        vm.confirmProject()
        vm.pickMode(CreateSessionMode.Custom("t1"))
        vm.selectCustomAgentMode("t1")
        advanceUntilIdle()

        assertEquals("This agent type was deleted", vm.uiState.value.error)
        // Once from init's cold-start seed + once from the 404 reconcile.
        coVerify(exactly = 2) { fx.customTypesRepo.refreshTypes() }
    }

    @Test
    fun submitCustomAgent_projectDeleted404_fallsThroughToGenericError() = runTest {
        // A custom launch can also 404 for a concurrently-deleted PROJECT
        // (agent-side detail) — same status, different detail string. That
        // must not masquerade as a deleted type nor trigger a type refresh.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        coEvery { fx.sessionRepo.createSession(any()) } throws
            ApiException(404, """{"detail":"Project 'beta' not found"}""")

        vm.selectProject("beta")
        vm.confirmProject()
        vm.pickMode(CreateSessionMode.Custom("t1"))
        vm.selectCustomAgentMode("t1")
        advanceUntilIdle()

        assertNotNull(vm.uiState.value.error)
        assertTrue(vm.uiState.value.error!!.startsWith("Failed to create session"))
        assertFalse(vm.uiState.value.submitting)
        // Only init's cold-start seed — a project 404 must not reconcile types.
        coVerify(exactly = 1) { fx.customTypesRepo.refreshTypes() }
    }

    @Test
    fun submitCustomShell_404_isNotMappedToDeletedTypeMessage() = runTest {
        // The 404 → "deleted" mapping is gated on a custom_ cli — a plain
        // Terminal 404 must fall through to the generic failure copy.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        coEvery { fx.sessionRepo.createSession(any()) } throws
            ApiException(404, """{"detail":"project gone"}""")

        vm.selectProject("beta")
        vm.confirmProject()
        vm.pickMode(CreateSessionMode.CustomShell)
        vm.selectTerminalMode()
        advanceUntilIdle()

        assertNotNull(vm.uiState.value.error)
        assertTrue(vm.uiState.value.error!!.startsWith("Failed to create session"))
        // Only init's cold-start seed — no reconcile for non-custom launches.
        coVerify(exactly = 1) { fx.customTypesRepo.refreshTypes() }
    }

    @Test
    fun chooseMode_Standard_recordsSelectionAndAdvances_inOneTap() = runTest {
        // The picker has no Continue button any more: a single card tap must
        // both record the mode and move the wizard on.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.selectProject("alpha")
        vm.confirmProject()

        vm.chooseMode(CreateSessionMode.Standard)

        val s = vm.uiState.value
        assertEquals(CreateSessionMode.Standard, s.selectedMode)
        assertEquals(CreateSessionStep.StandardConfig, s.step)
    }

    @Test
    fun chooseMode_Custom_nonGitProject_launchesWithThatTypesCli() = runTest {
        // The tapped card's type id has to survive the dispatch — a custom
        // card must never launch as a plain shell or as another type.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val requestSlot = slot<CreateSessionRequest>()
        coEvery { fx.sessionRepo.createSession(capture(requestSlot)) } returns
            SessionResponse("sess-tap-custom", "starting", "2026-07-30T00:00:00Z")

        vm.selectProject("beta") // is_git = false
        vm.confirmProject()

        vm.chooseMode(CreateSessionMode.Custom("ab12cd34ef56"))
        advanceUntilIdle()

        assertEquals(CreateSessionMode.Custom("ab12cd34ef56"), vm.uiState.value.selectedMode)
        assertEquals("custom_ab12cd34ef56", requestSlot.captured.cli)
    }

    @Test
    fun chooseMode_Standard_claudeUnavailable_doesNotSelectOrAdvance() = runTest {
        // A card tap is the only commit path now, so chooseMode itself has to
        // enforce the CLI gate — including not leaving a selected highlight on
        // a card that can never commit.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.sessionRepo.getAgentVersion() } returns
            versionResponse(claude = false, codex = true)

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.refreshCliAvailability()
        advanceUntilIdle()

        vm.chooseMode(CreateSessionMode.Standard)

        val s = vm.uiState.value
        assertEquals(CreateSessionStep.ModePick, s.step)
        assertNull("a blocked tap must not leave a card selected", s.selectedMode)
    }

    @Test
    fun chooseMode_Codex_codexUnavailable_isBlocked_whileStandardStillAdvances() = runTest {
        // isModeEnabled must key each card off its own CLI flag: a crosswired
        // Codex -> claudeEnabled mapping would let the Codex card through here.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.sessionRepo.getAgentVersion() } returns
            versionResponse(claude = true, codex = false)

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.refreshCliAvailability()
        advanceUntilIdle()

        vm.chooseMode(CreateSessionMode.Codex)
        assertEquals(CreateSessionStep.ModePick, vm.uiState.value.step)
        assertNull(vm.uiState.value.selectedMode)

        vm.chooseMode(CreateSessionMode.Standard)
        assertEquals(CreateSessionStep.StandardConfig, vm.uiState.value.step)
    }

    @Test
    fun chooseMode_AutoScan_projectWithLinearKey_advancesToAutoScanConfig() = runTest {
        val fx = newFixture(
            initialProjects = listOf(
                ProjectInfo(
                    name = "linear",
                    path = "/p/linear",
                    is_git = true,
                    has_linear_key = true,
                ),
            ),
            autoScanPrompt = "seed",
        )
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.selectProject("linear")
        vm.confirmProject()

        vm.chooseMode(CreateSessionMode.AutoScan)

        val s = vm.uiState.value
        assertTrue(s.autoScanEnabled)
        assertEquals(CreateSessionMode.AutoScan, s.selectedMode)
        assertEquals(CreateSessionStep.AutoScanConfig, s.step)
    }

    @Test
    fun chooseMode_AutoScan_projectWithoutLinearKey_isBlocked() = runTest {
        // Auto Scan reads the selected project's Linear issues, so a project
        // with no stored key must not commit — and must not seed the config.
        val fx = newFixture(autoScanPrompt = "seed")
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.selectProject("alpha") // has_linear_key defaults to false
        vm.confirmProject()

        vm.chooseMode(CreateSessionMode.AutoScan)

        val s = vm.uiState.value
        assertFalse(s.autoScanEnabled)
        assertEquals(CreateSessionStep.ModePick, s.step)
        assertNull(s.selectedMode)
        assertNull("a blocked tap must not seed the Auto Scan config", s.autoScanConfigInitializedFor)
    }

    @Test
    fun chooseMode_AutoScan_customPath_isBlocked_despiteALinearKeyedProject() = runTest {
        // The key must be read off the SELECTED stored project — a custom path
        // has none, and another project's key must not stand in for it.
        val fx = newFixture(
            initialProjects = listOf(
                ProjectInfo(
                    name = "linear",
                    path = "/p/linear",
                    is_git = true,
                    has_linear_key = true,
                ),
            ),
        )
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.selectCustomPath()
        vm.setCustomPathDraft(hostAbsolutePath)
        vm.confirmProject()
        advanceUntilIdle()
        assertEquals(CreateSessionStep.ModePick, vm.uiState.value.step)

        vm.chooseMode(CreateSessionMode.AutoScan)

        val s = vm.uiState.value
        assertFalse(s.autoScanEnabled)
        assertNull(s.selectedMode)
        assertNull(s.autoScanConfigInitializedFor)
    }

    @Test
    fun chooseMode_whileSubmitting_isIgnored_soAnInFlightLaunchIsNotOvertaken() = runTest {
        // Terminal and custom types launch straight from the picker; until that
        // create returns, a tap on a different card must not advance the wizard
        // out from under it.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val gate = CompletableDeferred<SessionResponse>()
        coEvery { fx.sessionRepo.createSession(any()) } coAnswers { gate.await() }

        vm.selectProject("beta") // non-git -> launches on tap
        vm.confirmProject()
        vm.chooseMode(CreateSessionMode.CustomShell)
        advanceUntilIdle()
        assertTrue(vm.uiState.value.submitting)

        vm.chooseMode(CreateSessionMode.Standard)
        advanceUntilIdle()

        val s = vm.uiState.value
        assertEquals(CreateSessionStep.ModePick, s.step)
        assertEquals(CreateSessionMode.CustomShell, s.selectedMode)

        gate.complete(SessionResponse("sess-gated", "starting", "2026-07-30T00:00:00Z"))
        advanceUntilIdle()
        coVerify(exactly = 1) { fx.sessionRepo.createSession(any()) }
    }

    private fun chatVersion(
        chat: String,
        cli: String = """{"codex":{"available":true}}""",
    ): VersionInfoResponse = VersionInfoResponse(
        name = "mobaide_agent",
        server_version = "0.3.23",
        protocol_versions = listOf(7),
        min_client_version = 6,
        capabilities = Json.parseToJsonElement(
            """{"cli":$cli,"chat":$chat}""",
        ).jsonObject,
    )

    private fun TestScope.codexSubmit(
        fx: Fixture,
        setUp: (CreateSessionViewModel) -> Unit = {},
    ): Pair<CreateSessionRequest, CodexLaunchPrefs> {
        val vm = fx.buildVm()
        advanceUntilIdle()
        val requestSlot = slot<CreateSessionRequest>()
        coEvery { fx.sessionRepo.createSession(capture(requestSlot)) } returns
            SessionResponse("sess-codex-chat", "starting", "2026-11-08T00:00:00Z")
        val prefsSlot = slot<CodexLaunchPrefs>()
        coEvery { fx.prefs.setLastUsedCodexOptions(capture(prefsSlot)) } just Runs

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectCodexMode()
        setUp(vm)
        vm.submitCodex()
        advanceUntilIdle()
        return requestSlot.captured to prefsSlot.captured
    }

    @Test
    fun submitCodex_byDefault_opensTheChatView() = runTest {
        val (request, saved) = codexSubmit(newFixture())

        assertEquals(SessionResponse.UI_CHAT, request.ui)
        assertFalse(saved.terminalUi)
    }

    @Test
    fun submitCodex_withTerminalUi_opensTheTerminal_andRemembersTheChoice() = runTest {
        val (request, saved) = codexSubmit(newFixture()) { it.toggleCodexTerminalUi(true) }

        assertEquals(SessionResponse.UI_TERMINAL, request.ui)
        assertTrue(saved.terminalUi)
    }

    @Test
    fun submitCodex_whereThePcHasNoCodexChat_opensTheTerminal() = runTest {
        val fx = newFixture()
        coEvery { fx.sessionRepo.getAgentVersion() } returns chatVersion("""{"claude":{"resume":true}}""")

        val (request, _) = codexSubmit(fx) { vm ->
            vm.refreshCliAvailability()
            advanceUntilIdle()
            assertEquals("The chat view needs Node.js on your PC.", vm.uiState.value.codexChatUnavailableHint)
            assertFalse(vm.uiState.value.codexResumeAvailable)
        }

        assertEquals(SessionResponse.UI_TERMINAL, request.ui)
    }

    @Test
    fun codexResume_isOffered_onlyForTheChatView() = runTest {
        val fx = newFixture()
        coEvery { fx.sessionRepo.getAgentVersion() } returns
            chatVersion("""{"codex":{"resume":true,"live":true}}""")
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.refreshCliAvailability()
        advanceUntilIdle()

        assertTrue(vm.uiState.value.codexResumeAvailable)
        assertNull(vm.uiState.value.codexChatUnavailableHint)

        vm.toggleCodexTerminalUi(true)

        assertFalse(vm.uiState.value.codexResumeAvailable)
    }

    @Test
    fun resumeChatSession_codex_sendsTheCodexOptionsWithoutAWorktree() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        val requestSlot = slot<CreateSessionRequest>()
        coEvery { fx.sessionRepo.createSession(capture(requestSlot)) } returns
            SessionResponse("sess-resumed", "starting", "2026-11-08T00:00:00Z")

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectCodexMode()
        vm.selectCodexPreset(CodexAutonomyPreset.ReadOnly)
        vm.toggleWorktree(true)
        vm.setBranchName("feature/x")
        vm.resumeChatSession("codex", ChatStoredSession(session_id = "external-2222", cwd = "/p/alpha/"))
        advanceUntilIdle()

        val request = requestSlot.captured
        assertEquals("codex", request.cli)
        assertEquals("alpha", request.project_name)
        assertNull(request.cwd)
        assertEquals(SessionResponse.UI_CHAT, request.ui)
        assertEquals("external-2222", request.chat_resume_id)
        assertNull(request.claude_options)
        val options = request.codex_options
        assertNotNull(options)
        assertEquals("read-only", options!!.sandbox)
        assertFalse(options.worktree)
        assertNull(options.branch_name)
    }

    @Test
    fun resumeChatSession_codex_inAnotherFolder_sendsThatFolder() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        val requestSlot = slot<CreateSessionRequest>()
        coEvery { fx.sessionRepo.createSession(capture(requestSlot)) } returns
            SessionResponse("sess-resumed", "starting", "2026-11-08T00:00:00Z")

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectCodexMode()
        vm.resumeChatSession("codex", ChatStoredSession(session_id = "external-1111", cwd = "/srv/other"))
        advanceUntilIdle()

        assertNull(requestSlot.captured.project_name)
        assertEquals("/srv/other", requestSlot.captured.cwd)
        assertEquals("codex", requestSlot.captured.cli)
    }

    /** A Codex resume whose create needs a Codex sign-in first; returns every create sent. */
    private fun TestScope.codexResumeNeedingSignIn(fx: Fixture, vm: CreateSessionViewModel): List<CreateSessionRequest> {
        val requests = mutableListOf<CreateSessionRequest>()
        coEvery { fx.sessionRepo.createSession(capture(requests)) } throws
            ApiException(409, """{"detail":{"codex_auth_required":true}}""") andThen
            SessionResponse("sess-resumed", "starting", "2026-11-08T00:00:00Z")
        coEvery { fx.sessionRepo.codexLogin("sk-user-key") } returns CodexLoginResponse(authenticated = true)

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectCodexMode()
        vm.resumeChatSession("codex", ChatStoredSession(session_id = "external-2222", cwd = "/srv/other"))
        advanceUntilIdle()
        assertTrue(vm.uiState.value.codexLoginVisible)
        return requests
    }

    @Test
    fun submitCodexLogin_afterACodexResumeNeededIt_resendsThatSameResume() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        val requests = codexResumeNeedingSignIn(fx, vm)

        vm.setCodexApiKeyDraft("sk-user-key")
        vm.submitCodexLogin()
        advanceUntilIdle()

        assertEquals(2, requests.size)
        assertEquals(requests[0], requests[1])
        assertEquals("external-2222", requests[1].chat_resume_id)
        assertEquals("/srv/other", requests[1].cwd)
        assertFalse(vm.uiState.value.codexLoginVisible)
    }

    @Test
    fun dismissCodexLogin_forgetsThePendingResume_soALaterLoginStartsANewSession() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        val requests = codexResumeNeedingSignIn(fx, vm)

        vm.dismissCodexLogin()
        vm.setCodexApiKeyDraft("sk-user-key")
        vm.submitCodexLogin()
        advanceUntilIdle()

        assertEquals(2, requests.size)
        assertNull(requests[1].chat_resume_id)
        assertEquals("alpha", requests[1].project_name)
        assertEquals("codex", requests[1].cli)
    }

    // Antigravity chat view

    private val agyCli = """{"antigravity":{"available":true}}"""

    private fun TestScope.antigravitySubmit(
        fx: Fixture,
        setUp: (CreateSessionViewModel) -> Unit = {},
    ): Pair<CreateSessionRequest, AntigravityLaunchPrefs> {
        val vm = fx.buildVm()
        advanceUntilIdle()
        val requestSlot = slot<CreateSessionRequest>()
        coEvery { fx.sessionRepo.createSession(capture(requestSlot)) } returns
            SessionResponse("sess-agy-chat", "starting", "2026-11-09T00:00:00Z")
        val prefsSlot = slot<AntigravityLaunchPrefs>()
        coEvery { fx.prefs.setLastUsedAntigravityOptions(capture(prefsSlot)) } just Runs

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectAntigravityMode()
        setUp(vm)
        vm.submitAntigravity()
        advanceUntilIdle()
        return requestSlot.captured to prefsSlot.captured
    }

    @Test
    fun submitAntigravity_byDefault_opensTheChatView() = runTest {
        val (request, saved) = antigravitySubmit(newFixture())

        assertEquals("antigravity", request.cli)
        assertEquals(SessionResponse.UI_CHAT, request.ui)
        assertFalse(saved.terminalUi)
    }

    @Test
    fun submitAntigravity_withTerminalUi_opensTheTerminal_andRemembersTheChoice() = runTest {
        val (request, saved) = antigravitySubmit(newFixture()) { it.toggleAntigravityTerminalUi(true) }

        assertEquals(SessionResponse.UI_TERMINAL, request.ui)
        assertTrue(saved.terminalUi)
    }

    @Test
    fun submitAntigravity_whereThePcHasNoAntigravityChat_opensTheTerminal_andPointsToAgyUpdate() = runTest {
        val fx = newFixture()
        coEvery { fx.sessionRepo.getAgentVersion() } returns
            chatVersion("""{"claude":{"resume":true}}""", cli = agyCli)

        val (request, _) = antigravitySubmit(fx) { vm ->
            vm.refreshCliAvailability()
            advanceUntilIdle()
            assertEquals(
                "The chat view needs a newer Antigravity CLI on your PC: run agy update there.",
                vm.uiState.value.antigravityChatUnavailableHint,
            )
            assertFalse(vm.uiState.value.antigravityResumeAvailable)
        }

        assertEquals(SessionResponse.UI_TERMINAL, request.ui)
    }

    @Test
    fun antigravityResume_isOffered_onlyForTheChatView() = runTest {
        val fx = newFixture()
        coEvery { fx.sessionRepo.getAgentVersion() } returns
            chatVersion("""{"antigravity":{"resume":true,"live":false}}""", cli = agyCli)
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.refreshCliAvailability()
        advanceUntilIdle()

        assertTrue(vm.uiState.value.antigravityResumeAvailable)
        assertNull(vm.uiState.value.antigravityChatUnavailableHint)
        // Another agent's Terminal UI toggle must not hide the entry.
        vm.toggleCodexTerminalUi(true)
        assertTrue(vm.uiState.value.antigravityResumeAvailable)

        vm.toggleAntigravityTerminalUi(true)

        assertFalse(vm.uiState.value.antigravityResumeAvailable)
    }

    @Test
    fun init_restoresTheAntigravityTerminalUiChoice() = runTest {
        val fx = newFixture()
        coEvery { fx.prefs.getLastUsedAntigravityOptions() } returns AntigravityLaunchPrefs(terminalUi = true)

        val vm = fx.buildVm()
        advanceUntilIdle()

        assertTrue(vm.uiState.value.antigravityTerminalUi)
        assertFalse(vm.uiState.value.codexTerminalUi)
    }

    @Test
    fun resumeChatSession_antigravity_sendsTheAntigravityOptionsWithoutAWorktree() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        val requestSlot = slot<CreateSessionRequest>()
        coEvery { fx.sessionRepo.createSession(capture(requestSlot)) } returns
            SessionResponse("sess-agy-resumed", "starting", "2026-11-09T00:00:00Z")

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectAntigravityMode()
        vm.setAntigravityModel("Gemini 3.8 Flash (High)")
        vm.setAntigravitySandbox(true)
        vm.setAntigravityAddDirsDraft("/a, /b")
        vm.toggleWorktree(true)
        vm.setBranchName("feature/x")
        vm.resumeChatSession("antigravity", ChatStoredSession(session_id = "external-2222", cwd = "/p/alpha"))
        advanceUntilIdle()

        val request = requestSlot.captured
        assertEquals("antigravity", request.cli)
        assertEquals("alpha", request.project_name)
        assertNull(request.cwd)
        assertEquals(SessionResponse.UI_CHAT, request.ui)
        assertEquals("external-2222", request.chat_resume_id)
        assertNull(request.claude_options)
        assertNull(request.codex_options)
        val options = request.antigravity_options
        assertNotNull(options)
        assertEquals("Gemini 3.8 Flash (High)", options!!.model)
        assertTrue(options.sandbox)
        assertFalse(options.skip_permissions)
        assertEquals(listOf("/a", "/b"), options.add_dirs)
        assertFalse(options.worktree)
        assertNull(options.branch_name)
    }

    // ---- OpenCode -----------------------------------------------------------

    private fun TestScope.opencodeSubmit(
        fx: Fixture,
        configure: (CreateSessionViewModel) -> Unit = {},
    ): Pair<CreateSessionRequest, OpenCodeLaunchPrefs> {
        val vm = fx.buildVm()
        advanceUntilIdle()
        val requestSlot = slot<CreateSessionRequest>()
        val prefsSlot = slot<OpenCodeLaunchPrefs>()
        coEvery { fx.sessionRepo.createSession(capture(requestSlot)) } returns
            SessionResponse("sess-opencode", "starting", "2026-11-12T00:00:00Z")
        coEvery { fx.prefs.setLastUsedOpenCodeOptions(capture(prefsSlot)) } just Runs

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectOpenCodeMode()
        configure(vm)
        vm.submitOpenCode()
        advanceUntilIdle()
        return requestSlot.captured to prefsSlot.captured
    }

    @Test
    fun submitOpenCode_byDefault_sendsCliOpenCode_withoutAutoApprove_inTheChatView() = runTest {
        val fx = newFixture()
        val (request, saved) = opencodeSubmit(fx)

        assertEquals("alpha", request.project_name)
        assertEquals("opencode", request.cli)
        assertEquals(SessionResponse.UI_CHAT, request.ui)
        assertNull("OpenCode must not send claude_options", request.claude_options)
        assertNull("OpenCode must not send codex_options", request.codex_options)
        assertNull("OpenCode must not send antigravity_options", request.antigravity_options)
        val options = request.opencode_options
        assertNotNull(options)
        assertFalse("auto-approve is off unless the user turned it on", options!!.auto_approve)
        assertFalse(options.worktree)
        assertEquals(OpenCodeLaunchPrefs(autoApprove = false, terminalUi = false), saved)
        // An OpenCode submit must write no other agent's blob.
        coVerify(exactly = 0) { fx.prefs.setLastUsedClaudeOptions(any()) }
        coVerify(exactly = 0) { fx.prefs.setLastUsedCodexOptions(any()) }
        coVerify(exactly = 0) { fx.prefs.setLastUsedAntigravityOptions(any()) }
    }

    @Test
    fun submitOpenCode_withAutoApproveTerminalUiAndWorktree_sendsAndRemembersThem() = runTest {
        val (request, saved) = opencodeSubmit(newFixture()) { vm ->
            vm.setOpenCodeAutoApprove(true)
            vm.toggleOpenCodeTerminalUi(true)
            vm.toggleWorktree(true)
            vm.setBranchName("feature/x")
        }

        assertEquals(SessionResponse.UI_TERMINAL, request.ui)
        val options = request.opencode_options!!
        assertTrue(options.auto_approve)
        assertTrue("worktree must travel inside opencode_options", options.worktree)
        assertEquals("feature/x", options.branch_name)
        assertFalse("not the top-level request field", request.worktree)
        assertNull(request.branch_name)
        // Worktree and branch are per-session: only the reusable choices are kept.
        assertEquals(OpenCodeLaunchPrefs(autoApprove = true, terminalUi = true), saved)
    }

    @Test
    fun submitOpenCode_whereThePcHasNoOpenCodeChat_opensTheTerminal() = runTest {
        val fx = newFixture()
        coEvery { fx.sessionRepo.getAgentVersion() } returns chatVersion(
            chat = """{"claude":{"resume":true}}""",
            cli = """{"opencode":{"available":true}}""",
        )

        val (request, _) = opencodeSubmit(fx) { vm ->
            vm.refreshCliAvailability()
            advanceUntilIdle()
            assertNotNull(vm.uiState.value.opencodeChatUnavailableHint)
            assertFalse(vm.uiState.value.opencodeResumeAvailable)
        }

        assertEquals(SessionResponse.UI_TERMINAL, request.ui)
    }

    @Test
    fun submitOpenCode_customPath_sendsCwd_andNoGitIntent() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        val requestSlot = slot<CreateSessionRequest>()
        coEvery { fx.sessionRepo.createSession(capture(requestSlot)) } returns
            SessionResponse("sess-opencode-path", "starting", "2026-11-12T00:00:00Z")

        // A worktree choice left over from a git project must not follow the
        // user to a one-off folder.
        vm.toggleWorktree(true)
        vm.setBranchName("feature/x")
        vm.selectCustomPath()
        advanceUntilIdle()
        vm.setCustomPathDraft("/tmp/scratch")
        vm.setOpenCodeAutoApprove(true)
        vm.submitOpenCode()
        advanceUntilIdle()

        val request = requestSlot.captured
        assertNull(request.project_name)
        assertEquals("/tmp/scratch", request.cwd)
        assertEquals("opencode", request.cli)
        val options = request.opencode_options!!
        assertTrue(options.auto_approve)
        assertFalse(options.worktree)
        assertNull(options.branch_name)
        assertNull(options.base_branch)
    }

    @Test
    fun init_seedsOpenCodeControlsFromPersistedPrefs() = runTest {
        val fx = newFixture()
        coEvery { fx.prefs.getLastUsedOpenCodeOptions() } returns
            OpenCodeLaunchPrefs(autoApprove = true, terminalUi = true)

        val vm = fx.buildVm()
        advanceUntilIdle()

        val s = vm.uiState.value
        assertTrue(s.opencodeAutoApprove)
        assertTrue(s.opencodeTerminalUi)
        assertFalse("no bleed into another agent's bucket", s.antigravitySkipPermissions)
        assertFalse(s.codexTerminalUi)
    }

    @Test
    fun agentThatListsItsClisWithoutOpenCode_disablesTheCard_andRefusesTheMode() = runTest {
        // That agent predates the kind and would open a plain shell for it.
        val fx = newFixture()
        coEvery { fx.sessionRepo.getAgentVersion() } returns
            versionResponse(claude = true, codex = true)
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.selectProject("alpha")
        vm.confirmProject()

        vm.refreshCliAvailability()
        advanceUntilIdle()

        val s = vm.uiState.value
        assertFalse(s.opencodeKnown)
        assertNull(s.opencodeAvailable)
        assertFalse(s.opencodeEnabled)
        assertFalse(s.isModeEnabled(CreateSessionMode.OpenCode))
        assertTrue("the agent's own CLIs stay enabled", s.codexEnabled)

        vm.selectOpenCodeMode()

        assertEquals(CreateSessionStep.ModePick, vm.uiState.value.step)
    }

    @Test
    fun openCodeCard_followsTheAgentsAvailabilityFlag() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.sessionRepo.getAgentVersion() } returns chatVersion(
            chat = "{}", cli = """{"claude":{"available":true},"opencode":{"available":false}}""",
        )
        vm.refreshCliAvailability()
        advanceUntilIdle()
        assertTrue(vm.uiState.value.opencodeKnown)
        assertFalse("known but not installed", vm.uiState.value.opencodeEnabled)

        coEvery { fx.sessionRepo.getAgentVersion() } returns chatVersion(
            chat = "{}", cli = """{"opencode":{"available":true}}""",
        )
        vm.refreshCliAvailability()
        advanceUntilIdle()
        assertTrue(vm.uiState.value.opencodeEnabled)
    }

    @Test
    fun agentWithoutACliList_keepsOpenCodeOptimisticallyEnabled() = runTest {
        // capabilities: {} is an agent that reports nothing: unknown, not absent.
        val fx = newFixture()
        coEvery { fx.sessionRepo.getAgentVersion() } returns versionResponse()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.refreshCliAvailability()
        advanceUntilIdle()

        assertTrue(vm.uiState.value.opencodeKnown)
        assertTrue(vm.uiState.value.opencodeEnabled)
    }

    @Test
    fun resumeChatSession_opencode_sendsTheOpenCodeOptionsWithoutAWorktree() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        val requestSlot = slot<CreateSessionRequest>()
        coEvery { fx.sessionRepo.createSession(capture(requestSlot)) } returns
            SessionResponse("sess-opencode-resumed", "starting", "2026-11-12T00:00:00Z")

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectOpenCodeMode()
        vm.setOpenCodeAutoApprove(true)
        vm.toggleWorktree(true)
        vm.setBranchName("feature/x")
        vm.resumeChatSession("opencode", ChatStoredSession(session_id = "external-2222", cwd = "/p/alpha"))
        advanceUntilIdle()

        val request = requestSlot.captured
        assertEquals("opencode", request.cli)
        assertEquals("alpha", request.project_name)
        assertNull(request.cwd)
        assertEquals(SessionResponse.UI_CHAT, request.ui)
        assertEquals("external-2222", request.chat_resume_id)
        assertNull(request.claude_options)
        assertNull(request.codex_options)
        assertNull(request.antigravity_options)
        val options = request.opencode_options
        assertNotNull(options)
        assertTrue(options!!.auto_approve)
        assertFalse(options.worktree)
        assertNull(options.branch_name)
    }

    // ---- Worktree base branch ----------------------------------------------

    private fun worktreeBaseVersion(enabled: Boolean = true) = VersionInfoResponse(
        name = "mobaide_agent",
        server_version = "0.2.16",
        protocol_versions = listOf(7),
        min_client_version = 5,
        capabilities = Json.parseToJsonElement(
            if (enabled) """{"git":{"worktree_base":true}}""" else """{"git":{"branches":true}}""",
        ).jsonObject,
    )

    private fun bases(default: String?, lastUsed: String?, vararg refs: String) = WorktreeBasesResponse(
        default = default,
        last_used = lastUsed,
        refs = refs.map { WorktreeBaseRef(it, if ("/" in it) "remote" else "local") },
    )

    /** alpha (git) selected, ModePick probe run, worktree on. */
    private suspend fun TestScope.worktreeVm(
        fx: Fixture,
        capability: Boolean = true,
    ): CreateSessionViewModel {
        coEvery { fx.sessionRepo.getAgentVersion() } returns worktreeBaseVersion(capability)
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.selectProject("alpha")
        vm.confirmProject()
        vm.refreshCliAvailability()
        advanceUntilIdle()
        vm.toggleWorktree(true)
        advanceUntilIdle()
        return vm
    }

    private fun Fixture.captureCreate(): io.mockk.CapturingSlot<CreateSessionRequest> {
        val slot = slot<CreateSessionRequest>()
        coEvery { sessionRepo.createSession(capture(slot)) } returns
            SessionResponse(session_id = "s", state = "starting", created_at = "2026-11-09T00:00:00Z")
        return slot
    }

    @Test
    fun baseBranch_preselectsDefault_withoutLastUsed_andNullWithNeither() = runTest {
        val fx = newFixture(
            initialProjects = listOf(project("alpha"), project("gamma")),
        )
        coEvery { fx.gitRepo.worktreeBases("alpha") } returns bases("origin/main", null, "origin/main", "wip")
        coEvery { fx.gitRepo.worktreeBases("gamma") } returns bases(null, null, "wip")
        val vm = worktreeVm(fx)
        assertEquals("origin/main", vm.uiState.value.baseBranch)

        vm.selectProject("gamma")
        advanceUntilIdle()
        assertEquals("gamma", vm.uiState.value.baseBranchesProject)
        assertNull(vm.uiState.value.baseBranch)
        assertTrue(vm.uiState.value.baseBranches is BaseBranchesLoad.Loaded)
    }

    @Test
    fun baseBranch_projectChange_resetsManualPick_andReloads() = runTest {
        val fx = newFixture(
            initialProjects = listOf(project("alpha"), project("gamma")),
        )
        coEvery { fx.gitRepo.worktreeBases("alpha") } returns
            bases("origin/main", null, "origin/main", "feature/a")
        coEvery { fx.gitRepo.worktreeBases("gamma") } returns
            bases("origin/trunk", "origin/rel", "origin/trunk", "origin/rel")
        val vm = worktreeVm(fx)

        vm.selectBaseBranch("feature/a")
        assertEquals("feature/a", vm.uiState.value.baseBranch)

        vm.selectProject("gamma")
        assertTrue(vm.uiState.value.baseBranches is BaseBranchesLoad.Loading)
        assertFalse(vm.uiState.value.baseBranchPicked)
        advanceUntilIdle()
        assertEquals("origin/rel", vm.uiState.value.baseBranch)

        // Re-selecting the same project keeps the list and the pick.
        vm.selectBaseBranch(null)
        vm.selectProject("gamma")
        advanceUntilIdle()
        assertNull(vm.uiState.value.baseBranch)
        assertTrue(vm.uiState.value.baseBranchPicked)
        coVerify(exactly = 1) { fx.gitRepo.worktreeBases("gamma") }
    }

    @Test
    fun baseBranch_manualPick_survivesARefresh() = runTest {
        val fx = newFixture()
        coEvery { fx.gitRepo.worktreeBases("alpha") } returns
            bases("origin/main", "origin/develop", "origin/main", "origin/develop", "wip")
        val vm = worktreeVm(fx)

        vm.selectBaseBranch("wip")
        fx.gitChanged.emit("alpha")
        advanceUntilIdle()

        assertEquals("wip", vm.uiState.value.baseBranch)
        coVerify(exactly = 2) { fx.gitRepo.worktreeBases("alpha") }
    }

    @Test
    fun baseBranch_capabilityAbsent_hidesPicker_andNeverLoadsOrSends() = runTest {
        val fx = newFixture()
        val slot = fx.captureCreate()
        val vm = worktreeVm(fx, capability = false)

        assertFalse(vm.uiState.value.showBaseBranchPicker)
        assertEquals(BaseBranchesLoad.Idle, vm.uiState.value.baseBranches)
        vm.selectStandardMode()
        vm.submit()
        advanceUntilIdle()

        assertTrue(slot.captured.claude_options!!.worktree)
        assertNull(slot.captured.claude_options!!.base_branch)
        coVerify(exactly = 0) { fx.gitRepo.worktreeBases(any()) }
    }

    @Test
    fun baseBranch_loadFailure_showsFailed_sendsNull_andRetryRecovers() = runTest {
        val fx = newFixture()
        coEvery { fx.gitRepo.worktreeBases("alpha") } throws RuntimeException("offline")
        val slot = fx.captureCreate()
        val vm = worktreeVm(fx)

        assertEquals(BaseBranchesLoad.Failed, vm.uiState.value.baseBranches)
        assertNull(vm.uiState.value.effectiveBaseBranch)
        vm.selectStandardMode()
        vm.submit()
        advanceUntilIdle()
        assertNull(slot.captured.claude_options!!.base_branch)

        coEvery { fx.gitRepo.worktreeBases("alpha") } returns bases("origin/main", null, "origin/main")
        vm.retryBaseBranches()
        advanceUntilIdle()
        assertEquals("origin/main", vm.uiState.value.effectiveBaseBranch)
    }

    @Test
    fun baseBranch_isSentInTheActiveCliOptions() = runTest {
        val fx = newFixture()
        coEvery { fx.gitRepo.worktreeBases("alpha") } returns
            bases("origin/main", "origin/develop", "origin/main", "origin/develop")
        val slot = fx.captureCreate()
        val vm = worktreeVm(fx)

        vm.submit()
        advanceUntilIdle()
        assertEquals("origin/develop", slot.captured.claude_options!!.base_branch)
        assertNull(slot.captured.base_branch)

        vm.submitCodex()
        advanceUntilIdle()
        assertEquals("origin/develop", slot.captured.codex_options!!.base_branch)

        vm.selectBaseBranch("origin/main")
        vm.submitAntigravity()
        advanceUntilIdle()
        assertEquals("origin/main", slot.captured.antigravity_options!!.base_branch)
    }

    @Test
    fun baseBranch_terminalMode_isTopLevel() = runTest {
        val fx = newFixture()
        coEvery { fx.gitRepo.worktreeBases("alpha") } returns bases("origin/main", null, "origin/main")
        val slot = fx.captureCreate()
        val vm = worktreeVm(fx)

        vm.selectTerminalMode()
        vm.submitFromTerminalConfig()
        advanceUntilIdle()

        assertTrue(slot.captured.worktree)
        assertEquals("origin/main", slot.captured.base_branch)
        assertNull(slot.captured.claude_options)
    }

    @Test
    fun baseBranch_notSent_whenWorktreeOff_orForAutoScan() = runTest {
        val fx = newFixture()
        coEvery { fx.gitRepo.worktreeBases("alpha") } returns bases("origin/main", null, "origin/main")
        val slot = fx.captureCreate()
        val optsSlot = slot<ClaudeOptionsModel>()
        coEvery { fx.autoScanRepo.startScan("alpha", capture(optsSlot)) } just Runs
        val vm = worktreeVm(fx)

        vm.submitAutoScan()
        advanceUntilIdle()
        assertNull(optsSlot.captured.base_branch)

        vm.toggleWorktree(false)
        vm.submit()
        advanceUntilIdle()
        assertFalse(slot.captured.claude_options!!.worktree)
        assertNull(slot.captured.claude_options!!.base_branch)
    }
}
