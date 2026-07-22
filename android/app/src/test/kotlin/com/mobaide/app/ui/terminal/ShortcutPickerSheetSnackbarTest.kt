package com.mobaide.app.ui.terminal

import androidx.activity.ComponentActivity
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.Surface
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Lifecycle contract of the [ShortcutPickerSheet]'s in-sheet SnackbarHost.
 *
 * `SnackbarHostState.showSnackbar()` suspends until the snackbar is dismissed,
 * and the only auto-dismiss timer lives in a `LaunchedEffect` inside the
 * *composed* `SnackbarHost`. The sheet is rendered under an `if` in
 * [TerminalScreen] and leaves composition the instant it is dismissed, taking
 * that host and its timer with it — so without an explicit teardown a pending
 * snackbar would strand its caller forever. That caller is TerminalScreen's
 * single `userMessages` collector, and `_userMessages` is a `MutableSharedFlow`
 * written with `tryEmit`, so a stranded collector silently drops every later
 * message on the screen.
 *
 * This is a coroutine/lifecycle contract, not a rendering one, so it does not
 * need the synthetic clicks Robolectric cannot deliver into the sheet's popup
 * window (see [ShortcutPickerContentTest]).
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w480dp-h1200dp-port-xxhdpi")
class ShortcutPickerSheetSnackbarTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    @Test
    fun sheetLeavingComposition_endsThePendingSnackbar_insteadOfStrandingItsCaller() {
        val hostState = SnackbarHostState()
        val open = mutableStateOf(true)
        lateinit var screenScope: CoroutineScope
        composeRule.setContent {
            // Hoisted OUTSIDE the `if`, exactly like TerminalScreen's
            // LaunchedEffect(Unit) collector: it outlives the sheet, so the
            // sheet has to RESUME it rather than cancel it along with itself.
            screenScope = rememberCoroutineScope()
            Surface {
                if (open.value) {
                    ShortcutPickerSheet(
                        recents = emptyList(),
                        onSendChord = {},
                        onDismiss = {},
                        snackbarHostState = hostState,
                    )
                }
            }
        }
        composeRule.onNodeWithText("Keyboard shortcut").assertExists()

        var result: SnackbarResult? = null
        val job = screenScope.launch {
            // Indefinite takes SnackbarHost's auto-dismiss timer out of the
            // picture, so the sheet's teardown is the only thing left that can
            // resume this call — with a timed duration the test could pass on
            // the timer alone. It also matches what a disposed host leaves
            // behind: that timer is a LaunchedEffect, cancelled along with it.
            result = hostState.showSnackbar(
                message = "Failed to save shortcut: boom",
                duration = SnackbarDuration.Indefinite,
            )
        }
        composeRule.waitForIdle()
        assertNotNull(
            "precondition: the host must have taken the snackbar",
            hostState.currentSnackbarData,
        )
        assertFalse("precondition: the snackbar is still pending", job.isCompleted)

        open.value = false
        composeRule.waitForIdle()
        composeRule.onNodeWithText("Keyboard shortcut").assertDoesNotExist()

        assertTrue(
            "a disposed sheet must end its pending snackbar; leaving the call " +
                "suspended strands TerminalScreen's only userMessages collector",
            job.isCompleted,
        )
        assertEquals(SnackbarResult.Dismissed, result)
        assertNull(
            "showSnackbar's finally must clear the host so a reopened sheet " +
                "does not replay a stale snackbar",
            hostState.currentSnackbarData,
        )
    }
}
