package com.mobaide.app.ui.sessions

import com.mobaide.app.data.network.dto.AUTO_SCAN_SOURCE_GITHUB
import com.mobaide.app.data.network.dto.AutoScanTemplateSettingsDto
import com.mobaide.app.data.network.dto.AutoScanTemplatesResponse
import com.mobaide.app.data.network.dto.CopilotScanOptionsDto
import com.mobaide.app.data.network.dto.JunieScanOptionsDto
import com.mobaide.app.data.network.dto.WorkspaceInfo
import com.mobaide.app.testing.ProtocolFixtures
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * The Auto Scan step's choices as template settings and back: one capture
 * function ([autoScanTemplateSettings]) and one apply function
 * ([withAutoScanSettings]) that seeding a workspace and applying a template
 * share. Settings travel through the `RestApi` Json configuration, as they do
 * to and from the PC.
 */
class AutoScanTemplateSettingsTest {

    @Serializable
    private data class Case(val name: String, val wire: JsonObject)

    @Serializable
    private data class Fixture(val type: String, val cases: List<Case>)

    private val json = Json { ignoreUnknownKeys = true; coerceInputValues = true }

    private fun wire(type: String, name: String): JsonObject {
        val fixture: Fixture = ProtocolFixtures.load("dto/agent/$type.json")
        return fixture.cases.first { it.name == name }.wire
    }

    private fun throughTheWire(settings: AutoScanTemplateSettingsDto): AutoScanTemplateSettingsDto =
        json.decodeFromString(
            AutoScanTemplateSettingsDto.serializer(),
            json.encodeToString(AutoScanTemplateSettingsDto.serializer(), settings),
        )

    @Test
    fun template_bringsBackTheChoices_andKeepsOnlyTheChosenAgentsOptions() {
        val dialled = CreateSessionUiState(
            initialPrompt = "Fix it.",
            autoScan = AutoScanWizardState(
                source = AUTO_SCAN_SOURCE_GITHUB,
                cli = "junie",
                // A template may keep a filter the step still has to finish.
                githubFilter = GitHubFilterDraft(assignee = " @me ", issueTypes = listOf("Bug")),
                agents = AutoScanAgentBuckets(
                    juniePlanMode = true,
                    junieBrave = false,
                    codex = CodexScanBucket(sandbox = "read-only", fullAuto = false),
                ),
            ),
        )
        val other = CreateSessionUiState(
            autoScan = AutoScanWizardState(agents = AutoScanAgentBuckets(codex = CodexScanBucket(model = "gpt-5.5"))),
        )

        val settings = throughTheWire(dialled.autoScanTemplateSettings())
        val applied = other.withAutoScanSettings(settings)

        assertEquals(JunieScanOptionsDto(plan_mode = true, brave = false), settings.junie_options)
        assertNull(settings.codex_options)
        assertNull(settings.claude_options)
        assertEquals(AUTO_SCAN_SOURCE_GITHUB to "junie", applied.autoScan.source to applied.autoScan.cli)
        assertEquals(
            GitHubFilterDraft(assignee = "@me", issueTypes = listOf("Bug")),
            applied.autoScan.githubFilter,
        )
        assertEquals("Fix it.", applied.initialPrompt)
        assertEquals(true to false, applied.autoScan.agents.juniePlanMode to applied.autoScan.agents.junieBrave)
        // Options the template does not hold stay as the step had them.
        assertEquals(other.autoScan.agents.codex, applied.autoScan.agents.codex)
    }

    @Test
    fun claudeTemplate_restoresTheAutoScanBucket_andLeavesTheNormalSessionAlone() {
        val dialled = CreateSessionUiState(
            autoScan = AutoScanWizardState(source = AUTO_SCAN_SOURCE_GITHUB, cli = "claude"),
            autoScanClaudeModel = "sonnet",
            autoScanClaudeEffort = "low",
            autoScanPlanMode = true,
            autoScanSkipPermissions = false,
        )
        val fresh = CreateSessionUiState()

        val applied = fresh.withAutoScanSettings(throughTheWire(dialled.autoScanTemplateSettings()))

        assertEquals(
            listOf<Any?>("sonnet", "low", true, false),
            listOf(
                applied.autoScanClaudeModel,
                applied.autoScanClaudeEffort,
                applied.autoScanPlanMode,
                applied.autoScanSkipPermissions,
            ),
        )
        assertEquals(
            listOf(fresh.claudeModel, fresh.claudeEffort, fresh.skipPermissions),
            listOf(applied.claudeModel, applied.claudeEffort, applied.skipPermissions),
        )
    }

