package com.mobaide.app.ui.sessions.panel

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.mobaide.app.data.network.dto.AgentSummaryResponse
import com.mobaide.app.data.network.dto.SessionKind
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.ui.common.pixel.PixelIconButton
import com.mobaide.app.ui.common.pixel.PixelStatusDot
import com.mobaide.app.ui.common.pixel.pixelFrame
import com.mobaide.app.ui.sessions.SessionAttentionChip
import com.mobaide.app.ui.sessions.SessionStatusIndicator
import com.mobaide.app.ui.sessions.sessionKindLabel
import com.mobaide.app.ui.sessions.sessionStatusChip
import com.mobaide.app.ui.sessions.sessionTitle
import com.mobaide.app.ui.theme.ChromeFontFamily
import com.mobaide.app.ui.theme.MobaideTheme
import com.mobaide.app.ui.theme.chromeCaps
import com.mobaide.app.ui.theme.pixel
import kotlinx.coroutines.flow.first

const val SESSIONS_PANEL_TAG = "sessions_panel"
const val SESSIONS_PANEL_CLOSE_TAG = "sessions_panel_close"
const val SESSIONS_PANEL_NEW_SESSION_TAG = "sessions_panel_new_session"

fun sessionsPanelRowTag(sessionId: String) = "sessions_panel_row_$sessionId"
fun sessionsPanelStopTag(sessionId: String) = "sessions_panel_stop_$sessionId"
fun sessionsPanelAgentTag(agentId: String) = "sessions_panel_agent_$agentId"

/** How long the selection frame takes to move to another row. */
const val SESSIONS_PANEL_SELECTION_MS = 250

/**
 * The terminal's left drawer (Herdr's sidebar): the connected agent's
 * sessions for one-tap switching or stopping, a shortcut to the create flow, and the
 * account's other agents. Stateless — the drawer host owns open/closed.
 *
 * [departingSessionId] is a session already stopped from this panel whose row
 * may still be listed until the agent's push drops it: it is dimmed and inert
 * meanwhile, as is the row of a stop still in flight.
 */
