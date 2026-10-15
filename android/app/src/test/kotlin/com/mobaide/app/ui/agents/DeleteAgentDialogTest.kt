package com.mobaide.app.ui.agents

import androidx.activity.ComponentActivity
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

/**
 * UI tests for [DeleteAgentDialog]'s uninstall option: offered only when the
 * agent's build can uninstall itself, checked by default, and what the
 * confirm button reports is the checkbox state at the moment of the click.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class DeleteAgentDialogTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    private val checkboxTag = "delete_agent_uninstall_checkbox"

    private fun setContent(
        supportsUninstall: Boolean,
        online: Boolean = true,
        onConfirm: (Boolean) -> Unit,
    ) {
        composeRule.setContent {
            DeleteAgentDialog(
                agentName = "workstation",
                online = online,
                supportsUninstall = supportsUninstall,
                onConfirm = onConfirm,
                onDismiss = {},
            )
        }
    }

    @Test
    fun supportedAgent_confirmsWithUninstallByDefault() {
        val confirmed = mutableListOf<Boolean>()
        setContent(supportsUninstall = true, onConfirm = { confirmed += it })

        composeRule.onNodeWithTag(checkboxTag).assertExists()
        composeRule.onNodeWithTag("delete_agent_confirm").performClick()

        assertEquals(listOf(true), confirmed)
    }

    @Test
    fun uncheckingTheOption_confirmsPlainDelete() {
        val confirmed = mutableListOf<Boolean>()
        setContent(supportsUninstall = true, onConfirm = { confirmed += it })

        composeRule.onNodeWithTag(checkboxTag).performClick()
        composeRule.onNodeWithTag("delete_agent_confirm").performClick()

        assertEquals(listOf(false), confirmed)
    }

    @Test
    fun unsupportedAgent_hasNoOption_andConfirmsPlainDelete() {
        val confirmed = mutableListOf<Boolean>()
        setContent(supportsUninstall = false, onConfirm = { confirmed += it })

        composeRule.onNodeWithTag(checkboxTag).assertDoesNotExist()
        composeRule.onNodeWithTag("delete_agent_confirm").performClick()

        assertEquals(listOf(false), confirmed)
    }

    @Test
    fun offlineAgent_explainsTheDeferredUninstall() {
        setContent(supportsUninstall = true, online = false, onConfirm = {})

        composeRule.onNodeWithText(
            "The agent is offline, so this happens the next time it connects (within 30 days).",
        ).assertExists()
    }
}
