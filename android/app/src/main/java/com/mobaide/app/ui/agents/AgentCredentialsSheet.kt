@file:OptIn(ExperimentalMaterial3Api::class)

package com.mobaide.app.ui.agents

import android.content.res.Configuration
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Lightbulb
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SheetValue
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.mobaide.app.ui.common.CopyableCodeBlock
import com.mobaide.app.ui.theme.MobaideTheme

/**
 * Host OS the user runs the agent on. The phone cannot detect it (the agent
 * lives on a different machine), so the user picks; the choice drives the
 * `.env` path and restart command shown. Values are the default install
 * locations from `deploy/` — a custom `--prefix` install differs.
 */
enum class AgentHostOs(
    val label: String,
    val envPath: String,
    val restartCommand: String,
) {
    Linux(
        label = "Linux",
        envPath = "/opt/mobaide/mobaide_agent/.env",
        restartCommand = "sudo systemctl restart mobaide-agent",
    ),
    MacOs(
        label = "macOS",
        envPath = "/usr/local/mobaide/mobaide_agent/.env",
        restartCommand = "launchctl unload ~/Library/LaunchAgents/com.mobaide.agent.plist && " +
            "launchctl load ~/Library/LaunchAgents/com.mobaide.agent.plist",
    ),
    Windows(
        label = "Windows",
        envPath = "C:\\Program Files\\MobaideAgent\\mobaide_agent\\.env",
        restartCommand = "Stop-ScheduledTask MobaideAgent; Start-ScheduledTask MobaideAgent",
    ),
}

/**
 * Bottom sheet shown after an agent is created ("Add manually") or
 * re-registered. Surfaces the generated Agent ID + Token and walks the user
 * through applying them on the agent host: a copy-ready `.env` snippet plus
 * the per-OS `.env` path and restart command.
 *
 * Accidental dismissal is blocked (the token is shown only once) — the Done
 * button is the only exit, mirroring the prior AlertDialog's
 * `onDismissRequest = {}`.
 */
@Composable
fun AgentCredentialsSheet(
    title: String,
    agentId: String,
    token: String,
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val sheetState = rememberModalBottomSheetState(
        skipPartiallyExpanded = true,
        confirmValueChange = { it != SheetValue.Hidden },
    )
    ModalBottomSheet(
        onDismissRequest = {},
        sheetState = sheetState,
        modifier = modifier,
    ) {
        AgentCredentialsContent(
            title = title,
            agentId = agentId,
            token = token,
            onDone = onDismiss,
        )
    }
}

/**
 * Visual body of [AgentCredentialsSheet]. `internal` (vs `private`) so a
 * Robolectric Compose test can compose it directly — synthetic clicks are
 * not delivered to nodes inside a [ModalBottomSheet]'s popup window (same
 * constraint as `InstallCommandCard`).
 */
@Composable
internal fun AgentCredentialsContent(
    title: String,
    agentId: String,
    token: String,
    onDone: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var selectedOs by rememberSaveable { mutableStateOf(AgentHostOs.Linux) }
    val envSnippet = "RELAY_AGENT_ID=$agentId\nRELAY_AGENT_TOKEN=$token"

    Column(
        modifier = modifier
            .fillMaxWidth()
            .navigationBarsPadding()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp, vertical = 8.dp)
            .testTag("agent_credentials_sheet"),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(text = title, style = MaterialTheme.typography.titleLarge)
        Text(
            text = "Add these to your Mobaide agent to finish connecting it. " +
                "The token is shown only once, so copy it now.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        CopyableCodeBlock(
            label = "Agent ID",
            code = agentId,
            modifier = Modifier.testTag("cred_agent_id"),
        )
        CopyableCodeBlock(
            label = "Token (shown once)",
            code = token,
            isSensitive = true,
            modifier = Modifier.testTag("cred_token"),
        )

        HorizontalDivider()

        Text(
            text = "Apply on the agent host",
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.Medium,
        )

        StepRow(number = 1, text = "Set both values in the agent's .env file:")
        CopyableCodeBlock(
            label = ".env",
            code = envSnippet,
            isSensitive = true,
            singleLine = false,
            modifier = Modifier.testTag("cred_env_block"),
        )

        StepRow(number = 2, text = "Open .env and restart the agent on your host OS:")
        SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
            AgentHostOs.entries.forEachIndexed { index, os ->
                SegmentedButton(
                    selected = selectedOs == os,
                    onClick = { selectedOs = os },
                    shape = SegmentedButtonDefaults.itemShape(index, AgentHostOs.entries.size),
                    modifier = Modifier.testTag("os_${os.name}"),
                ) {
                    Text(os.label)
                }
            }
        }
        CopyableCodeBlock(
            label = "File location (default install)",
            code = selectedOs.envPath,
            modifier = Modifier.testTag("cred_env_path"),
        )
        CopyableCodeBlock(
            label = "Restart command",
            code = selectedOs.restartCommand,
            modifier = Modifier.testTag("cred_restart"),
        )

        Row(verticalAlignment = Alignment.Top) {
            Icon(
                Icons.Default.Lightbulb,
                contentDescription = null,
                modifier = Modifier.size(18.dp),
                tint = MaterialTheme.colorScheme.primary,
            )
            Spacer(modifier = Modifier.width(8.dp))
            Text(
                text = "Can the agent show a pairing QR? Scanning it from the home " +
                    "screen pairs automatically — no manual editing.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        TextButton(
            onClick = onDone,
            modifier = Modifier
                .align(Alignment.End)
                .testTag("cred_done"),
        ) {
            Text("Done")
        }
        Spacer(modifier = Modifier.size(8.dp))
    }
}

@Composable
private fun StepRow(number: Int, text: String, modifier: Modifier = Modifier) {
    Row(modifier = modifier.fillMaxWidth(), verticalAlignment = Alignment.Top) {
        Text(
            text = "$number.",
            style = MaterialTheme.typography.bodyMedium,
            fontWeight = FontWeight.Bold,
        )
        Spacer(modifier = Modifier.width(8.dp))
        Text(text = text, style = MaterialTheme.typography.bodyMedium)
    }
}

@Preview(name = "AgentCredentials light", showBackground = true)
@Preview(
    name = "AgentCredentials dark",
    showBackground = true,
    uiMode = Configuration.UI_MODE_NIGHT_YES,
)
@Composable
private fun AgentCredentialsContentPreview() {
    MobaideTheme {
        AgentCredentialsContent(
            title = "Agent Created",
            agentId = "a1b2c3d4e5f6",
            token = "xVz9p-Q2r-EXAMPLE-token-not-real-0123456789",
            onDone = {},
        )
    }
}
