package com.mobaide.app.ui.herdr

import com.mobaide.app.data.local.HerdrSeenStore
import com.mobaide.app.data.network.dto.HerdrPane
import com.mobaide.app.data.network.dto.HerdrServer
import com.mobaide.app.data.network.dto.HerdrSnapshot
import com.mobaide.app.data.network.dto.HerdrTab
import com.mobaide.app.data.network.dto.HerdrWorkspace
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Test

/**
 * The rules the Herdr dashboard reads a snapshot by. Web and iOS keep the same
 * rules (`herdrDisplay.ts`, `HerdrDisplay.swift`): "seen" is per device and
 * keyed by the pane's `turn`, and the agents that need the user come first.
 */
class HerdrDisplayTest {

    private fun pane(
        id: String = "w1:p1",
        agent: String? = "claude",
        status: String? = null,
        turn: Long? = null,
    ) = HerdrPane(pane_id = id, agent = agent, status = status, turn = turn)

    private fun snapshot(vararg panes: HerdrPane, serverId: String = "tui:default") = HerdrSnapshot(
        servers = listOf(
            HerdrServer(
                server_id = serverId,
                workspaces = listOf(
                    HerdrWorkspace("w1", "mobaide", listOf(HerdrTab("w1:t1", "main", panes = panes.toList()))),
                ),
            ),
        ),
    )

    @Test
    fun done_isShownUntilThisDeviceHasSeenThatTurn() {
        val done = pane(status = "done", turn = 12)

        assertEquals(HerdrStatus.Done, herdrStatus(done, seenTurn = null))
        assertEquals(HerdrStatus.Done, herdrStatus(done, seenTurn = 11))
        assertEquals(HerdrStatus.Idle, herdrStatus(done, seenTurn = 12))
    }

    @Test
    fun done_withoutATurn_isNeverSeen() {
        // No turn to compare: hiding it would lose a "your move" for good.
        assertEquals(HerdrStatus.Done, herdrStatus(pane(status = "done"), seenTurn = null))
    }

    @Test
    fun seenTurn_neverHidesAnAgentThatNeedsTheUserAgain() {
        assertEquals(HerdrStatus.Blocked, herdrStatus(pane(status = "blocked", turn = 12), seenTurn = 12))
        assertEquals(HerdrStatus.Failed, herdrStatus(pane(status = "failed", turn = 12), seenTurn = 12))
    }

    @Test
    fun paneWithoutAnAgent_isAPlainTerminal_whateverItsStatus() {
        assertEquals(HerdrStatus.None, herdrStatus(pane(agent = null, status = "blocked"), seenTurn = null))
    }

    @Test
    fun statusThisBuildDoesNotKnow_keepsTheAgent() {
        assertEquals(HerdrStatus.Unknown, herdrStatus(pane(status = "hibernating"), seenTurn = null))
        assertEquals(HerdrStatus.Unknown, herdrStatus(pane(status = null), seenTurn = null))
    }

    @Test
    fun rows_readTheSeenTurnOfThisAgentServerAndPaneOnly() {
        val state = snapshot(pane(status = "done", turn = 3))
        fun status(key: String) = herdrPaneRows("agent-1", state, mapOf(key to 3L)).single().status

        assertEquals(HerdrStatus.Idle, status(HerdrSeenStore.key("agent-1", "tui:default", "w1:p1")))
        // The same pane id on another computer, or on another herdr server of
        // this one, is another pane.
        assertEquals(HerdrStatus.Done, status(HerdrSeenStore.key("agent-2", "tui:default", "w1:p1")))
        assertEquals(HerdrStatus.Done, status(HerdrSeenStore.key("agent-1", "tui:work", "w1:p1")))
    }

    @Test
    fun seenKey_doesNotLetIdsThatShareAPrefixCollide() {
        assertNotEquals(HerdrSeenStore.key("a", "b:c", "d"), HerdrSeenStore.key("a:b", "c", "d"))
        assertNotEquals(HerdrSeenStore.key("a", "b", "c:d"), HerdrSeenStore.key("a", "b:c", "d"))
    }

    @Test
    fun agentRows_putTheAgentsThatNeedTheUserFirst_andLeavePlainTerminalsOut() {
        val rows = herdrPaneRows(
            "agent-1",
            snapshot(
                pane("idle", status = "idle"),
                pane("shell", agent = null),
                pane("working", status = "working"),
                pane("done-1", status = "done", turn = 1),
                pane("blocked", status = "blocked"),
                pane("failed", status = "failed"),
                pane("done-2", status = "done", turn = 1),
            ),
            emptyMap(),
        )

        assertEquals(
            listOf("blocked", "failed", "done-1", "done-2", "working", "idle"),
            herdrAgentRows(rows).map { it.pane.pane_id },
        )
    }

    @Test
    fun rollup_isTheMostUrgentStatusOfAGroup() {
        fun rollup(vararg panes: HerdrPane) = herdrRollup(herdrPaneRows("agent-1", snapshot(*panes), emptyMap()))

        assertEquals(
            HerdrStatus.Done,
            rollup(pane("a", status = "idle"), pane("b", status = "working"), pane("c", status = "done")),
        )
        assertEquals(
            HerdrStatus.Blocked,
            rollup(pane("a", status = "done"), pane("b", agent = null), pane("c", status = "blocked")),
        )
        assertEquals(HerdrStatus.None, rollup(pane("a", agent = null)))
        assertEquals(HerdrStatus.None, rollup())
    }

    @Test
    fun rows_areEmptyBeforeTheFirstSnapshot() {
        assertEquals(emptyList<HerdrPaneRow>(), herdrPaneRows("agent-1", null, emptyMap()))
    }
}
