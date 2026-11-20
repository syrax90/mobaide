package com.mobaide.app.ui.sessions

import com.mobaide.app.data.network.dto.AUTO_SCAN_SOURCE_GITHUB
import com.mobaide.app.data.network.dto.AUTO_SCAN_UI_CHAT
import com.mobaide.app.data.network.dto.AUTO_SCAN_UI_TERMINAL
import com.mobaide.app.data.network.dto.AutoScanCapability
import com.mobaide.app.data.network.dto.AutoScanTemplateSettingsDto
import com.mobaide.app.data.network.dto.AutoScanTemplatesResponse
import com.mobaide.app.data.network.dto.ChatSupport
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
    fun codeMode_travelsThroughATemplate_forClaudeOpenCodeAndPi_andClaudesForcesTheTerminal() {
        fun dialled(cli: String) = CreateSessionUiState(
            autoScanCapability = AutoScanCapability(
                sources = listOf(AUTO_SCAN_SOURCE_GITHUB), clis = listOf(cli), githubSetup = true,
            ),
            claudeChat = ChatSupport.Available(resume = true),
            autoScan = AutoScanWizardState(
                source = AUTO_SCAN_SOURCE_GITHUB, cli = cli, ui = AUTO_SCAN_UI_CHAT,
                agents = AutoScanAgentBuckets(opencodeCodeMode = false, piCodeMode = true),
            ),
            autoScanClaudeCodeMode = true,
        )
        val fresh = CreateSessionUiState()

        val claude = fresh.withAutoScanSettings(throughTheWire(dialled("claude").autoScanTemplateSettings()))
        val opencode = fresh.withAutoScanSettings(throughTheWire(dialled("opencode").autoScanTemplateSettings()))
        val pi = fresh.withAutoScanSettings(throughTheWire(dialled("pi").autoScanTemplateSettings()))

        assertEquals(true, claude.autoScanClaudeCodeMode)
        assertEquals(false, opencode.autoScan.agents.opencodeCodeMode)
        assertEquals(true, pi.autoScan.agents.piCodeMode)
        // Claude Code Mode runs only in the terminal; the template keeps the chosen view.
        assertEquals(AUTO_SCAN_UI_TERMINAL, dialled("claude").autoScanUi)
        assertEquals(AUTO_SCAN_UI_CHAT, dialled("claude").autoScanTemplateSettings().ui)
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
    fun savedCodexOptionsWithRetiredUntrustedApproval_seedAsOnRequest() {
        val workspace = json.decodeFromString(
            WorkspaceInfo.serializer(),
            """
            {"name": "alpha", "path": "/p/alpha", "auto_scan": {
              "source": "github", "cli": "codex",
              "codex_options": {"sandbox": "read-only", "approval": "untrusted", "full_auto": false}
            }}
            """.trimIndent(),
        )

        val seeded = CreateSessionUiState()
            .withAutoScanSettings(workspace.auto_scan!!.asTemplateSettings(prompt = null))

        assertEquals(
            CodexScanBucket(sandbox = "read-only", approval = "on-request", fullAuto = false),
            seeded.autoScan.agents.codex,
        )
    }

    @Test
    fun seededViewAndInterval_comeBack_andUnusableOnesKeepTheStepsChoice() {
        val saved = json.decodeFromJsonElement(WorkspaceInfo.serializer(), wire("ProjectInfo", "auto_scan_choices"))
            .auto_scan!!

        val seeded = CreateSessionUiState().withAutoScanSettings(saved.asTemplateSettings(prompt = null))

        assertEquals(AUTO_SCAN_UI_CHAT to 15, seeded.autoScan.ui to seeded.autoScan.intervalMinutes)
        // A template from another client, or a hand-edited one, keeps the step's choice.
        listOf(
            AutoScanTemplateSettingsDto(ui = "web", interval_minutes = 0),
            AutoScanTemplateSettingsDto(ui = "Chat", interval_minutes = 1441),
        ).forEach { unusable ->
            val kept = seeded.withAutoScanSettings(unusable)
            assertEquals(AUTO_SCAN_UI_CHAT to 15, kept.autoScan.ui to kept.autoScan.intervalMinutes)
        }
    }

    @Test
    fun startSendsTheChat_onlyOnceTheProbeShowsTheAgentsChatView_butATemplateKeepsTheChosenView() {
        // The PC refuses a chat scan for an agent without a chat view.
        fun state(chat: ChatSupport?, ui: String = AUTO_SCAN_UI_CHAT) = CreateSessionUiState(
            autoScanCapability = AutoScanCapability(
                sources = listOf(AUTO_SCAN_SOURCE_GITHUB), clis = listOf("codex"), githubSetup = true,
            ),
            codexChat = chat,
            autoScan = AutoScanWizardState(
                source = AUTO_SCAN_SOURCE_GITHUB, cli = "codex", ui = ui, intervalMinutes = 240,
            ),
        )

        assertEquals(AUTO_SCAN_UI_CHAT, state(ChatSupport.Available(resume = true)).autoScanUi)
        assertEquals(AUTO_SCAN_UI_TERMINAL, state(chat = null).autoScanUi)
        assertEquals(AUTO_SCAN_UI_TERMINAL, state(ChatSupport.Unavailable).autoScanUi)
        assertEquals(AUTO_SCAN_UI_TERMINAL, state(ChatSupport.AgentOutdated).autoScanUi)
        assertEquals(
            AUTO_SCAN_UI_TERMINAL, state(ChatSupport.Available(resume = true), AUTO_SCAN_UI_TERMINAL).autoScanUi,
        )
        // A template keeps the chosen view even while the probe has no chat view yet.
        assertEquals(AUTO_SCAN_UI_CHAT, state(chat = null).autoScanTemplateSettings().ui)
        assertEquals(
            AUTO_SCAN_UI_TERMINAL,
            state(ChatSupport.Available(resume = true), AUTO_SCAN_UI_TERMINAL).autoScanTemplateSettings().ui,
        )
        assertEquals(240, state(chat = null).autoScanTemplateSettings().interval_minutes)
    }

    @Test
    fun codexAgentDefaultEffort_staysNull_throughATemplateAndBack() {
        val dialled = CreateSessionUiState(
            autoScan = AutoScanWizardState(
                source = AUTO_SCAN_SOURCE_GITHUB,
                cli = "codex",
                agents = AutoScanAgentBuckets(codex = CodexScanBucket(reasoningEffort = null)),
            ),
        )

        val settings = throughTheWire(dialled.autoScanTemplateSettings())
        // The fresh step starts at "high"; null is Codex's own setting, not "no choice".
        val applied = CreateSessionUiState().withAutoScanSettings(settings)

        assertNull(settings.codex_options!!.reasoning_effort)
        assertNull(applied.autoScan.agents.codex.reasoningEffort)
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
