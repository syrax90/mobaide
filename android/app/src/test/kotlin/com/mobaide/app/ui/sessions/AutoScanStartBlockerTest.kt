package com.mobaide.app.ui.sessions

import com.mobaide.app.data.network.dto.AUTO_SCAN_SOURCE_GITHUB
import com.mobaide.app.data.network.dto.AUTO_SCAN_SOURCE_LINEAR
import com.mobaide.app.data.network.dto.AutoScanCapability
import com.mobaide.app.data.network.dto.GitHubScanFilterDto
import com.mobaide.app.data.network.dto.GitHubStatusDto
import com.mobaide.app.data.network.dto.WorkspaceInfo
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * [autoScanStartBlocker] on an agent with `capabilities.autoscan`: the scan has
 * no default choices, so Start stays disabled until the user picked a source,
 * that source's filter and an agent the PC can run.
 */
class AutoScanStartBlockerTest {

    private val ready = CreateSessionUiState(
        workspaces = listOf(WorkspaceInfo(name = "alpha", path = "/p/alpha", is_git = true)),
        selection = WorkspaceSelection.Stored("alpha"),
        autoScanCapability = AutoScanCapability(
            sources = listOf("linear", "github"),
            clis = listOf("claude", "codex", "antigravity", "opencode", "copilot", "cursor", "junie", "pi"),
            githubSetup = true,
        ),
        codexAvailable = true,
        piAvailable = false,
        autoScan = AutoScanWizardState(
            source = AUTO_SCAN_SOURCE_GITHUB,
            cli = "codex",
            githubStatus = GitHubStatusDto(installed = true, signed_in = true, repo = "acme/app"),
            githubFilter = GitHubFilterDraft(labels = listOf("ai-ready")),
        ),
    )

    private fun changed(change: (AutoScanWizardState) -> AutoScanWizardState) =
        ready.copy(autoScan = change(ready.autoScan))

    @Test
    fun startsOnlyOnceEveryChoiceIsMadeAndUsable() {
        assertNull(ready.autoScanStartBlocker)

        assertNotNull(changed { it.copy(source = null) }.autoScanStartBlocker)
        assertNotNull(
            changed { it.copy(githubStatus = GitHubStatusDto(installed = false, problem = "Install gh.")) }
                .autoScanStartBlocker,
        )
        assertNotNull(changed { it.copy(githubStatus = null) }.autoScanStartBlocker)
        assertNotNull(changed { it.copy(githubFilter = GitHubFilterDraft()) }.autoScanStartBlocker)
        // The agent answers 422: the added label would make the issue match again.
        assertNotNull(
            changed { it.copy(githubFilter = GitHubFilterDraft(labels = listOf("ai-ready"), activeLabel = " AI-Ready ")) }
                .autoScanStartBlocker,
        )
        assertNotNull(changed { it.copy(cli = null) }.autoScanStartBlocker)
        // Listed by the agent, but not installed on the PC.
        assertNotNull(changed { it.copy(cli = "pi") }.autoScanStartBlocker)
        // A remembered agent this agent no longer offers is no choice.
        assertNotNull(changed { it.copy(cli = "aider") }.autoScanStartBlocker)
        // Linear without a key or a chosen filter.
        assertNotNull(changed { it.copy(source = AUTO_SCAN_SOURCE_LINEAR) }.autoScanStartBlocker)
    }

    @Test
    fun githubFilter_sendsTrimmedTextAndNullForBlanks() {
        assertEquals(
            GitHubScanFilterDto(labels = listOf("ai-ready"), assignee = null, active_label = "doing"),
            GitHubFilterDraft(labels = listOf("ai-ready"), assignee = "  ", activeLabel = " doing ").toDto(),
        )
        assertNotNull(GitHubFilterDraft(labels = listOf("a"), activeLabel = "x,y").activeLabelError)
    }
}
