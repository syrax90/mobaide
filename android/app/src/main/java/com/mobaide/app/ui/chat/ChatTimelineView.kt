package com.mobaide.app.ui.chat

import android.content.res.Configuration
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.scaleOut
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material.icons.outlined.Image
import androidx.compose.material.icons.automirrored.outlined.InsertDriveFile
import androidx.compose.material.icons.outlined.Psychology
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.mobaide.app.data.chat.ChatAttachment
import com.mobaide.app.data.chat.ChatItem
import com.mobaide.app.data.chat.ChatStatus
import com.mobaide.app.ui.chat.markdown.CodeFontFamily
import com.mobaide.app.ui.chat.markdown.MarkdownText
import com.mobaide.app.ui.common.pixel.PixelBlinkCursor
import com.mobaide.app.ui.common.pixel.PixelButton
import com.mobaide.app.ui.common.pixel.PixelButtonStyle
import com.mobaide.app.ui.common.pixel.PixelFab
import com.mobaide.app.ui.common.pixel.pixelFrame
import com.mobaide.app.ui.theme.MobaideTheme
import com.mobaide.app.ui.theme.chromeCaps
import com.mobaide.app.ui.theme.pixel

const val CHAT_TIMELINE_TAG = "chat_timeline"

/**
 * The conversation, newest at the bottom. Laid out reversed so the list stays
 * pinned to the bottom while text streams in or the dock grows, and keeps its
 * place by item key when the user has scrolled up.
 */
