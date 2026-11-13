package com.mobaide.app.ui.sessions

import app.cash.turbine.test
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.dto.CustomAgentTypeDto
import com.mobaide.app.data.network.dto.ProjectInfo
import com.mobaide.app.data.repository.AutoScanRepository
import com.mobaide.app.data.repository.CustomAgentTypesRepository
import com.mobaide.app.data.repository.GitRepository
import com.mobaide.app.data.repository.PathSuggestionRepository
import com.mobaide.app.data.repository.ProjectsRepository
import com.mobaide.app.data.repository.SessionRepository
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.Runs
import io.mockk.coEvery
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/**
 * Reordering in [CreateSessionViewModel]: the project rows save the agent's
 * own project order (`PATCH /api/projects/order`) and the mode cards save the
 * account's mode order through [CustomAgentTypesRepository]. Both show a move
 * at once; a failed save reverts and says so. Also pins [orderModes], the rule
 * shared with iOS `orderModes(_:by:)` and web `applyModeOrder`.
 *
 * Collaborators are MockK as in [CreateSessionViewModelTest]. The projects
 * fake answers a PATCH with the agent's re-sorted list, and the mode-order fake
 * shows a saved order at once, as the real repository does.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class CreateSessionViewModelReorderTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private fun project(name: String, order: Int): ProjectInfo =
        ProjectInfo(name = name, path = "/p/$name", is_git = true, order = order)

    private fun projects(vararg names: String): List<ProjectInfo> =
        names.mapIndexed { index, name -> project(name, index) }

    private fun customType(id: String): CustomAgentTypeDto = CustomAgentTypeDto(
        id = id,
        name = "Agent $id",
        command = "run-$id",
        created_at = "2026-07-28T12:00:00Z",
        updated_at = "2026-07-28T12:00:00Z",
    )

    private val hostAbsolutePath =
        if (System.getProperty("os.name").orEmpty().lowercase().contains("win")) {
            "C:/Work/sandbox"
        } else {
            "/srv/work"
        }

    private class Fixture(
        val projectsRepo: ProjectsRepository,
        val projectsFlow: MutableStateFlow<List<ProjectInfo>>,
        val customTypesRepo: CustomAgentTypesRepository,
        val customTypesFlow: MutableStateFlow<List<CustomAgentTypeDto>>,
        val modeOrderFlow: MutableStateFlow<List<String>>,
        /** Every name list PATCHed, in the order the agent received them. */
        val projectSaves: MutableList<List<String>>,
        /** Every mode id list PUT, in call order. */
        val modeSaves: MutableList<List<String>>,
        val vm: CreateSessionViewModel,
    )

    private fun newFixture(
        initialProjects: List<ProjectInfo> = projects("alpha", "beta", "gamma"),
        customTypes: List<CustomAgentTypeDto> = emptyList(),
        modeOrder: List<String> = emptyList(),
    ): Fixture {
        val projectsRepo = mockk<ProjectsRepository>(relaxed = true)
        val sessionRepo = mockk<SessionRepository>(relaxed = true)
        val autoScanRepo = mockk<AutoScanRepository>(relaxed = true)
        val prefs = mockk<PreferencesStore>(relaxed = true)
        val tokenStore = mockk<TokenStore>(relaxed = true)
        val projectsFlow = MutableStateFlow(initialProjects)
        val projectSaves = mutableListOf<List<String>>()
        val modeSaves = mutableListOf<List<String>>()

        every { projectsRepo.observeProjects() } returns projectsFlow
        coEvery { projectsRepo.listProjects() } returns initialProjects
        coEvery { projectsRepo.reorderProjects(any()) } coAnswers {
            val names = firstArg<List<String>>()
            projectSaves += names
            names.mapIndexed { index, name -> project(name, index) }
        }
        coEvery { prefs.getLastUsedProjectName() } returns null
        coEvery { prefs.getLastUsedClaudeOptions() } returns null
        coEvery { prefs.getLastUsedAutoScanOptions() } returns null
        coEvery { prefs.getLastUsedCodexOptions() } returns null
        coEvery { prefs.getLastUsedAntigravityOptions() } returns null
        coEvery { prefs.getRecentCustomPaths(any()) } returns emptyList()
        every { autoScanRepo.getAutoScanPrompt() } returns "default auto scan prompt"
        every { tokenStore.getAgentId() } returns "agent-1"
        every { tokenStore.getAgentUrl() } returns null

        val customTypesFlow = MutableStateFlow(customTypes)
        val modeOrderFlow = MutableStateFlow(modeOrder)
        val customTypesRepo = mockk<CustomAgentTypesRepository>()
        every { customTypesRepo.observeTypes() } returns customTypesFlow
        every { customTypesRepo.observeModeOrder() } returns modeOrderFlow
        coEvery { customTypesRepo.refreshTypes() } just Runs
        coEvery { customTypesRepo.refreshModeOrder() } just Runs
        coEvery { customTypesRepo.saveModeOrder(any()) } coAnswers {
            val ids = firstArg<List<String>>()
            modeSaves += ids
            modeOrderFlow.value = ids
        }

        val restApi = mockk<RestApi>()
        val vm = CreateSessionViewModel(
            projectsRepo, sessionRepo, autoScanRepo, prefs, tokenStore, customTypesRepo,
            PathSuggestionRepository(restApi, tokenStore),
            mockk<GitRepository>(relaxed = true) {
                every { changed } returns MutableSharedFlow()
            },
        )
        return Fixture(
            projectsRepo, projectsFlow, customTypesRepo, customTypesFlow, modeOrderFlow,
            projectSaves, modeSaves, vm,
        )
    }

    private val Fixture.projectNames: List<String>
        get() = vm.uiState.value.projects.map { it.name }

    private val Fixture.modeIds: List<String>
        get() = vm.uiState.value.orderedModes.map { it.orderId }


    private val defaults = BUILT_IN_MODES + listOf(
        CreateSessionMode.Custom("aaaaaaaaaaaa"),
        CreateSessionMode.Custom("bbbbbbbbbbbb"),
    )

    @Test
    fun orderModes_ranksNamedModes_thenUnnamedInDefaultOrder() {
        // A custom type created after the last reorder, or a built-in a newer
        // client added, goes after everything the user arranged.
        val ordered = orderModes(defaults, listOf("custom_bbbbbbbbbbbb", "shell", "codex"))

        assertEquals(
            listOf(
                "custom_bbbbbbbbbbbb", "shell", "codex",
                "claude", "antigravity", "opencode", "copilot", "cursor", "auto_scan", "custom_aaaaaaaaaaaa",
            ),
            ordered.map { it.orderId },
        )
    }

    @Test
    fun orderModes_skipsIdsThatNameNoMode() {
        val ordered = orderModes(BUILT_IN_MODES, listOf("custom_deleted0000", "auto_scan", "future_mode"))

        assertEquals(
            listOf("auto_scan", "claude", "codex", "antigravity", "opencode", "copilot", "cursor", "shell"),
            ordered.map { it.orderId },
        )
    }

    @Test
    fun orderId_matchesTheWireIdsOfEveryClient() {
        assertEquals(
            listOf(
                "claude", "codex", "antigravity", "opencode", "copilot", "cursor", "auto_scan", "shell",
                "custom_ab12cd34ef56",
            ),
            (BUILT_IN_MODES + CreateSessionMode.Custom("ab12cd34ef56")).map { it.orderId },
        )
    }


    @Test
    fun orderedModes_followsTheAccountOrder_fromTheRepository() = runTest {
        val f = newFixture(customTypes = listOf(customType("t1"), customType("t2")))
        advanceUntilIdle()
        assertEquals(
            listOf(
                "claude", "codex", "antigravity", "opencode", "copilot", "cursor", "auto_scan", "shell",
                "custom_t1", "custom_t2",
            ),
            f.modeIds,
        )

        // A `session_mode_order.updated` push from another device.
        f.modeOrderFlow.value = listOf("custom_t2", "shell")
        advanceUntilIdle()

        assertEquals(
            listOf(
                "custom_t2", "shell", "claude", "codex", "antigravity", "opencode", "copilot", "cursor", "auto_scan",
                "custom_t1",
            ),
            f.modeIds,
        )
    }

    @Test
    fun moveMode_savesTheWholeIdList_andShowsIt() = runTest {
        val f = newFixture(customTypes = listOf(customType("t1")))
        advanceUntilIdle()

        f.vm.moveMode(8, 0)
        advanceUntilIdle()

        val expected = listOf(
            "custom_t1", "claude", "codex", "antigravity", "opencode", "copilot", "cursor", "auto_scan", "shell",
        )
        assertEquals(listOf(expected), f.modeSaves)
        assertEquals(expected, f.modeIds)
    }

    @Test
    fun moveMode_outOfRangeOrInPlace_savesNothing() = runTest {
        val f = newFixture()
        advanceUntilIdle()

        f.vm.moveMode(0, 0)
        f.vm.moveMode(0, 8)
        f.vm.moveMode(-1, 0)
        advanceUntilIdle()

        assertTrue(f.modeSaves.isEmpty())
    }

    @Test
    fun reorderModes_failure_saysReorderFailed() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        coEvery { f.customTypesRepo.saveModeOrder(any()) } throws ApiException(503, "")

        f.vm.transientMessages.test {
            f.vm.moveMode(0, 1)
            advanceUntilIdle()
            assertEquals("Reorder failed — reverted.", awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
    }


    @Test
    fun moveProject_showsTheMoveAtOnce_andSavesTheWholeNameList() = runTest {
        val f = newFixture()
        advanceUntilIdle()

        f.vm.moveProject(0, 2)
        assertEquals(listOf("beta", "gamma", "alpha"), f.projectNames)

        advanceUntilIdle()
        assertEquals(listOf(listOf("beta", "gamma", "alpha")), f.projectSaves)
        assertEquals(listOf("beta", "gamma", "alpha"), f.projectNames)
    }

    @Test
    fun moveProject_failure_revertsToTheAgentOrder_andSaysSo() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        coEvery { f.projectsRepo.reorderProjects(any()) } throws ApiException(503, "")

        f.vm.transientMessages.test {
            f.vm.moveProject(2, 0)
            assertEquals(listOf("gamma", "alpha", "beta"), f.projectNames)

            advanceUntilIdle()
            assertEquals("Reorder failed — reverted.", awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
        assertEquals(listOf("alpha", "beta", "gamma"), f.projectNames)
    }

    @Test
    fun moveProject_anUnrelatedPushWhileSaving_doesNotSnapTheRowsBack() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        val answer = CompletableDeferred<List<ProjectInfo>>()
        coEvery { f.projectsRepo.reorderProjects(any()) } coAnswers { answer.await() }

        f.vm.moveProject(0, 1)
        advanceUntilIdle()

        // `projects.changed` from another device, still in the old order.
        f.projectsFlow.value = projects("alpha", "beta", "gamma").map {
            if (it.name == "gamma") it.copy(has_linear_key = true) else it
        }
        advanceUntilIdle()
        assertEquals(listOf("beta", "alpha", "gamma"), f.projectNames)

        answer.complete(projects("beta", "alpha", "gamma"))
        advanceUntilIdle()
        assertEquals(listOf("beta", "alpha", "gamma"), f.projectNames)
    }

    @Test
    fun moveProject_twoQuickDrops_saveInOrder_andTheFirstAnswerKeepsTheSecondDrop() = runTest {
        val f = newFixture()
        advanceUntilIdle()
        val firstAnswer = CompletableDeferred<List<ProjectInfo>>()
        val sent = mutableListOf<List<String>>()
        coEvery { f.projectsRepo.reorderProjects(any()) } coAnswers {
            val names = firstArg<List<String>>()
            sent += names
            if (sent.size == 1) firstAnswer.await() else projects(*names.toTypedArray())
        }

        f.vm.moveProject(0, 1)
        advanceUntilIdle()
        f.vm.moveProject(2, 0)
        advanceUntilIdle()

        assertEquals(listOf(listOf("beta", "alpha", "gamma")), sent)
        assertEquals(listOf("gamma", "beta", "alpha"), f.projectNames)

        firstAnswer.complete(projects("beta", "alpha", "gamma"))
        advanceUntilIdle()

        assertEquals(
            listOf(listOf("beta", "alpha", "gamma"), listOf("gamma", "beta", "alpha")),
            sent,
        )
        assertEquals(listOf("gamma", "beta", "alpha"), f.projectNames)
    }


    @Test
    fun selectCustomPath_again_keepsTheTypedDraft() = runTest {
        // The "Custom path…" row stays on screen beside the project list, so
        // a second tap must not wipe a path the user has typed.
        val f = newFixture()
        advanceUntilIdle()
        f.vm.selectCustomPath()
        advanceUntilIdle()
        f.vm.setCustomPathDraft(hostAbsolutePath)

        f.vm.selectCustomPath()
        advanceUntilIdle()

        val s = f.vm.uiState.value
        assertEquals(hostAbsolutePath, s.customPathDraft)
        assertEquals(ProjectSelection.CustomPath(hostAbsolutePath), s.selection)
    }
}
