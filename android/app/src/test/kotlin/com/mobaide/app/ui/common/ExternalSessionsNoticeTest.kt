package com.mobaide.app.ui.common

import androidx.activity.ComponentActivity
import androidx.compose.material3.Surface
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w360dp-h800dp-xxhdpi")
class ExternalSessionsNoticeTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    @Test
    fun notice_rendersHeadlineAndExplanation() {
        composeRule.setContent {
            Surface { ExternalSessionsNotice(onDismiss = {}) }
        }

        composeRule.onNodeWithTag(EXTERNAL_SESSIONS_NOTICE_TAG).assertIsDisplayed()
        composeRule.onNodeWithText("Sessions started outside Mobaide won't appear here")
            .assertIsDisplayed()
        // The headline alone reads as a defect report; the body is what tells
        // the user what to do instead.
        composeRule.onNodeWithText(
            "Mobaide lists only sessions its own agent started",
            substring = true,
        ).assertIsDisplayed()
    }

    @Test
    fun gotItButton_firesOnDismissOnce() {
        var dismissals = 0
        composeRule.setContent {
            Surface { ExternalSessionsNotice(onDismiss = { dismissals++ }) }
        }

        composeRule.onNodeWithTag(EXTERNAL_SESSIONS_NOTICE_DISMISS_TAG)
            .assertIsDisplayed()
            .performClick()

        assertEquals(1, dismissals)
    }
}
