package com.mobaide.app.ui.common

import androidx.activity.ComponentActivity
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.assertIsFocused
import androidx.compose.ui.test.assertIsNotFocused
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.longClick
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.swipeUp
import androidx.compose.ui.unit.dp
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * UI tests for [dismissKeyboardOnTap]. The field losing focus is the observable
 * "keyboard hidden" signal; only a plain tap clears it, and a clickable row inside
 * the area still gets its click.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w360dp-h800dp-xxhdpi")
class DismissKeyboardOnTapTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    private var rowClicks = 0

    private fun setUpFocusedField() {
        composeRule.setContent {
            Surface {
                Column {
                    Box(
                        Modifier
                            .testTag(AREA)
                            .fillMaxWidth()
                            .height(400.dp)
                            .dismissKeyboardOnTap(),
                    ) {
                        Box(
                            Modifier
                                .testTag(ROW)
                                .size(80.dp)
                                .clickable { rowClicks++ },
                        )
                    }
                    BasicTextField(
                        value = "hello",
                        onValueChange = {},
                        modifier = Modifier.testTag(FIELD).fillMaxWidth(),
                    )
                }
            }
        }
        composeRule.onNodeWithTag(FIELD).performClick()
        composeRule.onNodeWithTag(FIELD).assertIsFocused()
    }

    @Test
    fun tapOnClickableRow_clearsFocus_andRowStillGetsClick() {
        setUpFocusedField()

        composeRule.onNodeWithTag(ROW).performClick()

        assertEquals(1, rowClicks)
        composeRule.onNodeWithTag(FIELD).assertIsNotFocused()
    }

    @Test
    fun dragPastTouchSlop_keepsFocus() {
        setUpFocusedField()

        composeRule.onNodeWithTag(AREA).performTouchInput { swipeUp() }

        composeRule.onNodeWithTag(FIELD).assertIsFocused()
    }

    @Test
    fun longPress_keepsFocus() {
        setUpFocusedField()

        composeRule.onNodeWithTag(AREA).performTouchInput { longClick(Offset(width * 0.75f, height * 0.75f)) }

        composeRule.onNodeWithTag(FIELD).assertIsFocused()
    }

    private companion object {
        const val AREA = "timeline"
        const val ROW = "row"
        const val FIELD = "field"
    }
}
