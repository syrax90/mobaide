package com.mobaide.app.data.network.dto

import com.mobaide.app.testing.ProtocolFixtures
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * The Android arm of the Auto Scan sources-and-agents goldens under
 * `tests/protocol_fixtures/dto/agent/`, under the `RestApi` Json configuration:
 * `capabilities.autoscan` decides between the new wizard step and the legacy
 * panel, `auto_scan` seeds the step's choices, and the start body this client
 * sends must encode to the wire the agent's model round-trips.
 */
class AutoScanDtoFixtureTest {

    @Serializable
    private data class Case(val name: String, val wire: JsonObject)

    @Serializable
    private data class Fixture(val type: String, val cases: List<Case>)

    private val json = Json { ignoreUnknownKeys = true; coerceInputValues = true }

    private fun wire(type: String, name: String): JsonObject {
        val fixture: Fixture = ProtocolFixtures.load("dto/agent/$type.json")
        assertEquals(type, fixture.type)
        return fixture.cases.first { it.name == name }.wire
    }

    private fun version(name: String): VersionInfoResponse =
        json.decodeFromJsonElement(VersionInfoResponse.serializer(), wire("VersionInfoResponse", name))

    private fun workspace(name: String): WorkspaceInfo =
        json.decodeFromJsonElement(WorkspaceInfo.serializer(), wire("ProjectInfo", name))

    @Test
    fun versionFixture_autoscanCapability_andNoneFromAnOlderAgent() {
        assertEquals(
            AutoScanCapability(
                sources = listOf("linear", "github"),
                clis = listOf("claude", "codex", "antigravity", "opencode", "copilot", "cursor", "junie", "pi"),
                githubSetup = true,
            ),
            version("autoscan_capability").autoScanCapability(),
        )
        assertNull(version("missing_capabilities_defaults_to_empty_object").autoScanCapability())
    }

    @Test
    fun workspaceFixture_autoScanChoices_decodeAndAnUnchosenFilterStaysNull() {
        assertEquals(
            AutoScanSettingsDto(
                source = "github",
                cli = "codex",
                linear_filter = AutoScanFilterDto(
                    state_names = listOf("Todo"),
                    label_names = listOf("bot"),
                    active_state = "Doing",
                    done_state = "Shipped",
                    removal_label = "bot",
                ),
                github_filter = GitHubScanFilterDto(
                    labels = listOf("ai-ready", "bot"),
                    assignee = "@me",
                    issue_types = listOf("Bug"),
                ),
                claude_options = ClaudeScanOptionsDto(
                    model = "opus",
                    effort = "high",
                    focus_mode = true,
                ),
                codex_options = CodexScanOptionsDto(model = "gpt-5.5"),
                copilot_options = CopilotScanOptionsDto(allow_all = false),
            ),
            workspace("auto_scan_choices").auto_scan,
        )
        // Not the legacy defaults: the wizard must start these fields empty.
        assertEquals(AutoScanSettingsDto(), workspace("auto_scan_nothing_chosen").auto_scan)
        assertNull(workspace("full_heterogeneous_flags").auto_scan)
    }

    @Test
    fun instanceFixture_sourceAndAgent_defaultToLinearAndClaudeOnAnOlderAgent() {
        val newer = json.decodeFromJsonElement(
            AutoScanInstanceDto.serializer(), wire("AutoScanInstanceDto", "github_source_other_agent"),
        )
        val older = json.decodeFromJsonElement(
            AutoScanInstanceDto.serializer(), wire("AutoScanInstanceDto", "minimal_defaults_materialize"),
        )

        assertEquals("github" to "codex", newer.source to newer.cli)
        assertEquals("linear" to "claude", older.source to older.cli)
    }

    @Test
    fun startScanRequest_encodesToTheFixtureWire() {
        val request = StartScanRequest(
            workspace_name = "p",
            source = "github",
            cli = "codex",
            codex_options = CodexScanOptionsDto(model = "gpt-5.5", full_auto = false),
            // "" is sent on purpose: it clears the prompt the workspace saved.
            initial_prompt = "",
            github_filter = GitHubScanFilterDto(
                labels = listOf("ai-ready", "bot"),
                assignee = "@me",
                active_label = "ai-doing",
                issue_types = listOf("Bug", "Task"),
            ),
        )

        assertEquals(
            wire("StartScanRequest", "github_source_other_agent"),
            json.encodeToJsonElement(StartScanRequest.serializer(), request),
        )
    }
}