@Composable
fun SessionsPanel(
    state: SessionsPanelUiState,
    currentSessionId: String,
    onSelectSession: (sessionId: String) -> Unit,
    onStopSession: (SessionResponse) -> Unit,
    onCreateSession: () -> Unit,
    onSelectAgent: (AgentSummaryResponse) -> Unit,
    onClose: () -> Unit,
    modifier: Modifier = Modifier,
    departingSessionId: String? = null,
) {
    val listState = rememberLazyListState()
    // The selection can move without a tap (a stop hands it to the nearest
    // session), so bring the current row into view whenever it changes.
    LaunchedEffect(currentSessionId) {
        val index = state.sessions.indexOfFirst { it.session_id == currentSessionId }
        if (index < 0) return@LaunchedEffect
        val item = index + if (state.synced) 0 else 1
        val layout = snapshotFlow { listState.layoutInfo }.first { it.visibleItemsInfo.isNotEmpty() }
        val shown = layout.visibleItemsInfo.firstOrNull { it.index == item }
        val fullyShown = shown != null && shown.offset >= layout.viewportStartOffset &&
            shown.offset + shown.size <= layout.viewportEndOffset
        if (!fullyShown) listState.animateScrollToItem(item)
    }
    Column(
        modifier = modifier
            .fillMaxHeight()
            .width(300.dp)
            .testTag(SESSIONS_PANEL_TAG),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(min = 56.dp)
                .padding(start = 16.dp, end = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = "Sessions".chromeCaps(),
                style = MaterialTheme.typography.titleMedium.copy(fontFamily = ChromeFontFamily),
                color = MaterialTheme.colorScheme.primary,
                modifier = Modifier.weight(1f),
            )
            PixelIconButton(
                imageVector = Icons.Default.Close,
                contentDescription = "Close sessions panel",
                onClick = onClose,
                compact = true,
                modifier = Modifier.testTag(SESSIONS_PANEL_CLOSE_TAG),
            )
        }
        Rule()

        val agent = state.connectedAgent
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            val online = agent?.online == true
            PixelStatusDot(
                color = if (online) MaterialTheme.pixel.statusRunning else MaterialTheme.pixel.statusStopped,
            )
            Spacer(modifier = Modifier.width(8.dp))
            Text(
                text = agent?.name ?: "Connected agent",
                style = MaterialTheme.typography.titleSmall,
                fontFamily = ChromeFontFamily,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f),
            )
            Text(
                text = if (online) "Online" else "Offline",
                style = MaterialTheme.typography.labelSmall,
                color = if (online) MaterialTheme.pixel.statusRunning else MaterialTheme.pixel.statusStopped,
            )
        }

        LazyColumn(
            state = listState,
            modifier = Modifier
                .weight(1f)
                .fillMaxWidth(),
            contentPadding = PaddingValues(horizontal = 12.dp, vertical = 4.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            if (!state.synced) {
                item {
                    PanelNote("Reconnecting…")
                }
            } else if (state.sessions.isEmpty()) {
                item {
                    PanelNote("No active sessions")
                }
            }
            items(state.sessions, key = { it.session_id }) { session ->
                SessionRow(
                    session = session,
                    kindLabel = sessionKindLabel(session, state.customTypeNames),
                    selected = session.session_id == currentSessionId,
                    stopping = state.stoppingSessionId != null || session.session_id == departingSessionId,
                    departing = session.session_id == departingSessionId ||
                        session.session_id == state.stoppingSessionId,
                    onClick = {
                        if (session.session_id == currentSessionId) onClose() else onSelectSession(session.session_id)
                    },
                    onStop = { onStopSession(session) },
                    // Fades a stopped row out and slides its neighbours into the gap.
                    modifier = Modifier.animateItem(),
                )
            }
            item {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .heightIn(min = 48.dp)
                        .clickable(onClick = onCreateSession)
                        .testTag(SESSIONS_PANEL_NEW_SESSION_TAG)
                        .padding(horizontal = 4.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Icon(
                        Icons.Default.Add,
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.primary,
                    )
                    Spacer(modifier = Modifier.width(8.dp))
                    Text(
                        text = "New session",
                        style = MaterialTheme.typography.titleSmall,
                        fontFamily = ChromeFontFamily,
                        color = MaterialTheme.colorScheme.primary,
                    )
                }
            }
            val others = state.otherAgents
            if (others.isNotEmpty()) {
                item {
                    Text(
                        text = "Agents".chromeCaps(),
                        style = MaterialTheme.typography.labelMedium.copy(fontFamily = ChromeFontFamily),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(start = 4.dp, top = 8.dp),
                    )
                }
                items(others, key = { it.agent_id }) { other ->
                    AgentRow(
                        agent = other,
                        switching = state.switchingAgentId == other.agent_id,
                        enabled = other.online && state.switchingAgentId == null,
                        onClick = { onSelectAgent(other) },
                    )
                }
            }
        }
    }
}

@Composable
private fun Rule() {
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .height(2.dp)
            .background(MaterialTheme.colorScheme.outlineVariant),
    )
}

@Composable
private fun PanelNote(text: String) {
    Text(
        text = text,
        style = MaterialTheme.typography.bodyMedium,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(horizontal = 4.dp, vertical = 12.dp),
    )
}

