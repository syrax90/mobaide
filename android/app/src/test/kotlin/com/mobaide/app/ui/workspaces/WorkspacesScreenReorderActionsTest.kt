package com.mobaide.app.ui.workspaces

import androidx.activity.ComponentActivity
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.semantics.getOrNull
import androidx.compose.ui.test.ExperimentalTestApi
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.SemanticsNodeInteraction
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.performCustomAccessibilityActionWithLabel
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.dto.AutoScanInstanceDto
import com.mobaide.app.data.network.dto.WorkspaceInfo
import com.mobaide.app.data.repository.AutoScanRepository
import com.mobaide.app.data.repository.LinearMetadataRepository
import com.mobaide.app.data.repository.PathSuggestionRepository
import com.mobaide.app.data.repository.WorkspacesRepository
import com.mobaide.app.ui.theme.MobaideTheme
import io.mockk.coEvery
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.flow.MutableStateFlow
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * TalkBack's way to reorder the Workspaces screen: "Move up" / "Move down"
 * custom actions on each card, each saving the whole new order. The drag
 * gesture itself is not driven here — the handle's long-press drag does not
 * run reliably under Robolectric.
 */
@OptIn(ExperimentalTestApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], qualifiers = "w360dp-h800dp-xxhdpi")
class WorkspacesScreenReorderActionsTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    private val saves = mutableListOf<List<String>>()

    private fun workspace(name: String, order: Int) = WorkspaceInfo(
        name = name,
        path = "/p/$name",
        is_git = true,
        order = order,
    )

    private fun setScreen(names: List<String>) {
        val workspacesRepo = mockk<WorkspacesRepository>(relaxed = true)
        val workspaces = names.mapIndexed { index, name -> workspace(name, index) }
        every { workspacesRepo.observeWorkspaces() } returns MutableStateFlow(workspaces)
        coEvery { workspacesRepo.listWorkspaces() } returns workspaces
        coEvery { workspacesRepo.reorderWorkspaces(any()) } answers {
            saves += firstArg<List<String>>()
            emptyList()
        }
        val autoScanRepo = mockk<AutoScanRepository>(relaxed = true)
        every { autoScanRepo.observeScans() } returns MutableStateFlow(emptyList<AutoScanInstanceDto>())
        val tokenStore = mockk<TokenStore>(relaxed = true)
        every { tokenStore.getAgentId() } returns "agent-1"
        val vm = WorkspacesViewModel(
            workspacesRepo,
            autoScanRepo,
            mockk<LinearMetadataRepository>(relaxed = true),
            PathSuggestionRepository(mockk<RestApi>(), tokenStore),
        )
        composeRule.setContent {
            MobaideTheme { WorkspacesScreen(viewModel = vm, onBack = {}) }
        }
        composeRule.waitForIdle()
    }

    private fun card(name: String): SemanticsNodeInteraction =
        composeRule.onNode(hasText(name) and SemanticsMatcher.keyIsDefined(SemanticsActions.CustomActions))

    private fun moveLabels(name: String): List<String> =
        card(name).fetchSemanticsNode().config.getOrNull(SemanticsActions.CustomActions)
            .orEmpty()
            .map { it.label }

    @Test
    fun moveDown_savesTheWholeNewOrder_andTheListFollows() {
        setScreen(listOf("alpha", "beta", "gamma"))

        card("alpha").performCustomAccessibilityActionWithLabel("Move down")
        composeRule.waitForIdle()

        assertEquals(listOf(listOf("beta", "alpha", "gamma")), saves)
        assertEquals(listOf("Move down"), moveLabels("beta"))
        assertEquals(listOf("Move up", "Move down"), moveLabels("alpha"))
    }

    @Test
    fun movingARowBackToItsEarlierPlace_isSavedToo() {
        setScreen(listOf("alpha", "beta", "gamma"))

        card("alpha").performCustomAccessibilityActionWithLabel("Move down")
        composeRule.waitForIdle()
        card("alpha").performCustomAccessibilityActionWithLabel("Move up")
        composeRule.waitForIdle()

        assertEquals(
            listOf(listOf("beta", "alpha", "gamma"), listOf("alpha", "beta", "gamma")),
            saves,
        )
    }
}