    @Test
    fun seedingSavedChoices_givesAnAgentWithoutSavedOptionsTheRunWithoutAskingDefaults() {
        val saved = json.decodeFromJsonElement(WorkspaceInfo.serializer(), wire("ProjectInfo", "auto_scan_choices"))
            .auto_scan!!

        val seeded = CreateSessionUiState().withAutoScanSettings(saved.asTemplateSettings(prompt = null))

        val agents = seeded.autoScan.agents
        assertEquals(
            CodexScanBucket(model = "gpt-5.5", sandbox = "workspace-write", approval = "never", fullAuto = true),
            agents.codex,
        )
        assertEquals(false, agents.copilotAllowAll)
        assertEquals(AutoScanAgentBuckets().cursorForce, agents.cursorForce)
        assertEquals(AutoScanAgentBuckets().junieBrave, agents.junieBrave)
        assertEquals("opus", seeded.autoScanClaudeModel)
        assertEquals(listOf("Bug"), seeded.autoScan.githubFilter.issueTypes)
    }

    @Test
    fun fixtureTemplate_appliesItsAgentAndDraftFilter() {
        val response = json.decodeFromJsonElement(
            AutoScanTemplatesResponse.serializer(), wire("AutoScanTemplatesResponse", "two_templates"),
        )
        val smallBugs = response.templates.first { it.name == "Small bugs" }

        val applied = CreateSessionUiState().withAutoScanSettings(smallBugs.settings)

        assertEquals("codex", applied.autoScan.cli)
        assertEquals(
            CodexScanBucket(model = "gpt-5.5-mini", reasoningEffort = "low", fullAuto = true),
            applied.autoScan.agents.codex,
        )
        assertEquals(GitHubFilterDraft(assignee = "@me", issueTypes = listOf("Bug")), applied.autoScan.githubFilter)
    }

    @Test
    fun optionsTheClientCannotRead_decodeAsNull_andTheWorkspaceStillDecodes() {
        val workspace = json.decodeFromString(
            WorkspaceInfo.serializer(),
            """
            {"name": "alpha", "path": "/p/alpha", "auto_scan": {
              "source": "github", "cli": "codex",
              "codex_options": {"full_auto": [1]},
              "copilot_options": {"allow_all": false}
            }}
            """.trimIndent(),
        )

        val saved = workspace.auto_scan!!
        assertNull(saved.codex_options)
        assertEquals(CopilotScanOptionsDto(allow_all = false), saved.copilot_options)
        assertEquals("codex", saved.cli)
    }

    @Test
    fun templateNames_matchIgnoringCaseAndWhiteSpaceRuns() {
        assertEquals(templateNameKey("small bugs"), templateNameKey("  Small \t BUGS "))
    }

    @Test
    fun saveCheck_blocksOnlyWhatThePcRefusesInATemplate() {
        fun check(
            filter: GitHubFilterDraft = GitHubFilterDraft(),
            cli: String = "codex",
            prompt: String = "",
            addDirs: String = "",
        ) = CreateSessionUiState(
            initialPrompt = prompt,
            autoScan = AutoScanWizardState(
                source = AUTO_SCAN_SOURCE_GITHUB,
                cli = cli,
                githubFilter = filter,
                agents = AutoScanAgentBuckets(antigravity = AntigravityScanBucket(addDirsDraft = addDirs)),
            ),
        ).autoScanTemplateSaveBlocker
        val long = "x".repeat(101)

        // A template may keep a filter the scan would refuse: no label yet, or
        // a label to add that is also a label to pick up.
        assertNull(check())
        assertNull(check(GitHubFilterDraft(labels = listOf("ai-ready"), activeLabel = " AI-Ready ")))
        assertNull(check(GitHubFilterDraft(issueTypes = listOf("Bug, urgent"))))
        assertNull(check(prompt = "x".repeat(32_000)))
        assertNull(check(addDirs = "-x")) // the directories of an agent not chosen

        assertNotNull(check(GitHubFilterDraft(labels = listOf("ai,ready"))))
        assertNotNull(check(GitHubFilterDraft(activeLabel = "in,progress")))
        assertNotNull(check(GitHubFilterDraft(activeLabel = long)))
        assertNotNull(check(GitHubFilterDraft(issueTypes = listOf(long))))
        assertNotNull(check(GitHubFilterDraft(assignee = long)))
        assertNotNull(check(prompt = "x".repeat(32_001)))
        assertNotNull(check(cli = "antigravity", addDirs = "-x"))
    }
}