@Composable
private fun SessionRow(
    session: SessionResponse,
    kindLabel: String,
    selected: Boolean,
    stopping: Boolean,
    departing: Boolean,
    onClick: () -> Unit,
    onStop: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val selectionSpec = tween<Color>(SESSIONS_PANEL_SELECTION_MS)
    val ring by animateColorAsState(
        targetValue = if (selected) MaterialTheme.pixel.brand2 else MaterialTheme.pixel.surface2,
        animationSpec = selectionSpec,
        label = "sessionRowRing",
    )
    // Toward a transparent surface, not Color.Transparent (transparent black):
    // the fade would pass through a dark tint otherwise.
    val fill by animateColorAsState(
        targetValue = MaterialTheme.pixel.surface.copy(alpha = if (selected) 1f else 0f),
        animationSpec = selectionSpec,
        label = "sessionRowFill",
    )
    val contentAlpha by animateFloatAsState(
        targetValue = if (departing) 0.4f else 1f,
        animationSpec = tween(SESSIONS_PANEL_SELECTION_MS),
        label = "sessionRowAlpha",
    )
    Row(
        modifier = modifier
            .fillMaxWidth()
            .alpha(contentAlpha)
            .background(fill)
            .pixelFrame(ring)
            .clickable(enabled = !departing, onClick = onClick)
            .semantics { this.selected = selected }
            .testTag(sessionsPanelRowTag(session.session_id))
            .padding(horizontal = 12.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        SessionStatusIndicator(session)
        Spacer(modifier = Modifier.width(10.dp))
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = sessionTitle(session, kindLabel),
                style = MaterialTheme.typography.titleSmall,
                fontFamily = ChromeFontFamily,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            val detail = listOfNotNull(kindLabel, session.project_name).joinToString(" · ")
            Text(
                text = detail,
                style = MaterialTheme.typography.bodySmall,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        sessionStatusChip(session)?.let { chip ->
            Spacer(modifier = Modifier.width(8.dp))
            SessionAttentionChip(chip)
        }
        if (session.state == "running" || session.state == "starting") {
            Spacer(modifier = Modifier.width(8.dp))
            PixelIconButton(
                onClick = onStop,
                enabled = !stopping,
                compact = true,
                modifier = Modifier.testTag(sessionsPanelStopTag(session.session_id)),
            ) {
                Icon(
                    Icons.Default.Stop,
                    contentDescription = "Stop session",
                    tint = if (stopping) MaterialTheme.pixel.gray else MaterialTheme.colorScheme.error,
                )
            }
        }
    }
}

@Composable
private fun AgentRow(
    agent: AgentSummaryResponse,
    switching: Boolean,
    enabled: Boolean,
    onClick: () -> Unit,
) {
    val online = agent.online
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .heightIn(min = 48.dp)
            .clickable(enabled = enabled, onClick = onClick)
            .testTag(sessionsPanelAgentTag(agent.agent_id))
            .padding(horizontal = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        PixelStatusDot(
            color = if (online) MaterialTheme.pixel.statusRunning else MaterialTheme.pixel.statusStopped,
        )
        Spacer(modifier = Modifier.width(10.dp))
        Text(
            text = agent.name,
            style = MaterialTheme.typography.titleSmall,
            fontFamily = ChromeFontFamily,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            color = if (enabled || switching) MaterialTheme.colorScheme.onBackground
            else MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.weight(1f),
        )
        Text(
            text = when {
                switching -> "Connecting…"
                online -> "Online"
                else -> "Offline"
            },
            style = MaterialTheme.typography.labelSmall,
            color = if (online) MaterialTheme.pixel.statusRunning else MaterialTheme.pixel.statusStopped,
        )
    }
}

private val previewState = SessionsPanelUiState(
    sessions = listOf(
        SessionResponse(
            session_id = "abc12345", state = "running", created_at = "", cwd = "/home/me/app",
            title = "fix: login", project_name = "app", kind = SessionKind.Claude,
            attention = "awaiting_permission",
        ),
        SessionResponse(
            session_id = "fed09876", state = "running", created_at = "", project_name = "web",
            kind = SessionKind.Claude, activity = "working",
        ),
        SessionResponse(
            session_id = "def67890", state = "running", created_at = "", project_name = "api",
            kind = SessionKind.Codex,
        ),
    ),
    synced = true,
    agents = listOf(
        AgentSummaryResponse(agent_id = "a1", name = "desktop", online = true, created_at = ""),
        AgentSummaryResponse(agent_id = "a2", name = "laptop", online = false, created_at = ""),
    ),
    connectedAgentId = "a1",
)

@Preview(name = "Sessions panel dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun SessionsPanelPreviewDark() {
    MobaideTheme(darkTheme = true) {
        SessionsPanel(previewState, "abc12345", {}, {}, {}, {}, {})
    }
}

@Preview(name = "Sessions panel light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun SessionsPanelPreviewLight() {
    MobaideTheme(darkTheme = false) {
        SessionsPanel(previewState, "abc12345", {}, {}, {}, {}, {})
    }
}
