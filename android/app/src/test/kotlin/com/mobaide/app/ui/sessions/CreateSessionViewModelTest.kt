package com.mobaide.app.ui.sessions

import app.cash.turbine.test
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.local.ClaudeLaunchPrefs
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.dto.ClaudeOptionsModel
import com.mobaide.app.data.network.dto.CodexLoginResponse
import com.mobaide.app.data.network.dto.CreateSessionRequest
import com.mobaide.app.data.network.dto.ProjectInfo
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.data.network.dto.VersionInfoResponse
import com.mobaide.app.data.repository.AutoScanRepository
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
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
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
        coEvery { prefs.getRecentCustomPaths(any()) } returns recentCustomPaths
        coEvery { prefs.addRecentCustomPath(any(), any()) } just Runs
        every { autoScanRepo.getAutoScanPrompt() } returns autoScanPrompt
        every { tokenStore.getAgentId() } returns agentId
        every { tokenStore.getAgentUrl() } returns agentUrl

        return Fixture(projectsRepo, sessionRepo, autoScanRepo, prefs, tokenStore, projectsFlow)
    }

    private fun Fixture.buildVm(): CreateSessionViewModel =
        CreateSessionViewModel(projectsRepo, sessionRepo, autoScanRepo, prefs, tokenStore)

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
    fun confirmProject_doesNothing_whenNoProjectSelected() = runTest {
        val fx = newFixture(initialProjects = emptyList())
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.confirmProject()
        assertEquals(CreateSessionStep.ProjectSelect, vm.uiState.value.step)
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

    @Test
    fun selectProject_gitProject_preservesExistingWorktreeAndBranch() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.toggleWorktree(true)
        vm.setBranchName("main")
        vm.selectProject("alpha") // is_git = true

        assertTrue(vm.uiState.value.worktree)
        assertEquals("main", vm.uiState.value.branchName)
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
        assertTrue(opts.skip_permissions)
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
        vm.submit()
        advanceUntilIdle()

        val opts = requestSlot.captured.claude_options
        assertNotNull(opts)
        assertEquals("opus", opts!!.model)
        assertEquals("max", opts.effort)
        assertTrue(opts.ultracode)
        assertEquals("be terse", opts.append_system_prompt)
        // The same selections are persisted for the next launch.
        assertEquals("opus", prefsSlot.captured.model)
        assertEquals("max", prefsSlot.captured.effort)
        assertTrue(prefsSlot.captured.ultracode)
        assertEquals("be terse", prefsSlot.captured.appendSystemPrompt)
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
        // sonnet doesn't support xhigh/max, so the invalid level must clamp.
        vm.setClaudeModel("sonnet")

        assertEquals("high", vm.uiState.value.claudeEffort)
    }

    @Test
    fun init_seedsClaudeControlsFromPersistedPrefs() = runTest {
        val fx = newFixture()
        coEvery { fx.prefs.getLastUsedClaudeOptions() } returns
            ClaudeLaunchPrefs(model = "fable", effort = "xhigh", ultracode = true)
        val vm = fx.buildVm()
        advanceUntilIdle()

        val s = vm.uiState.value
        assertEquals("fable", s.claudeModel)
        assertEquals("xhigh", s.claudeEffort)
        assertTrue(s.claudeUltracode)
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
    fun submit_noSelectedProject_isNoop() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.submit()
        advanceUntilIdle()

        coVerify(exactly = 0) { fx.sessionRepo.createSession(any()) }
        assertFalse(vm.uiState.value.submitting)
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
    fun selectAutoScanMode_firstCall_seedsPresetDefaults() = runTest {
        val fx = newFixture(autoScanPrompt = "scan everything")
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectAutoScanMode()

        val s = vm.uiState.value
        assertEquals(CreateSessionStep.AutoScanConfig, s.step)
        assertTrue("AutoScan preset must enable worktree", s.worktree)
        assertEquals("scan everything", s.initialPrompt)
        assertEquals("alpha", s.autoScanConfigInitializedFor)
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
        vm.toggleNoMemory(true)
        vm.goBack()
        vm.selectAutoScanMode() // same project → must NOT re-seed (preserve edits)

        val s = vm.uiState.value
        assertEquals("user edit", s.initialPrompt)
        assertTrue(s.noMemory)
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
    fun selectAutoScanMode_firstCall_seedsPromptFromSelectedProjectsSavedPrompt() = runTest {
        // The per-project saved prompt (delivered on ProjectInfo.auto_scan_prompt)
        // takes precedence over the agent-wide default when seeding the field.
        val fx = newFixture(
            initialProjects = listOf(
                ProjectInfo(
                    name = "alpha",
                    path = "/p/alpha",
                    is_git = true,
                    auto_scan_prompt = "project saved prompt",
                ),
            ),
            autoScanPrompt = "agent wide default",
        )
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectAutoScanMode()

        assertEquals("project saved prompt", vm.uiState.value.initialPrompt)
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
        vm.selectAutoScanMode() // seeds worktree=true, initialPrompt="seed"

        vm.navigateToAutoScan.test {
            vm.submitAutoScan()
            advanceUntilIdle()

            assertEquals("alpha", awaitItem())
            cancelAndIgnoreRemainingEvents()
        }

        val opts = optsSlot.captured
        assertTrue(opts.skip_permissions)
        assertEquals("Auto Scan effort defaults to high", "high", opts.effort)
        assertNull(opts.model)
        assertTrue(opts.worktree)
        assertNull(opts.branch_name)
        assertEquals("seed", opts.initial_prompt)
        coVerify(exactly = 1) { fx.prefs.setLastUsedProjectName("alpha") }
        coVerify(exactly = 1) { fx.prefs.setLastUsedClaudeOptions(any()) }
        assertFalse(vm.uiState.value.autoScanStarting)
    }

    @Test
    fun submitAutoScan_withClaudeReasoningOptions_sendsThem() = runTest {
        val fx = newFixture(autoScanPrompt = "seed")
        val vm = fx.buildVm()
        advanceUntilIdle()

        val optsSlot = slot<ClaudeOptionsModel>()
        coEvery { fx.autoScanRepo.startScan("alpha", capture(optsSlot)) } just Runs

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectAutoScanMode()
        vm.setClaudeModel("opus")
        vm.setClaudeEffort("xhigh")
        vm.submitAutoScan()
        advanceUntilIdle()

        assertEquals("opus", optsSlot.captured.model)
        assertEquals("xhigh", optsSlot.captured.effort)
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
    fun onProjectCreated_gitProject_selectsByNameAndPreservesWorktreeAndBranch() = runTest {
        // Why `onProjectCreated` exists rather than reusing
        // `selectProject(name)`. The new project is NOT yet in `state.projects`
        // (the `AgentEvent.ProjectsChanged` bus event arrives asynchronously),
        // so `selectProject(name)`'s lookup would default `is_git` to `false`
        // via `firstOrNull()?.is_git == true` and incorrectly clear worktree
        // for a real git project. `onProjectCreated` reads `is_git` off the
        // passed `ProjectInfo` directly.
        val fx = newFixture(initialProjects = emptyList())  // simulate bus lag
        val vm = fx.buildVm()
        advanceUntilIdle()
        // User had configured a worktree before opening the create-project
        // sheet (e.g. switched modes). Their settings must survive selection.
        vm.toggleWorktree(true)
        vm.setBranchName("feature/x")

        vm.onProjectCreated(project("gamma", isGit = true))

        val s = vm.uiState.value
        assertEquals("gamma", s.selectedProjectName)
        assertTrue(
            "Worktree must be preserved when the newly-created project is git",
            s.worktree,
        )
        assertEquals("feature/x", s.branchName)
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

    @Test
    fun onProjectCreated_doesNotChangeWizardStep() = runTest {
        // After the sheet closes the user remains on the
        // ProjectSelect step — the new project is auto-selected in the
        // dropdown, but the wizard does NOT advance. Pinning this so a
        // future "auto-confirm on create" tweak surfaces as a test failure
        // rather than silently changing UX.
        val fx = newFixture(initialProjects = emptyList())
        val vm = fx.buildVm()
        advanceUntilIdle()
        assertEquals(CreateSessionStep.ProjectSelect, vm.uiState.value.step)

        vm.onProjectCreated(project("gamma", isGit = true))

        assertEquals(CreateSessionStep.ProjectSelect, vm.uiState.value.step)
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
    fun pickMode_CustomShell_onlySelectsMode_doesNotSubmit() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.selectProject("alpha")
        vm.confirmProject()
        assertEquals(CreateSessionStep.ModePick, vm.uiState.value.step)

        vm.pickMode(CreateSessionMode.CustomShell)
        advanceUntilIdle()

        // pickMode no longer auto-submits Terminal mode — Continue
        // (selectTerminalMode) drives the next step now. Picking only records
        // the selected mode; the wizard stays on ModePick and no session is
        // created.
        assertEquals(CreateSessionStep.ModePick, vm.uiState.value.step)
        assertEquals(CreateSessionMode.CustomShell, vm.uiState.value.selectedMode)
        coVerify(exactly = 0) { fx.sessionRepo.createSession(any()) }
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

    @Test
    fun submitCustomShell_noSelectedProject_isNoop() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.submitCustomShell()
        advanceUntilIdle()

        coVerify(exactly = 0) { fx.sessionRepo.createSession(any()) }
        assertFalse(vm.uiState.value.submitting)
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
    fun selectCustomPath_noScopeKey_skipsRecentsLoad() = runTest {
        // No agent_id AND no agent_url ⇒ the VM must not call
        // `getRecentCustomPaths(null)` — it simply skips the load. The
        // panel renders without chips and the user can still type a path.
        val fx = newFixture(agentId = null, agentUrl = null)
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.selectCustomPath()
        advanceUntilIdle()

        assertTrue(vm.uiState.value.selection is ProjectSelection.CustomPath)
        coVerify(exactly = 0) { fx.prefs.getRecentCustomPaths(any()) }
        assertTrue(vm.uiState.value.recentCustomPaths.isEmpty())
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
    fun pickRecentCustomPath_delegatesToSetCustomPathDraft() = runTest {
        // Tapping a recents chip must produce the same state as typing
        // the path manually — including validation pass-through.
        val fx = newFixture(recentCustomPaths = listOf(hostAbsolutePath))
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.selectCustomPath()
        advanceUntilIdle()

        vm.pickRecentCustomPath(hostAbsolutePath)

        val s = vm.uiState.value
        assertEquals(hostAbsolutePath, s.customPathDraft)
        assertNull(s.customPathError)
        assertEquals(hostAbsolutePath, (s.selection as ProjectSelection.CustomPath).path)
    }

    @Test
    fun confirmProject_customPathValid_advancesToModePick() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.selectCustomPath()
        advanceUntilIdle()
        vm.setCustomPathDraft(hostAbsolutePath)

        vm.confirmProject()

        assertEquals(CreateSessionStep.ModePick, vm.uiState.value.step)
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
    fun submit_storedProject_regression_stillCallsLastUsedProjectName() = runTest {
        // Regression guard for the dispatch matrix: the sealed-interface
        // refactor must not break the `Stored` branch — `setLastUsedProjectName`
        // is still called, `addRecentCustomPath` is still NOT.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.sessionRepo.createSession(any()) } returns
            SessionResponse("sess-stored", "starting", "2026-05-20T00:00:00Z")

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectStandardMode()
        vm.submit()
        advanceUntilIdle()

        coVerify(exactly = 1) { fx.prefs.setLastUsedProjectName("alpha") }
        coVerify(exactly = 0) { fx.prefs.addRecentCustomPath(any(), any()) }
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
    fun wizardFlow_ModePick_to_CodexConfig_andBack() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.pickMode(CreateSessionMode.Codex)
        vm.selectCodexMode()
        assertEquals(CreateSessionStep.CodexConfig, vm.uiState.value.step)

        vm.goBack()
        assertEquals(CreateSessionStep.ModePick, vm.uiState.value.step)
    }

    @Test
    fun defaultCodexState_derivesAutoPreset() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()
        assertEquals(CodexAutonomyPreset.Auto, vm.uiState.value.codexPreset)
    }

    @Test
    fun selectCodexPreset_fullAccess_setsFullAuto_andDerivesFullAccess() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.selectCodexPreset(CodexAutonomyPreset.FullAccess)
        assertTrue(vm.uiState.value.codexFullAuto)
        assertEquals(CodexAutonomyPreset.FullAccess, vm.uiState.value.codexPreset)
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
        assertFalse(vm.uiState.value.submitting)
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

    @Test
    fun submitCodex_readOnlyPreset_sendsReadOnlySandbox() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val requestSlot = slot<CreateSessionRequest>()
        coEvery { fx.sessionRepo.createSession(capture(requestSlot)) } returns
            SessionResponse("sess-ro", "starting", "2026-06-23T00:00:00Z")

        vm.selectProject("alpha")
        vm.selectCodexPreset(CodexAutonomyPreset.ReadOnly)
        vm.submitCodex()
        advanceUntilIdle()

        val opts = requestSlot.captured.codex_options
        assertNotNull(opts)
        assertEquals("read-only", opts!!.sandbox)
        assertEquals("on-request", opts.approval)
        assertFalse(opts.full_auto)
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
    fun submitCodex_non401Error_setsGenericError_doesNotOpenDialog() = runTest {
        // A 500 (or any non-401) is not the auth case -> generic error, no dialog.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.sessionRepo.createSession(any()) } throws
            ApiException(500, """{"detail":"boom"}""")

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.selectCodexMode()
        vm.submitCodex()
        advanceUntilIdle()

        val s = vm.uiState.value
        assertFalse("non-auth failure must not open the login dialog", s.codexLoginVisible)
        assertNotNull("non-auth failure must surface the generic error", s.error)
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
    fun submitCodexLogin_blankKey_isNoop() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.setCodexApiKeyDraft("   ")
        vm.submitCodexLogin()
        advanceUntilIdle()

        coVerify(exactly = 0) { fx.sessionRepo.codexLogin(any()) }
        assertFalse(vm.uiState.value.codexLoginSubmitting)
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
     * per-CLI availability flags, mirroring the agent's wire shape. A `null`
     * flag omits that id (the "key absent" case); the whole `cli` map is omitted
     * when both are null so the response models an old agent's `capabilities:{}`.
     */
    private fun versionResponse(
        claude: Boolean? = null,
        codex: Boolean? = null,
    ): VersionInfoResponse {
        val entries = buildList {
            if (claude != null) add("\"claude\":{\"available\":$claude}")
            if (codex != null) add("\"codex\":{\"available\":$codex}")
        }
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
    fun refreshCliAvailability_bothInstalled_setsBothAvailableTrue() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.sessionRepo.getAgentVersion() } returns
            versionResponse(claude = true, codex = true)

        vm.refreshCliAvailability()
        advanceUntilIdle()

        val s = vm.uiState.value
        assertEquals(true, s.claudeAvailable)
        assertEquals(true, s.codexAvailable)
        assertFalse("loading flag must reset after the probe", s.cliAvailabilityLoading)
        assertTrue(s.claudeEnabled)
        assertTrue(s.codexEnabled)
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
    fun selectStandardMode_claudeUnavailable_isNoop_staysOnModePick() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.sessionRepo.getAgentVersion() } returns
            versionResponse(claude = false, codex = true)

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.refreshCliAvailability()
        advanceUntilIdle()
        assertEquals(CreateSessionStep.ModePick, vm.uiState.value.step)

        vm.selectStandardMode()

        // Guard: a disabled Claude must not advance to StandardConfig even if a
        // racing tap slips past the grayed-out card.
        assertEquals(CreateSessionStep.ModePick, vm.uiState.value.step)
    }

    @Test
    fun selectCodexMode_codexUnavailable_isNoop_staysOnModePick() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.sessionRepo.getAgentVersion() } returns
            versionResponse(claude = true, codex = false)

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.refreshCliAvailability()
        advanceUntilIdle()

        vm.selectCodexMode()

        assertEquals(CreateSessionStep.ModePick, vm.uiState.value.step)
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
    fun selectStandardMode_claudeAvailableTrue_advancesNormally() = runTest {
        // Regression guard: the guard must only block on an explicit false —
        // an available (true) claude must still advance to StandardConfig.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        coEvery { fx.sessionRepo.getAgentVersion() } returns
            versionResponse(claude = true, codex = true)

        vm.selectProject("alpha")
        vm.confirmProject()
        vm.refreshCliAvailability()
        advanceUntilIdle()

        vm.selectStandardMode()

        assertEquals(CreateSessionStep.StandardConfig, vm.uiState.value.step)
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
    fun claudeEffortsFor_fable_offersFullLadder() {
        // fable is the other xhigh-capable model; the clamp test only covers
        // opus, so pin fable's full ladder explicitly.
        assertEquals(
            CreateSessionViewModel.CLAUDE_EFFORTS,
            CreateSessionViewModel.claudeEffortsFor("fable"),
        )
    }

    @Test
    fun claudeEffortsFor_sonnet_capsAtHigh_neverOffersDownshiftLevels() {
        val efforts = CreateSessionViewModel.claudeEffortsFor("sonnet")
        assertEquals(listOf("low", "medium", "high"), efforts)
        assertFalse("sonnet must never be offered xhigh", efforts.contains("xhigh"))
        assertFalse("sonnet must never be offered max", efforts.contains("max"))
    }

    @Test
    fun claudeEffortsFor_haiku_capsAtHigh() {
        assertEquals(
            listOf("low", "medium", "high"),
            CreateSessionViewModel.claudeEffortsFor("haiku"),
        )
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

    @Test
    fun claudeEffortsFor_everyCuratedModel_returnsSubsetOfCanonicalLadder() {
        // Invariant: whatever a curated model offers is always a subset of the
        // canonical ladder, so the dropdown can never present a level the ladder
        // doesn't define. Iterating CLAUDE_MODELS holds any newly-added alias to
        // this contract automatically.
        for (model in CreateSessionViewModel.CLAUDE_MODELS) {
            val efforts = CreateSessionViewModel.claudeEffortsFor(model)
            assertTrue(
                "claudeEffortsFor($model) must be a subset of CLAUDE_EFFORTS",
                CreateSessionViewModel.CLAUDE_EFFORTS.containsAll(efforts),
            )
        }
    }
}
