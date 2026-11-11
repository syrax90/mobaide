package com.mobaide.app.ui.chat

import androidx.activity.ComponentActivity
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assert
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performImeAction
import androidx.compose.ui.test.performTextInput
import com.mobaide.app.data.chat.ChatRequest
import com.mobaide.app.data.chat.ChatSignInInput
import com.mobaide.app.data.chat.ChatSignInMethod
import com.mobaide.app.ui.theme.MobaideTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The sign-in card asking for an API key: the key stays masked until the
 * reveal toggle is pressed, and the keyboard's Done sends it like Submit.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w360dp-h800dp-xxhdpi")
class ChatRequestCardSignInTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    private val apiKey = ChatRequest.SignIn(
        id = "sign-in",
        toolCallId = null,
        title = "Sign in to Codex",
        text = "Paste an API key.",
        stage = ChatRequest.SignIn.STAGE_WAITING,
        methods = listOf(ChatSignInMethod("api_key", "OpenAI API key", "API usage billing")),
        url = null,
        userCode = null,
        input = ChatSignInInput(label = "OpenAI API key", secret = true),
        busy = false,
        error = null,
    )

    private val masked = SemanticsMatcher.keyIsDefined(SemanticsProperties.Password)

    private fun setContent(request: ChatRequest.SignIn = apiKey, onSubmit: (String) -> Unit = {}) {
        composeRule.setContent {
            MobaideTheme(darkTheme = true) {
                ChatRequestCard(
                    request = request,
                    moreWaiting = 0,
                    answering = null,
                    error = null,
                    draft = null,
                    enabled = true,
                    onPermission = { _, _ -> },
                    onFormValue = { _, _, _ -> },
                    onFormAction = { _, _ -> },
                    onSignInStart = { _, _ -> },
                    onSignInSubmit = { _, value -> onSubmit(value) },
                    onSignInCancel = {},
                )
            }
        }
    }

    private fun field() = composeRule.onNodeWithTag(CHAT_SIGN_IN_FIELD_TAG)

    @Test
    fun aKey_isMaskedUntilTheRevealToggleIsPressed() {
        setContent()
        field().performTextInput("sk-test")
        field().assert(masked)

        composeRule.onNodeWithContentDescription("Show OpenAI API key").performClick()
        field().assert(!masked)

        composeRule.onNodeWithContentDescription("Hide OpenAI API key").performClick()
        field().assert(masked)
    }

    @Test
    fun keyboardDone_submitsTheKey() {
        val submits = mutableListOf<String>()
        setContent(onSubmit = { submits += it })

        field().performTextInput("sk-test")
        field().performImeAction()

        assertEquals(listOf("sk-test"), submits)
    }

    @Test
    fun keyboardDone_withoutAKey_sendsNothing() {
        val submits = mutableListOf<String>()
        setContent(onSubmit = { submits += it })

        field().performImeAction()

        assertEquals(emptyList<String>(), submits)
    }

    @Test
    fun aKeyBeingChecked_locksTheField() {
        setContent(apiKey.copy(busy = true))

        field().assertIsNotEnabled()
    }
}
