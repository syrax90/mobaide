package com.mobaide.app.ui.sessions.panel

import com.mobaide.app.data.network.dto.SessionResponse
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class NearestOpenSessionIdTest {

    private fun row(id: String, state: String = "running"): SessionResponse =
        SessionResponse(session_id = id, state = state, created_at = "2026-04-11T00:00:00Z")

    @Test
    fun prefersTheRowBelow_thenTheRowAbove() {
        val list = listOf(row("a"), row("b"), row("c"))

        assertEquals("c", nearestOpenSessionId(list, list, "b"))
        assertEquals("b", nearestOpenSessionId(list, list, "c"))
        assertEquals("b", nearestOpenSessionId(list, list, "a"))
    }

    @Test
    fun widensOutwardPastRowsThatAreNotOpen() {
        val list = listOf(row("a"), row("b", "stopped"), row("c"), row("d", "crashed"), row("e"))

        // One away on both sides is closed; two below beats two above.
        assertEquals("e", nearestOpenSessionId(list, list, "c"))
        assertEquals("a", nearestOpenSessionId(list, listOf(row("a"), row("c"), row("d", "crashed")), "c"))
    }

    @Test
    fun countsAStartingSessionAsOpen() {
        val list = listOf(row("a", "stopping"), row("b"), row("c", "starting"))

        assertEquals("c", nearestOpenSessionId(list, list, "b"))
    }

    @Test
    fun keepsTheSnapshotPositions_afterTheStoppedRowLeftTheCurrentList() {
        val snapshot = listOf(row("a"), row("b"), row("c"), row("d"))

        assertEquals("c", nearestOpenSessionId(snapshot, listOf(row("a"), row("c"), row("d")), "b"))
    }

    @Test
    fun skipsANeighbourThatHasSinceGoneOrStopped() {
        val snapshot = listOf(row("a"), row("b"), row("c"), row("d"))

        assertEquals("a", nearestOpenSessionId(snapshot, listOf(row("a"), row("b"), row("d")), "b"))
        assertEquals(
            "d",
            nearestOpenSessionId(snapshot, listOf(row("a", "stopped"), row("c", "stopping"), row("d")), "b"),
        )
    }

    @Test
    fun ignoresSessionsThatOnlyAppearedAfterTheSnapshot() {
        val snapshot = listOf(row("a", "stopped"), row("b"))

        assertNull(nearestOpenSessionId(snapshot, listOf(row("a", "stopped"), row("z")), "b"))
    }

    @Test
    fun returnsNull_whenNoOpenSessionIsLeft() {
        assertNull(nearestOpenSessionId(listOf(row("a")), emptyList(), "a"))
        assertNull(nearestOpenSessionId(listOf(row("a", "stopped"), row("b")), listOf(row("a", "stopped")), "b"))
    }

    @Test
    fun returnsNull_whenTheStoppedSessionWasNotInTheSnapshot() {
        val list = listOf(row("a"), row("b"))

        assertNull(nearestOpenSessionId(list, list, "x"))
    }
}