@Composable
fun ChatTimeline(
    ui: ChatUiState,
    listState: LazyListState,
    onRestart: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val items = ui.items
    val reversed = remember(items) { items.asReversed() }
    val streamingId = ui.streamingItemId
    val waitingTools = ui.waitingToolIds
    val canRestart = ui.state.status == ChatStatus.ERROR && ui.live && !ui.sessionClosed
    val restartEnabled = ui.canRestart
    LazyColumn(
        state = listState,
        reverseLayout = true,
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp, Alignment.Bottom),
    ) {
        items(
            items = reversed,
            key = { it.id },
            contentType = { it::class },
        ) { item ->
            when (item) {
                is ChatItem.User -> UserBubble(item)
                is ChatItem.Agent -> AgentMessage(item, streaming = item.id == streamingId)
                is ChatItem.Thought -> ThoughtRow(item, streaming = item.id == streamingId)
                is ChatItem.Tool -> ChatToolCard(item, waitingForApproval = item.id in waitingTools)
                is ChatItem.Notice -> NoticeBanner(item)
                is ChatItem.Compaction -> CompactionDivider(item)
                is ChatItem.TurnEnd -> TurnEndLabel(item)
                is ChatItem.Error -> ErrorCard(
                    item,
                    canRestart = canRestart,
                    restartEnabled = restartEnabled,
                    onRestart = onRestart,
                )
            }
        }
        if (ui.truncated) {
            item(key = "truncated", contentType = "truncated") {
                Text(
                    text = "Earlier messages are not shown.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        }
    }
}

@Composable
private fun UserBubble(item: ChatItem.User) {
    Box(modifier = Modifier.fillMaxWidth()) {
        Column(
            modifier = Modifier
                .alignEndFraction(0.85f)
                .background(MaterialTheme.colorScheme.surfaceContainerHighest)
                .pixelFrame(MaterialTheme.pixel.brand2)
                .padding(horizontal = 12.dp, vertical = 10.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            if (item.text.isNotEmpty()) {
                SelectionContainer {
                    Text(text = item.text, style = MaterialTheme.typography.bodyMedium)
                }
            }
            if (item.attachments.isNotEmpty()) {
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    item.attachments.forEach { AttachmentChip(it) }
                }
            }
        }
    }
}

@Composable
private fun AttachmentChip(attachment: ChatAttachment) {
    Row(
        modifier = Modifier
            .pixelFrame(MaterialTheme.pixel.rule)
            .padding(horizontal = 6.dp, vertical = 3.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(
            imageVector = if (attachment.isImage) Icons.Outlined.Image else Icons.AutoMirrored.Outlined.InsertDriveFile,
            contentDescription = null,
            tint = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.size(14.dp),
        )
        Spacer(modifier = Modifier.width(4.dp))
        Text(
            text = attachment.label,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 1,
        )
    }
}

@Composable
private fun AgentMessage(item: ChatItem.Agent, streaming: Boolean) {
    Column(modifier = Modifier.fillMaxWidth()) {
        if (item.text.isNotEmpty()) {
            SelectionContainer {
                MarkdownText(text = item.text, style = MaterialTheme.typography.bodyMedium)
            }
        }
        if (streaming) {
            PixelBlinkCursor(height = 14.dp, modifier = Modifier.padding(top = 4.dp))
        }
    }
}

@Composable
private fun ThoughtRow(item: ChatItem.Thought, streaming: Boolean) {
    var expanded by rememberSaveable(item.id) { mutableStateOf(false) }
    val pulse = if (streaming) {
        val transition = rememberInfiniteTransition(label = "thought-pulse")
        transition.animateFloat(
            initialValue = 1f,
            targetValue = 0.35f,
            animationSpec = infiniteRepeatable(tween(700), RepeatMode.Reverse),
            label = "thought-alpha",
        ).value
    } else {
        1f
    }
    Column(modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier
                .clickable(role = Role.Button, onClickLabel = if (expanded) "Hide thinking" else "Show thinking") {
                    expanded = !expanded
                }
                .heightIn(min = 48.dp)
                .padding(end = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(
                imageVector = Icons.Outlined.Psychology,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier
                    .size(18.dp)
                    .alpha(pulse),
            )
            Spacer(modifier = Modifier.width(8.dp))
            Text(
                text = if (streaming) "Thinking…" else "Thinking",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Icon(
                imageVector = if (expanded) Icons.Default.KeyboardArrowUp else Icons.Default.KeyboardArrowDown,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.size(18.dp),
            )
        }
        if (expanded && item.text.isNotBlank()) {
            SelectionContainer {
                MarkdownText(
                    text = item.text,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(start = 26.dp, top = 2.dp),
                )
            }
        }
    }
}

@Composable
private fun NoticeBanner(item: ChatItem.Notice) {
    val pixel = MaterialTheme.pixel
    val (tone, icon) = when (item.severity) {
        "warning" -> pixel.warning to Icons.Filled.Warning
        "error" -> pixel.danger to Icons.Outlined.ErrorOutline
        else -> MaterialTheme.colorScheme.primary to Icons.Filled.Info
    }
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(tone.copy(alpha = 0.08f))
            .pixelFrame(tone)
            .padding(12.dp),
    ) {
        Icon(imageVector = icon, contentDescription = null, tint = tone, modifier = Modifier.size(18.dp))
        Spacer(modifier = Modifier.width(10.dp))
        Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
            if (item.title.isNotBlank()) {
                Text(text = item.title, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold)
            }
            if (item.text.isNotBlank()) {
                SelectionContainer {
                    MarkdownText(
                        text = item.text,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}

@Composable
private fun CompactionDivider(item: ChatItem.Compaction) {
    var expanded by rememberSaveable(item.id) { mutableStateOf(false) }
    val running = item.status == "in_progress"
    val canExpand = item.text.isNotBlank()
    Column(modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .then(
                    if (canExpand) {
                        Modifier.clickable(role = Role.Button, onClickLabel = if (expanded) "Hide summary" else "Show summary") {
                            expanded = !expanded
                        }
                    } else {
                        Modifier
                    },
                )
                .heightIn(min = 48.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            DividerLine(Modifier.weight(1f))
            Text(
                text = when (item.status) {
                    "failed" -> "Compaction failed"
                    "cancelled" -> "Compaction cancelled"
                    else -> "Context compacted"
                }.chromeCaps(),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(horizontal = 8.dp),
            )
            if (running) PixelBlinkCursor(height = 12.dp, modifier = Modifier.padding(end = 8.dp))
            DividerLine(Modifier.weight(1f))
        }
        if (expanded && canExpand) {
            SelectionContainer {
                MarkdownText(
                    text = item.text,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 4.dp),
                )
            }
        }
    }
}

@Composable
private fun DividerLine(modifier: Modifier = Modifier) {
    Box(
        modifier = modifier
            .height(2.dp)
            .background(MaterialTheme.colorScheme.outlineVariant),
    )
}

@Composable
private fun TurnEndLabel(item: ChatItem.TurnEnd) {
    Text(
        text = item.text.ifBlank { "Stopped" },
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        textAlign = TextAlign.Center,
        modifier = Modifier.fillMaxWidth(),
    )
}

@Composable
private fun ErrorCard(
    item: ChatItem.Error,
    canRestart: Boolean,
    restartEnabled: Boolean,
    onRestart: () -> Unit,
) {
    val danger = MaterialTheme.pixel.danger
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(danger.copy(alpha = 0.10f))
            .pixelFrame(danger)
            .padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Row {
            Icon(Icons.Outlined.ErrorOutline, contentDescription = null, tint = danger, modifier = Modifier.size(18.dp))
            Spacer(modifier = Modifier.width(10.dp))
            SelectionContainer {
                Text(text = item.text, style = MaterialTheme.typography.bodyMedium)
            }
        }
        if (canRestart) {
            PixelButton(
                text = "Restart",
                onClick = onRestart,
                style = PixelButtonStyle.Primary,
                enabled = restartEnabled,
            )
        }
    }
}

/** Centered state for a session with nothing to show yet. */
@Composable
fun ChatEmptyState(
    title: String,
    cwd: String?,
    hasCommands: Boolean,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            text = title,
            style = MaterialTheme.typography.titleMedium,
            textAlign = TextAlign.Center,
            modifier = Modifier.semantics { heading() },
        )
        if (!cwd.isNullOrBlank()) {
            Spacer(modifier = Modifier.height(4.dp))
            MiddleEllipsisText(
                text = cwd,
                style = MaterialTheme.typography.bodySmall.copy(fontFamily = CodeFontFamily),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Spacer(modifier = Modifier.height(16.dp))
        Text(
            text = if (hasCommands) "Send a message to start. Type / for commands." else "Send a message to start.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.Center,
        )
    }
}

@Composable
fun ChatLoadingState(text: String, modifier: Modifier = Modifier) {
    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        PixelBlinkCursor(height = 24.dp)
        Spacer(modifier = Modifier.height(16.dp))
        Text(
            text = text,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.Center,
        )
    }
}

@Composable
fun JumpToLatestFab(
    visible: Boolean,
    hasNew: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    AnimatedVisibility(
        visible = visible,
        enter = fadeIn() + scaleIn(),
        exit = fadeOut() + scaleOut(),
        modifier = modifier,
    ) {
        BadgedBox(
            badge = {
                if (hasNew) Badge(containerColor = MaterialTheme.pixel.brand, contentColor = Color.White)
            },
        ) {
            PixelFab(
                onClick = onClick,
                modifier = Modifier
                    .size(40.dp)
                    .semantics { contentDescription = "Jump to latest" },
            ) {
                Icon(imageVector = Icons.Default.KeyboardArrowDown, contentDescription = null)
            }
        }
    }
}

@Preview(name = "Timeline light", showBackground = true)
@Preview(name = "Timeline dark", showBackground = true, uiMode = Configuration.UI_MODE_NIGHT_YES)
@Composable
private fun ChatTimelinePreview() {
    MobaideTheme {
        ChatTimeline(
            ui = ChatUiState(
                loaded = true,
                live = true,
                items = listOf(
                    ChatItem.User("u1", "Fix the login bug", listOf(ChatAttachment(true, "Image"))),
                    ChatItem.Thought("t1", "The null check looks wrong."),
                    ChatItem.Agent("m1", "I'll **look** at `login.py` first."),
                    ChatItem.Notice("n1", "warning", "MCP server unavailable", "Continuing without it."),
                    ChatItem.Compaction("c1", "completed", "## Retained context"),
                    ChatItem.TurnEnd("e1", "cancelled", "Stopped"),
                    ChatItem.Error("x1", "The agent process exited with code 1"),
                ),
            ),
            listState = androidx.compose.foundation.lazy.rememberLazyListState(),
            onRestart = {},
        )
    }
}

@Preview(name = "Empty light", showBackground = true)
@Preview(name = "Empty dark", showBackground = true, uiMode = Configuration.UI_MODE_NIGHT_YES)
@Composable
private fun ChatEmptyStatePreview() {
    MobaideTheme {
        ChatEmptyState(title = "Claude Agent", cwd = "/home/user/projects/very/long/path/to/proj", hasCommands = true)
    }
}
