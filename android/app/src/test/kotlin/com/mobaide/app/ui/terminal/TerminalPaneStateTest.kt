package com.mobaide.app.ui.terminal

import com.mobaide.app.data.network.PaneClient
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The `pane` block of a Herdr pane session's state frame. A grid read where
 * there is none would stop the resize frames of a normal session; a grid
 * missed in watch mode would let this client resize a desktop pane.
 *
 * Robolectric so `org.json.JSONObject` is real (the stub `android.jar` returns
 * defaults under `unitTests.isReturnDefaultValues = true`).
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class TerminalPaneStateTest {

    private fun parse(pane: String) = TerminalPaneState.parse("""{"status":"running","pane":$pane}""")

    @Test
    fun watchBlock_dictatesItsGrid() {
        val state = parse("""{"mode":"watch","cols":84,"rows":47}""")

        assertEquals(TerminalPaneState("watch", 84, 47), state)
        assertEquals(TerminalGrid(cols = 84, rows = 47), state?.grid)
    }

    @Test
    fun controlBlockOfAnotherClient_dictatesThatClientsGrid() {
        // A size from a watcher would resize the pane under the client that controls it.
        val state = parse("""{"mode":"control","controller":"another-device","cols":40,"rows":20}""")!!

        assertEquals(TerminalGrid(cols = 40, rows = 20), state.grid)
        assertTrue(state.isControlledElsewhere)
        assertFalse(state.isControl)
    }

    @Test
    fun controlBlockOfThisClient_leavesTheSizeToIt() {
        // A dictated grid here would stop the resize frames that control exists for.
        val state = parse("""{"mode":"control","controller":"${PaneClient.id}","cols":40,"rows":20}""")!!

        assertNull(state.grid)
        assertTrue(state.isControl)
        assertFalse(state.isControlledElsewhere)
    }

    @Test
    fun stateOfANormalSession_hasNoPaneBlock() {
        assertNull(TerminalPaneState.parse("""{"status":"running","pid":1234}"""))
        assertNull(parse("null"))
        assertNull(TerminalPaneState.parse("not json"))
    }

    @Test
    fun watchBlockItCannotDraw_hasNoGrid() {
        for (pane in listOf(
            """{"mode":"watch"}""",
            """{"mode":"watch","cols":84}""",
            """{"mode":"watch","cols":84,"rows":null}""",
            """{"mode":"watch","cols":0,"rows":47}""",
            """{"mode":"watch","cols":84,"rows":-1}""",
            """{"mode":"observe","cols":84,"rows":47}""",
        )) {
            assertNull(pane, parse(pane)?.grid)
        }
        assertNull(parse("""{"cols":84,"rows":47}"""))
    }
}
