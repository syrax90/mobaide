package com.mobaide.app.ui.herdr

import app.cash.turbine.test
import com.mobaide.app.data.local.HerdrSeenStore
import com.mobaide.app.data.network.dto.HerdrPane
import com.mobaide.app.data.network.dto.HerdrServer
import com.mobaide.app.data.network.dto.HerdrSnapshot
import com.mobaide.app.data.network.dto.HerdrTab
import com.mobaide.app.data.network.dto.HerdrWorkspace
import com.mobaide.app.data.repository.HerdrRepository
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Rule
import org.junit.Test

/**
 * The Herdr card of the session list shows only while a herdr server runs: a
 * user without herdr must see nothing new. Its chip is the most urgent status
 * of the agents, after this device's "seen" turns.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class HerdrCardViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private val snapshot = MutableStateFlow<HerdrSnapshot?>(null)
    private val seen = MutableStateFlow<Map<String, Long>>(emptyMap())
    private val repository = mockk<HerdrRepository>(relaxed = true).also {
        every { it.snapshot } returns snapshot
        every { it.agentId() } returns "agent-1"
    }
    private val seenStore = mockk<HerdrSeenStore>().also { every { it.seen } returns seen }

    private fun running(vararg panes: HerdrPane) = HerdrSnapshot(
        listOf(
            HerdrServer(
                server_id = "tui:default",
                workspaces = listOf(HerdrWorkspace("w1", "mobaide", listOf(HerdrTab("w1:t1", "main", panes = panes.toList())))),
            ),
        ),
    )

    @Test
    fun card_showsOnlyWhileAServerRuns() = runTest {
        HerdrCardViewModel(repository, seenStore).uiState.test {
            assertNull(awaitItem())

            snapshot.value = HerdrSnapshot(emptyList())
            expectNoEvents()

            snapshot.value = running(HerdrPane("w1:p1", agent = "claude", status = "working"))
            assertEquals(HerdrStatus.Working, awaitItem()?.status)

            // The last server stopped.
            snapshot.value = HerdrSnapshot(emptyList())
            assertNull(awaitItem())
        }
    }

    @Test
    fun card_dropsTheDoneChip_onceThisDeviceHasSeenThatTurn() = runTest {
        snapshot.value = running(HerdrPane("w1:p1", agent = "claude", status = "done", turn = 4))

        HerdrCardViewModel(repository, seenStore).uiState.test {
            assertNull(awaitItem())
            assertEquals(HerdrStatus.Done, awaitItem()?.status)

            seen.value = mapOf(HerdrSeenStore.key("agent-1", "tui:default", "w1:p1") to 4L)
            assertEquals(HerdrStatus.Idle, awaitItem()?.status)
        }
    }
}
