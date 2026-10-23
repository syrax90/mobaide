package com.mobaide.app.ui.sessions.panel

import androidx.activity.ComponentActivity
import androidx.compose.ui.test.assert
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertIsNotSelected
import androidx.compose.ui.test.assertIsSelected
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import com.mobaide.app.data.network.dto.AgentSummaryResponse
import com.mobaide.app.data.network.dto.SessionKind
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.ui.theme.MobaideTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The terminal drawer's content: session rows, the current-row rule (tap =
 * close, not a self-switch), the agents section's online gate, and the two
 * shortcuts. Stateless, so every test renders one [SessionsPanelUiState] and
 * records the callbacks.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w360dp-h800dp-xxhdpi")
class SessionsPanelTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    private fun session(
        id: String,
        title: String? = null,
        project: String? = null,
        kind: SessionKind = SessionKind.Claude,
        attention: String? = null,
        state: String = "running",
    ): SessionResponse = SessionResponse(
        session_id = id,
        state = state,
        created_at = "2026-01-01T00:00:00Z",
        title = title,
        project_name = project,
        kind = kind,
        attention = attention,
    )

    private fun agent(id: String, name: String, online: Boolean): AgentSummaryResponse =
        AgentSummaryResponse(agent_id = id, name = name, online = online, created_at = "")

    private class Recorder {
        val selectedSessions = mutableListOf<String>()
        val selectedAgents = mutableListOf<String>()
        val stops = mutableListOf<String>()
        var creates = 0
        var closes = 0
    }

    private fun setPanel(state: SessionsPanelUiState, currentSessionId: String = "s1"): Recorder {
        val recorder = Recorder()
        composeRule.setContent {
            MobaideTheme(darkTheme = true) {
                SessionsPanel(
                    state = state,
                    currentSessionId = currentSessionId,
                    onSelectSession = { recorder.selectedSessions += it },
                    onStopSession = { recorder.stops += it.session_id },
                    onCreateSession = { recorder.creates += 1 },
                    onSelectAgent = { recorder.selectedAgents += it.agent_id },
                    onClose = { recorder.closes += 1 },
                )
            }
        }
        return recorder
    }

    private val twoSessions = SessionsPanelUiState(
        sessions = listOf(
            session("s1", title = "fix: login", project = "app", attention = "awaiting_permission"),
            session("s2", project = "api", kind = SessionKind.Codex),
        ),
        synced = true,
        agents = listOf(agent("a1", "desktop", online = true)),
        connectedAgentId = "a1",
    )

    @Test
    fun rows_renderTitleKindProjectAndAttention_withTheCurrentOneSelected() {
        setPanel(twoSessions)

        composeRule.onNodeWithTag(sessionsPanelRowTag("s1")).assertIsDisplayed().assertIsSelected()
        composeRule.onNodeWithText("fix: login").assertIsDisplayed()
        composeRule.onNodeWithText("Claude Code · app").assertIsDisplayed()
        // `PixelChip` uppercases its label; the mapping is what is under test here.
        composeRule.onNodeWithText("Needs permission", ignoreCase = true).assertIsDisplayed()

        composeRule.onNodeWithTag(sessionsPanelRowTag("s2")).assertIsDisplayed().assertIsNotSelected()
        // No OSC title yet: the composite is the headline and the detail line
        // repeats the kind, the way the list screen's card does.
        composeRule.onNodeWithTag(sessionsPanelRowTag("s2")).assert(hasText("Codex · api"))
    }

    @Test
    fun tappingAnotherRow_switches_andTappingTheCurrentRow_onlyCloses() {
        val recorder = setPanel(twoSessions)

        composeRule.onNodeWithTag(sessionsPanelRowTag("s2")).performClick()
        assertEquals(listOf("s2"), recorder.selectedSessions)
        assertEquals(0, recorder.closes)

        composeRule.onNodeWithTag(sessionsPanelRowTag("s1")).performClick()
        assertEquals("the current session must not be re-opened on top of itself", listOf("s2"), recorder.selectedSessions)
        assertEquals(1, recorder.closes)
    }

    @Test
    fun connectedAgentLine_namesTheAgent_andListsOnlyTheOthers() {
        val recorder = setPanel(
            twoSessions.copy(
                agents = listOf(
                    agent("a1", "desktop", online = true),
                    agent("a2", "laptop", online = false),
                    agent("a3", "server", online = true),
                ),
            ),
        )

        composeRule.onNodeWithText("desktop").assertIsDisplayed()
        composeRule.onNodeWithTag(sessionsPanelAgentTag("a1")).assertDoesNotExist()

        composeRule.onNodeWithTag(sessionsPanelAgentTag("a2")).assertIsDisplayed().assertIsNotEnabled()
        composeRule.onNodeWithTag(sessionsPanelAgentTag("a2")).assert(hasText("Offline"))
        composeRule.onNodeWithTag(sessionsPanelAgentTag("a2")).performClick()
        assertEquals("an offline agent cannot be connected to", emptyList<String>(), recorder.selectedAgents)

        composeRule.onNodeWithTag(sessionsPanelAgentTag("a3")).assertIsEnabled().performClick()
        assertEquals(listOf("a3"), recorder.selectedAgents)
    }

    @Test
    fun agentsSection_isAbsent_whenTheAccountOwnsOnlyTheConnectedAgent() {
        setPanel(twoSessions)

        composeRule.onNodeWithText("AGENTS").assertDoesNotExist()
        composeRule.onNodeWithText("Agents").assertDoesNotExist()
    }

    @Test
    fun inFlightSwitch_labelsItsTarget_andFreezesTheOtherAgents() {
        val recorder = setPanel(
            twoSessions.copy(
                agents = listOf(
                    agent("a1", "desktop", online = true),
                    agent("a2", "laptop", online = true),
                    agent("a3", "server", online = true),
                ),
                switchingAgentId = "a2",
            ),
        )

        composeRule.onNodeWithTag(sessionsPanelAgentTag("a2")).assert(hasText("Connecting…"))
        composeRule.onNodeWithTag(sessionsPanelAgentTag("a3")).assertIsNotEnabled().performClick()
        assertEquals("a second switch must not race the first", emptyList<String>(), recorder.selectedAgents)
    }

    @Test
    fun closeAndNewSession_reachTheirCallbacks() {
        val recorder = setPanel(twoSessions)

        composeRule.onNodeWithTag(SESSIONS_PANEL_CLOSE_TAG).performClick()
        composeRule.onNodeWithTag(SESSIONS_PANEL_NEW_SESSION_TAG).performClick()

        assertEquals(1, recorder.closes)
        assertEquals(1, recorder.creates)
        assertEquals(emptyList<String>(), recorder.selectedSessions)
    }

    @Test
    fun unsyncedList_readsAsReconnecting_notAsEmpty() {
        setPanel(SessionsPanelUiState(synced = false))

        composeRule.onNodeWithText("Reconnecting…").assertIsDisplayed()
        composeRule.onNodeWithText("No active sessions").assertDoesNotExist()
    }

    @Test
    fun syncedEmptyList_readsAsNoActiveSessions() {
        setPanel(SessionsPanelUiState(synced = true))

        composeRule.onNodeWithText("No active sessions").assertIsDisplayed()
        composeRule.onNodeWithText("Reconnecting…").assertDoesNotExist()
        composeRule.onNodeWithTag(SESSIONS_PANEL_NEW_SESSION_TAG).assertIsDisplayed()
    }

    @Test
    fun stopButton_showsOnlyForRunningOrStartingRows_andReportsItsSession() {
        val recorder = setPanel(
            SessionsPanelUiState(
                sessions = listOf(
                    session("s1"),
                    session("s2", state = "starting"),
                    session("s3", state = "stopped"),
                ),
                synced = true,
            ),
        )

        composeRule.onNodeWithTag(sessionsPanelStopTag("s3")).assertDoesNotExist()

        composeRule.onNodeWithTag(sessionsPanelStopTag("s2")).assertIsEnabled().performClick()
        composeRule.onNodeWithTag(sessionsPanelStopTag("s1")).assertIsEnabled().performClick()
        assertEquals(listOf("s2", "s1"), recorder.stops)
        assertEquals("stop must not double as a switch or close", emptyList<String>(), recorder.selectedSessions)
        assertEquals(0, recorder.closes)
    }

    @Test
    fun stopButton_isDisabledOnEveryRowWhileAStopIsInFlight() {
        val recorder = setPanel(twoSessions.copy(stoppingSessionId = "s2"))

        composeRule.onNodeWithTag(sessionsPanelStopTag("s2")).assertIsNotEnabled().performClick()
        composeRule.onNodeWithTag(sessionsPanelStopTag("s1")).assertIsNotEnabled().performClick()
        assertEquals("no stop may be requested while one is in flight", emptyList<String>(), recorder.stops)
    }
}
