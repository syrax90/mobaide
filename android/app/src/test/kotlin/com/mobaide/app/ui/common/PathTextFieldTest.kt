package com.mobaide.app.ui.common

import androidx.activity.ComponentActivity
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performImeAction
import androidx.compose.ui.text.TextRange
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * UI tests for [PathTextField]: the in-flow suggestion rows and their
 * overflow footer, the caret jumping to the end when a pick replaces the
 * text from outside — a String-valued field would strand it mid-path — and
 * the IME's Done closing the list.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w360dp-h800dp-port-xxhdpi")
class PathTextFieldTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    private var value by mutableStateOf("/s")
    private var suggestions by mutableStateOf(PathSuggestionsUi(listOf("srv", "sbin"), overflow = 3, listedPath = "/"))
    private val picked = mutableListOf<String>()
    private val focusChanges = mutableListOf<Boolean>()

    private fun setContent() {
        composeRule.setContent {
            Surface {
                PathTextField(
                    value = value,
                    onValueChange = { value = it },
                    suggestions = suggestions,
                    onFocusChange = { focusChanges += it },
                    onPickSuggestion = { name ->
                        picked += name
                        value = "/$name/"
                    },
                    label = { Text("Path") },
                    isError = false,
                    supportingText = null,
                    modifier = Modifier.testTag(FIELD_TAG),
                )
            }
        }
    }

    @Test
    fun rows_pickTheirFolder_andTheFooterCountsTheRest() {
        setContent()

        composeRule.onNodeWithText("+3 more — keep typing").assertExists()
        composeRule.onNodeWithText("sbin/").performClick()

        assertEquals(listOf("sbin"), picked)
    }

    @Test
    fun noSuggestions_rendersNoList() {
        suggestions = PathSuggestionsUi.None
        setContent()

        composeRule.onNodeWithTag("path_suggestions").assertDoesNotExist()
    }

    @Test
    fun aPickReplacingTheText_putsTheCaretAtTheEnd() {
        setContent()

        composeRule.onNodeWithText("srv/").performClick()
        composeRule.waitForIdle()

        val selection = composeRule.onNodeWithTag(FIELD_TAG)
            .fetchSemanticsNode().config[SemanticsProperties.TextSelectionRange]
        assertEquals(TextRange("/srv/".length), selection)
    }

    @Test
    fun windowsListing_showsBackslashSeparatedRows() {
        suggestions = PathSuggestionsUi(listOf("Users"), listedPath = "C:\\", windowsPaths = true)
        setContent()

        composeRule.onNodeWithText("Users\\").assertExists()
    }

    @Test
    fun imeDone_closesTheListUntilTheTextChanges_andGivesUpFocus() {
        setContent()
        composeRule.onNodeWithTag(FIELD_TAG).performClick()
        composeRule.waitForIdle()

        composeRule.onNodeWithTag(FIELD_TAG).performImeAction()
        composeRule.waitForIdle()

        composeRule.onNodeWithTag("path_suggestions").assertDoesNotExist()
        assertTrue(false in focusChanges.drop(focusChanges.indexOf(true)))

        value = "/sb"
        composeRule.waitForIdle()
        composeRule.onNodeWithTag("path_suggestions").assertExists()
    }

    private companion object {
        const val FIELD_TAG = "path_field"
    }
}
