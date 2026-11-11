package com.mobaide.app.ui.sessions

import androidx.activity.ComponentActivity
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.network.dto.AutoScanListResponse
import com.mobaide.app.data.network.dto.CustomAgentTypeDto
import com.mobaide.app.data.network.dto.SessionKind
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.data.repository.AgentUpdateRepository
import com.mobaide.app.data.repository.AgentVersionMonitor
import com.mobaide.app.data.repository.AutoScanRepository
import com.mobaide.app.data.repository.CustomAgentTypesRepository
import com.mobaide.app.data.repository.SessionRepository
import com.mobaide.app.data.review.ReviewPromptCoordinator
import com.mobaide.app.ui.sessions.panel.STOP_SESSION_DIALOG_FORCE_TAG
import com.mobaide.app.ui.sessions.panel.STOP_SESSION_DIALOG_STOP_TAG
import com.mobaide.app.ui.theme.MobaideTheme
import io.mockk.Runs
import io.mockk.coEvery
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w360dp-h800dp-xxhdpi")
class SessionListScreenTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    private val stops = mutableListOf<Pair<String, Boolean>>()

    private fun session(id: String, project: String, kind: SessionKind): SessionResponse =
        SessionResponse(
            session_id = id,
            state = "running",
            created_at = "2026-01-01T00:00:00Z",
            project_name = project,
            kind = kind,
        )

    private fun newViewModel(sessions: List<SessionResponse>): SessionListViewModel {
        val sessionRepo = mockk<SessionRepository>(relaxed = true)
        every { sessionRepo.observeSessionList() } returns MutableStateFlow(sessions)
        every { sessionRepo.observeSessionsSynced() } returns MutableStateFlow(true)
        every { sessionRepo.endedSessionNotices } returns MutableSharedFlow<String>()
        coEvery { sessionRepo.deleteSession(any(), any()) } answers {
            stops += firstArg<String>() to secondArg<Boolean>()
        }

        val scanList = AutoScanListResponse(
            scans = emptyList(),
            capacity = 0,
            max_scans = 5,
            available_projects = emptyList(),
        )
        val autoScanRepo = mockk<AutoScanRepository>(relaxed = true)
        every { autoScanRepo.observeList() } returns MutableStateFlow(scanList)
        coEvery { autoScanRepo.refresh() } returns scanList

        val prefs = mockk<PreferencesStore>(relaxed = true)
        coEvery { prefs.getLastUsedSessionId() } returns null

        val bus = mockk<AgentEventBus>(relaxed = true)
        every { bus.connection } returns MutableStateFlow(AgentEventBus.ConnState.Connected)
        every { bus.start() } just Runs

        val versionMonitor = mockk<AgentVersionMonitor>(relaxed = true)
        every { versionMonitor.outdated } returns MutableStateFlow<AgentVersionMonitor.Outdated?>(null)
        coEvery { versionMonitor.probe() } returns true

        val customTypesRepo = mockk<CustomAgentTypesRepository>()
        every { customTypesRepo.observeTypes() } returns
            MutableStateFlow<List<CustomAgentTypeDto>>(emptyList())
        coEvery { customTypesRepo.refreshTypes() } just Runs

        return SessionListViewModel(
            sessionRepo, autoScanRepo, prefs, bus, versionMonitor,
            mockk<AgentUpdateRepository>(relaxed = true), customTypesRepo,
            mockk<ReviewPromptCoordinator>(relaxed = true),
        )
    }

    private fun setScreen() {
        val vm = newViewModel(
            listOf(
                session("s1", project = "app", kind = SessionKind.Claude),
                session("s2", project = "api", kind = SessionKind.Codex),
            ),
        )
        composeRule.setContent {
            MobaideTheme(darkTheme = true) {
                SessionListScreen(
                    viewModel = vm,
                    onSessionClick = {},
                    onCreateClick = {},
                    onSettingsClick = {},
                )
            }
        }
    }

    private fun tapCardStop(index: Int) {
        composeRule.onAllNodesWithContentDescription("Stop session")[index].performClick()
        composeRule.waitForIdle()
    }

    @Test
    fun cardStop_opensTheDialogForThatSession_andStopsNothing() {
        setScreen()

        tapCardStop(1)

        composeRule.onNodeWithText(
            "Stop \"Codex · api\"? Force Stop kills the process immediately.",
        ).assertIsDisplayed()
        composeRule.onNodeWithTag(STOP_SESSION_DIALOG_STOP_TAG).assertIsDisplayed()
        assertEquals(emptyList<Pair<String, Boolean>>(), stops)
    }

    @Test
    fun stop_stopsTheTappedSession_withoutForce_andClosesTheDialog() {
        setScreen()
        tapCardStop(0)

        composeRule.onNodeWithTag(STOP_SESSION_DIALOG_STOP_TAG).performClick()
        composeRule.waitForIdle()

        assertEquals(listOf("s1" to false), stops)
        composeRule.onNodeWithTag(STOP_SESSION_DIALOG_STOP_TAG).assertDoesNotExist()
    }

    @Test
    fun forceStop_forceStopsTheTappedSession_andClosesTheDialog() {
        setScreen()
        tapCardStop(1)

        composeRule.onNodeWithTag(STOP_SESSION_DIALOG_FORCE_TAG).performClick()
        composeRule.waitForIdle()

        assertEquals(listOf("s2" to true), stops)
        composeRule.onNodeWithTag(STOP_SESSION_DIALOG_FORCE_TAG).assertDoesNotExist()
    }
}
