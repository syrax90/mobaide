package com.mobaide.app.ui.chat

import android.content.res.Configuration
import android.graphics.Bitmap
import android.util.LruCache
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.DriveFileMove
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.outlined.Block
import androidx.compose.material.icons.outlined.Build
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.Description
import androidx.compose.material.icons.outlined.Edit
import androidx.compose.material.icons.outlined.Flag
import androidx.compose.material.icons.outlined.Language
import androidx.compose.material.icons.outlined.Link
import androidx.compose.material.icons.outlined.Psychology
import androidx.compose.material.icons.outlined.RadioButtonUnchecked
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material.icons.outlined.Terminal
import androidx.compose.material.icons.outlined.Visibility
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.minimumInteractiveComponentSize
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.painter.BitmapPainter
import androidx.compose.ui.graphics.painter.Painter
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLayoutResult
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.isSpecified
import androidx.compose.ui.unit.sp
import com.mobaide.app.data.chat.ChatContentPart
import com.mobaide.app.data.chat.ChatImageDecoder
import com.mobaide.app.data.chat.ChatItem
import com.mobaide.app.data.chat.ChatLocation
import com.mobaide.app.data.chat.ChatPlanEntry
import com.mobaide.app.data.chat.DecodedChatImage
import com.mobaide.app.data.chat.SubagentState
import com.mobaide.app.data.chat.ToolStatus
import com.mobaide.app.ui.chat.markdown.CodeBlock
import com.mobaide.app.ui.chat.markdown.CodeFontFamily
import com.mobaide.app.ui.chat.markdown.Markdown
import com.mobaide.app.ui.chat.markdown.MarkdownText
import com.mobaide.app.ui.common.pixel.PixelBlinkCursor
import com.mobaide.app.ui.common.pixel.PixelButton
import com.mobaide.app.ui.common.pixel.PixelButtonStyle
import com.mobaide.app.ui.common.pixel.PixelChip
import com.mobaide.app.ui.common.pixel.PixelTextButton
import com.mobaide.app.ui.common.pixel.pixelFrame
import com.mobaide.app.ui.theme.MobaideTheme
import com.mobaide.app.ui.theme.pixel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

private const val DIFF_PREVIEW_LINES = 30

fun toolKindIcon(kind: String): ImageVector = when (kind) {
    "read" -> Icons.Outlined.Visibility
    "edit" -> Icons.Outlined.Edit
    "delete" -> Icons.Outlined.Delete
    "move" -> Icons.AutoMirrored.Outlined.DriveFileMove
    "search" -> Icons.Outlined.Search
    "execute" -> Icons.Outlined.Terminal
    "think" -> Icons.Outlined.Psychology
    "fetch" -> Icons.Outlined.Language
    "switch_mode" -> Icons.Outlined.Flag
    else -> Icons.Outlined.Build
}

private fun ChatItem.Tool.failureLine(): String? {
    if (status != ToolStatus.FAILED) return null
    val text = content.firstNotNullOfOrNull { it as? ChatContentPart.Text }?.text ?: return null
    return text.lineSequence().map { it.trim().trim('`') }.firstOrNull { it.isNotBlank() }
}

/** One tool call: a compact row that expands to its output, locations and raw input. */
@Composable
fun ChatToolCard(
    item: ChatItem.Tool,
    waitingForApproval: Boolean,
    modifier: Modifier = Modifier,
) {
    var expanded by rememberSaveable(item.id) { mutableStateOf(false) }
    val pixel = MaterialTheme.pixel
    val showCounts = item.hasDiff && item.kind in setOf("edit", "delete", "move")
    val statusLabel = toolStatusLabel(item.status)
    Column(
        modifier = modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surfaceContainerLow)
            .pixelFrame(if (waitingForApproval) pixel.brand2 else pixel.surface2),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .clickable(role = Role.Button, onClickLabel = if (expanded) "Collapse" else "Expand") {
                    expanded = !expanded
                }
                .heightIn(min = 48.dp)
                .padding(horizontal = 12.dp, vertical = 6.dp)
                .semantics { stateDescription = statusLabel },
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(
                imageVector = toolKindIcon(item.kind),
                contentDescription = null,
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.size(20.dp),
            )
            Spacer(modifier = Modifier.width(10.dp))
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = item.title.ifBlank { "Tool call" },
                    style = MaterialTheme.typography.bodyMedium,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                item.subtitle?.takeIf { it.isNotBlank() && it != item.title }?.let { subtitle ->
                    Text(
                        text = subtitle,
                        style = MaterialTheme.typography.bodySmall.copy(fontFamily = CodeFontFamily, fontSize = 12.sp),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                item.failureLine()?.let { line ->
                    Text(
                        text = line,
                        style = MaterialTheme.typography.bodySmall,
                        color = pixel.danger,
                        maxLines = if (expanded) 3 else 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                if (waitingForApproval) {
                    Text(
                        text = "Waiting for approval",
                        style = MaterialTheme.typography.bodySmall,
                        fontWeight = FontWeight.SemiBold,
                        color = MaterialTheme.colorScheme.primary,
                    )
                }
            }
            if (showCounts) {
                DiffCounts(added = item.diffAdded, removed = item.diffRemoved, modifier = Modifier.padding(start = 8.dp))
            }
            ToolStatusIcon(item.status, modifier = Modifier.padding(start = 8.dp))
        }
        if (expanded) {
            // Locations and the Input disclosure are 48dp rows with their own air.
            val endsWithRows = item.locations.isNotEmpty() || item.input != null
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(start = 12.dp, end = 12.dp, bottom = if (endsWithRows) 4.dp else 12.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                item.content.forEach { part -> ContentPartView(part) }
                if (endsWithRows) {
                    Column {
                        if (item.locations.isNotEmpty()) LocationsView(item.locations)
                        item.input?.let { InputDisclosure(it) }
                    }
                }
                if (item.content.isEmpty() && !endsWithRows) {
                    Text(
                        text = "No output yet.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}

private fun toolStatusLabel(status: String): String = when (status) {
    ToolStatus.IN_PROGRESS -> "Running"
    ToolStatus.COMPLETED -> "Done"
    ToolStatus.FAILED -> "Failed"
    ToolStatus.CANCELLED -> "Cancelled"
    else -> "Pending"
}

@Composable
fun DiffCounts(added: Int, removed: Int, modifier: Modifier = Modifier) {
    Row(modifier = modifier, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(
            text = "+$added",
            style = MaterialTheme.typography.bodySmall.copy(fontFamily = CodeFontFamily),
            color = MaterialTheme.pixel.ok,
        )
        Text(
            text = "−$removed",
            style = MaterialTheme.typography.bodySmall.copy(fontFamily = CodeFontFamily),
            color = MaterialTheme.pixel.danger,
        )
    }
}

@Composable
private fun ToolStatusIcon(status: String, modifier: Modifier = Modifier) {
    val pixel = MaterialTheme.pixel
    when (status) {
        ToolStatus.IN_PROGRESS -> PixelBlinkCursor(modifier = modifier, height = 14.dp)
        ToolStatus.COMPLETED -> Icon(Icons.Default.Check, null, tint = pixel.ok, modifier = modifier.size(18.dp))
        ToolStatus.FAILED -> Icon(Icons.Default.Close, null, tint = pixel.danger, modifier = modifier.size(18.dp))
        ToolStatus.CANCELLED -> Icon(
            Icons.Outlined.Block,
            null,
            tint = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = modifier.size(18.dp),
        )
        else -> Icon(
            Icons.Outlined.RadioButtonUnchecked,
            null,
            tint = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = modifier.size(18.dp),
        )
    }
}

@Composable
fun ContentPartView(part: ChatContentPart, modifier: Modifier = Modifier) {
    when (part) {
        is ChatContentPart.Text -> Column(modifier = modifier) {
            MarkdownText(text = part.text)
            if (part.truncated) TruncatedFooter("Output truncated")
        }
        is ChatContentPart.Diff -> ChatDiffView(part, modifier)
        is ChatContentPart.Image -> InlineImage(part, modifier)
        is ChatContentPart.Link -> LinkRow(part.name, part.uri, modifier)
        is ChatContentPart.Resource -> Column(modifier = modifier, verticalArrangement = Arrangement.spacedBy(6.dp)) {
            LinkRow(part.name, part.uri)
            part.text?.takeIf { it.isNotBlank() }?.let { CodeBlock(language = null, code = it) }
        }
        is ChatContentPart.Terminal -> Text(
            text = "Terminal output",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = modifier,
        )
    }
}

@Composable
private fun TruncatedFooter(text: String) {
    Text(
        text = text,
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(top = 4.dp),
    )
}

@Composable
private fun LinkRow(name: String, uri: String, modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val safe = Markdown.isSafeUrl(uri)
    Row(
        modifier = modifier
            .fillMaxWidth()
            .then(if (safe) Modifier.clickable(role = Role.Button) { openExternalLink(context, uri) } else Modifier)
            .heightIn(min = 48.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(
            imageVector = if (safe) Icons.Outlined.Link else Icons.Outlined.Description,
            contentDescription = null,
            tint = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.size(18.dp),
        )
        Spacer(modifier = Modifier.width(8.dp))
        Text(
            text = name,
            style = MaterialTheme.typography.bodySmall,
            color = if (safe) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

private val InlineImageMaxHeight = 240.dp

/** Decoded agent images shared by every chat row, so a row that scrolls back in draws at once. */
private object InlineImageCache {
    private val bitmaps = object : LruCache<String, Bitmap>(32 * 1024 * 1024) {
        override fun sizeOf(key: String, value: Bitmap): Int = value.byteCount
    }
    private val sizes = LruCache<String, IntSize>(256)

    fun ready(key: String): DecodedChatImage.Ready? = bitmaps.get(key)?.let { DecodedChatImage.Ready(it) }

    fun size(key: String): IntSize? = sizes.get(key)

    fun put(key: String, bitmap: Bitmap) {
        bitmaps.put(key, bitmap)
        sizes.put(key, IntSize(bitmap.width, bitmap.height))
    }
}

@Composable
private fun InlineImage(part: ChatContentPart.Image, modifier: Modifier = Modifier) {
    val data = part.data
    if (data == null) {
        ImagePlaceholder("Image", modifier)
        return
    }
    val screenWidthDp = LocalConfiguration.current.screenWidthDp
    val density = LocalDensity.current
    val maxWidth = with(density) { screenWidthDp.dp.roundToPx() }
    val maxHeight = with(density) { InlineImageMaxHeight.roundToPx() }
    val key = remember(data, maxWidth, maxHeight) { "${data.length}:${data.hashCode()}:${maxWidth}x$maxHeight" }
    val decoded by produceState<DecodedChatImage?>(InlineImageCache.ready(key), key) {
        value = InlineImageCache.ready(key)
        if (value == null) {
            value = withContext(Dispatchers.Default) {
                ChatImageDecoder.decodeBase64(data, maxWidth, maxHeight).also {
                    if (it is DecodedChatImage.Ready) InlineImageCache.put(key, it.bitmap)
                }
            }
        }
    }
    when (val state = decoded) {
        null -> InlineImageCache.size(key)?.let { size ->
            FramedImage(painter = remember(size) { BlankPainter(size) }, contentDescription = null, modifier = modifier)
        }
        is DecodedChatImage.Ready -> FramedImage(
            painter = remember(state.bitmap) { BitmapPainter(state.bitmap.asImageBitmap()) },
            contentDescription = "Image",
            modifier = modifier,
        )
        DecodedChatImage.TooLarge -> ImagePlaceholder("Image too large", modifier)
        DecodedChatImage.Unreadable -> ImagePlaceholder("Image", modifier)
    }
}

@Composable
private fun FramedImage(painter: Painter, contentDescription: String?, modifier: Modifier = Modifier) {
    Image(
        painter = painter,
        contentDescription = contentDescription,
        contentScale = ContentScale.Fit,
        modifier = modifier
            .heightIn(max = InlineImageMaxHeight)
            .pixelFrame(MaterialTheme.colorScheme.outlineVariant),
    )
}

/** Holds the drawn size of an image that is decoding again after it left the cache. */
private class BlankPainter(size: IntSize) : Painter() {
    override val intrinsicSize = Size(size.width.toFloat(), size.height.toFloat())

    override fun DrawScope.onDraw() = Unit
}

@Composable
private fun ImagePlaceholder(text: String, modifier: Modifier = Modifier) {
    Text(
        text = text,
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = modifier,
    )
}

@Composable
private fun LocationsView(locations: List<ChatLocation>) {
    val context = LocalContext.current
    val haptic = LocalHapticFeedback.current
    Column {
        locations.forEach { location ->
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable(onClickLabel = "Copy path") {
                        copyWithFeedback(context, haptic, "Path", location.label)
                    }
                    .heightIn(min = 48.dp),
                contentAlignment = Alignment.CenterStart,
            ) {
                Text(
                    text = location.label,
                    style = MaterialTheme.typography.bodySmall.copy(fontFamily = CodeFontFamily, fontSize = 12.sp),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        }
    }
}

@Composable
private fun InputDisclosure(input: String) {
    var open by rememberSaveable(input) { mutableStateOf(false) }
    Column {
        Row(
            modifier = Modifier
                .clickable(role = Role.Button) { open = !open }
                .heightIn(min = 48.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(
                imageVector = if (open) Icons.Default.KeyboardArrowUp else Icons.Default.KeyboardArrowDown,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.size(18.dp),
            )
            Text(
                text = "Input",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        if (open) CodeBlock(language = "json", code = input, modifier = Modifier.padding(bottom = 8.dp))
    }
}

/** A unified diff body: one horizontal scroll for the whole block, tinted add/remove lines. */
@Composable
fun ChatDiffView(part: ChatContentPart.Diff, modifier: Modifier = Modifier) {
    val pixel = MaterialTheme.pixel
    val lines = remember(part.diff) { if (part.diff.isEmpty()) emptyList() else part.diff.trimEnd('\n').split('\n') }
    var showAll by rememberSaveable(part.path, part.diff.length) { mutableStateOf(false) }
    val shown = if (showAll || lines.size <= DIFF_PREVIEW_LINES) lines else lines.take(DIFF_PREVIEW_LINES)
    val fileName = part.path.substringAfterLast('/').substringAfterLast('\\').ifBlank { part.path }
    val directory = part.path.dropLast(fileName.length).trimEnd('/', '\\')
    Column(
        modifier = modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surfaceContainerHighest)
            .pixelFrame(MaterialTheme.colorScheme.outlineVariant),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 10.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Text(
                text = fileName,
                style = MaterialTheme.typography.bodySmall,
                fontWeight = FontWeight.SemiBold,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.widthIn(max = 200.dp),
            )
            MiddleEllipsisText(
                text = directory,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.weight(1f),
            )
            if (part.newFile) {
                Text(
                    text = "New file",
                    style = MaterialTheme.typography.bodySmall,
                    fontWeight = FontWeight.SemiBold,
                    color = pixel.ok,
                    modifier = Modifier
                        .background(MaterialTheme.colorScheme.surfaceContainerHighest)
                        .pixelFrame(pixel.ok)
                        .padding(horizontal = 6.dp, vertical = 2.dp),
                )
            }
            DiffCounts(part.added, part.removed)
        }
        if (lines.isEmpty()) {
            Text(
                text = if (part.truncated) "Too large to show a diff" else "No changes",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(horizontal = 10.dp, vertical = 8.dp),
            )
            return@Column
        }
        DiffLines(shown)
        if (shown.size < lines.size) {
            PixelTextButton(onClick = { showAll = true }) {
                Text("Show all ${lines.size} lines", style = MaterialTheme.typography.bodySmall)
            }
        }
        if (part.truncated) {
            Text(
                text = "Diff truncated",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
            )
        }
    }
}

@Composable
private fun DiffLines(lines: List<String>) {
    val pixel = MaterialTheme.pixel
    val hunkFg = MaterialTheme.colorScheme.primary
    val hunkBg = MaterialTheme.colorScheme.surfaceContainerHigh
    val plainFg = MaterialTheme.colorScheme.onSurface
    val addBg = pixel.ok.copy(alpha = 0.14f)
    val delBg = pixel.danger.copy(alpha = 0.14f)
    val text = remember(lines, pixel, hunkFg, plainFg) {
        buildAnnotatedString {
            lines.forEachIndexed { i, line ->
                val color = when {
                    line.startsWith("@@") -> hunkFg
                    line.startsWith("+") -> pixel.ok
                    line.startsWith("-") -> pixel.danger
                    else -> plainFg
                }
                withStyle(SpanStyle(color = color)) { append(line.ifEmpty { " " }) }
                if (i < lines.lastIndex) append('\n')
            }
        }
    }
    val backgrounds = remember(lines, addBg, delBg, hunkBg) {
        lines.map { line ->
            when {
                line.startsWith("@@") -> hunkBg
                line.startsWith("+") -> addBg
                line.startsWith("-") -> delBg
                else -> Color.Transparent
            }
        }
    }
    var layout by remember { mutableStateOf<TextLayoutResult?>(null) }
    val density = LocalDensity.current
    val padX = with(density) { 10.dp.toPx() }
    val padY = with(density) { 4.dp.toPx() }
    BoxWithConstraints(modifier = Modifier.fillMaxWidth()) {
        val viewport = maxWidth
        Row(modifier = Modifier.horizontalScroll(rememberScrollState())) {
            Text(
                text = text,
                style = MaterialTheme.typography.bodySmall.copy(
                    fontFamily = CodeFontFamily,
                    fontSize = 12.sp,
                    lineHeight = 17.sp,
                ),
                softWrap = false,
                onTextLayout = { layout = it },
                modifier = Modifier
                    .widthIn(min = viewport)
                    .drawBehind {
                        val result = layout ?: return@drawBehind
                        val count = minOf(result.lineCount, backgrounds.size)
                        for (i in 0 until count) {
                            val bg = backgrounds[i]
                            if (bg == Color.Transparent) continue
                            val top = padY + result.getLineTop(i)
                            drawRect(bg, topLeft = Offset(0f, top), size = Size(size.width, result.getLineBottom(i) - result.getLineTop(i)))
                        }
                    }
                    .padding(horizontal = with(density) { padX.toDp() }, vertical = with(density) { padY.toDp() }),
            )
        }
    }
}

const val CHAT_SUBAGENT_CARD_TAG = "chat_subagent_card"
const val CHAT_SUBAGENT_STOP_TAG = "chat_subagent_stop"

private const val SUBAGENT_TASK_LINES = 3
private const val SUBAGENT_RESULT_LINES = 6

/**
 * What the subagent cards and groups of one timeline share. [open] holds the
 * keys of the open cards and groups; [stopping] the subagent ids whose Stop
 * waits for the agent.
 */
@Immutable
class SubagentCardContext(
    val tree: ChatSubagentTree,
    val waitingToolIds: Set<String>,
    val stopping: Set<String>,
    val canAct: Boolean,
    val open: Set<String>,
    val onToggle: (String) -> Unit,
    val onStop: (String) -> Unit,
)

/**
 * A subagent. Collapsed: its head and one line of progress or result. Open:
 * also its task, checklist, tool calls, the subagents it started, result and
 * Stop. A card that needs the user is always open.
 */
@Composable
fun ChatSubagentCard(
    item: ChatItem.Subagent,
    context: SubagentCardContext,
    modifier: Modifier = Modifier,
    nested: Boolean = false,
) {
    val pixel = MaterialTheme.pixel
    val muted = MaterialTheme.colorScheme.onSurfaceVariant
    val tree = context.tree
    val waitingToolIds = context.waitingToolIds
    val tools = tree.tools[item.subagentId].orEmpty()
    val children = tree.children[item.subagentId].orEmpty()
    val needsYou = subagentNeedsYou(item, tree, waitingToolIds)
    val cardKey = subagentCardKey(item.id)
    val open = needsYou || cardKey in context.open
    val padding = if (nested) 10.dp else 12.dp
    Column(
        modifier = modifier
            .fillMaxWidth()
            .testTag(CHAT_SUBAGENT_CARD_TAG)
            .background(if (nested) MaterialTheme.colorScheme.surfaceContainer else MaterialTheme.colorScheme.surfaceContainerLow)
            .pixelFrame(if (item.state == SubagentState.REQUIRES_ACTION) pixel.brand2 else pixel.surface2)
            .padding(horizontal = padding, vertical = 6.dp),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(min = 48.dp)
                .then(
                    if (needsYou) {
                        Modifier.semantics(mergeDescendants = true) {}
                    } else {
                        Modifier
                            .clickable(
                                role = Role.Button,
                                onClickLabel = if (open) "Collapse" else "Expand",
                                onClick = { context.onToggle(cardKey) },
                            )
                            .semantics(mergeDescendants = true) {
                                stateDescription = if (open) "Expanded" else "Collapsed"
                            }
                    },
                ),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(
                imageVector = toolKindIcon("think"),
                contentDescription = null,
                tint = muted,
                modifier = Modifier.size(20.dp),
            )
            Spacer(modifier = Modifier.width(10.dp))
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = item.title,
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                item.description?.let { description ->
                    Text(
                        text = description,
                        style = MaterialTheme.typography.bodySmall,
                        color = muted,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                if (!open) SubagentSummary(item, tools)
            }
            SubagentStateChip(item, modifier = Modifier.padding(start = 8.dp))
            if (!needsYou) {
                Icon(
                    imageVector = if (open) Icons.Default.KeyboardArrowUp else Icons.Default.KeyboardArrowDown,
                    contentDescription = null,
                    tint = muted,
                    modifier = Modifier.padding(start = 4.dp).size(18.dp),
                )
            }
        }
        if (!open) return@Column
        item.task?.let { task -> SubagentTask(item.id, task) }
        if (item.plan.isNotEmpty()) SubagentChecklist(item)
        if (tools.isNotEmpty()) {
            SubagentTools(
                item = item,
                tools = tools,
                waitingToolIds = waitingToolIds,
            )
        }
        if (children.isNotEmpty()) {
            Column(
                modifier = Modifier.padding(top = 4.dp, bottom = 4.dp, start = 8.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                subagentEntries(children, tree, waitingToolIds).forEach { entry ->
                    when (entry) {
                        is SubagentEntry.Card -> ChatSubagentCard(entry.card, context, nested = true)
                        is SubagentEntry.Group -> ChatSubagentGroup(
                            key = entry.key,
                            cards = entry.cards,
                            open = entry.key in context.open,
                            context = context,
                            nested = true,
                        )
                    }
                }
            }
        }
        item.result?.let { result -> SubagentResult(item.id, result) }
        if (item.canCancel) {
            Row(modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp), horizontalArrangement = Arrangement.End) {
                Box(modifier = Modifier.minimumInteractiveComponentSize(), contentAlignment = Alignment.Center) {
                    PixelButton(
                        text = "Stop",
                        onClick = { context.onStop(item.subagentId) },
                        style = PixelButtonStyle.DangerOutline,
                        enabled = context.canAct && item.subagentId !in context.stopping,
                        compact = true,
                        modifier = Modifier
                            .testTag(CHAT_SUBAGENT_STOP_TAG)
                            .semantics { contentDescription = "Stop ${item.title}" },
                    )
                }
            }
        }
    }
}

/** A collapsed card's one line: the newest tool call while it runs, else the start of its result. */
@Composable
private fun SubagentSummary(item: ChatItem.Subagent, tools: List<ChatItem.Tool>) {
    val newest = tools.lastOrNull()
    val strong = MaterialTheme.colorScheme.onSurface
    val text = when {
        item.state == SubagentState.RUNNING && newest != null -> buildAnnotatedString {
            append(subagentToolsLabel(tools.size))
            append(" · ")
            withStyle(SpanStyle(color = strong)) { append(subagentToolTitle(item, newest).ifBlank { "Tool call" }) }
        }
        else -> subagentResultPreview(item.result)?.let(::AnnotatedString)
            ?: tools.takeIf { it.isNotEmpty() }?.let { AnnotatedString(subagentToolsLabel(it.size)) }
    } ?: return
    Text(
        text = text,
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        maxLines = 1,
        overflow = TextOverflow.Ellipsis,
    )
}

const val CHAT_SUBAGENT_GROUP_TAG = "chat_subagent_group"

/** Two or more subagents behind one head: how many, and how many run, are done or failed. */
@Composable
fun ChatSubagentGroup(
    key: String,
    cards: List<ChatItem.Subagent>,
    open: Boolean,
    context: SubagentCardContext,
    modifier: Modifier = Modifier,
    nested: Boolean = false,
) {
    val pixel = MaterialTheme.pixel
    val muted = MaterialTheme.colorScheme.onSurfaceVariant
    val running = cards.any { it.state == SubagentState.RUNNING }
    val summary = subagentGroupSummary(cards)
    val padding = if (nested) 10.dp else 12.dp
    Column(
        modifier = modifier
            .fillMaxWidth()
            .testTag(CHAT_SUBAGENT_GROUP_TAG)
            .background(if (nested) MaterialTheme.colorScheme.surfaceContainer else MaterialTheme.colorScheme.surfaceContainerLow)
            .pixelFrame(pixel.surface2)
            .padding(horizontal = padding, vertical = 6.dp),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(min = 48.dp)
                .clickable(
                    role = Role.Button,
                    onClickLabel = if (open) "Hide subagents" else "Show subagents",
                    onClick = { context.onToggle(key) },
                )
                .semantics(mergeDescendants = true) { stateDescription = if (open) "Expanded" else "Collapsed" },
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(modifier = Modifier.size(20.dp), contentAlignment = Alignment.Center) {
                if (running) {
                    PixelBlinkCursor(height = 12.dp)
                } else {
                    Icon(imageVector = toolKindIcon("think"), contentDescription = null, tint = muted, modifier = Modifier.size(20.dp))
                }
            }
            Spacer(modifier = Modifier.width(10.dp))
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = subagentsLabel(cards.size),
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 1,
                )
                if (summary.isNotEmpty()) {
                    Text(
                        text = summary,
                        style = MaterialTheme.typography.bodySmall,
                        color = muted,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
            Icon(
                imageVector = if (open) Icons.Default.KeyboardArrowUp else Icons.Default.KeyboardArrowDown,
                contentDescription = null,
                tint = muted,
                modifier = Modifier.padding(start = 4.dp).size(18.dp),
            )
        }
        if (open) {
            Column(
                modifier = Modifier.padding(top = 2.dp, bottom = 6.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                cards.forEach { card -> ChatSubagentCard(card, context, nested = true) }
            }
        }
    }
}

/** The state chip label; null for a state without one. */
fun subagentStateLabel(state: String, stopReason: String?): String? = when (state) {
    SubagentState.RUNNING -> "Running"
    SubagentState.REQUIRES_ACTION -> "Needs you"
    SubagentState.IDLE -> when (stopReason) {
        "cancelled" -> "Stopped"
        "error" -> "Failed"
        else -> "Done"
    }
    else -> null
}

/** The tool call title inside its card, without the "<card title>: " the agent puts before it. */
fun subagentToolTitle(card: ChatItem.Subagent, tool: ChatItem.Tool): String = tool.title.removePrefix("${card.title}: ")

fun subagentToolsLabel(count: Int): String = if (count == 1) "1 tool call" else "$count tool calls"

@Composable
private fun SubagentStateChip(item: ChatItem.Subagent, modifier: Modifier = Modifier) {
    val label = subagentStateLabel(item.state, item.stopReason) ?: return
    val pixel = MaterialTheme.pixel
    val tone = when {
        item.state == SubagentState.REQUIRES_ACTION -> pixel.warning
        item.state == SubagentState.RUNNING -> pixel.accent
        item.stopReason == "error" -> pixel.danger
        item.stopReason == "cancelled" -> pixel.gray
        else -> pixel.ok
    }
    Row(modifier = modifier, verticalAlignment = Alignment.CenterVertically) {
        if (item.state == SubagentState.RUNNING) {
            PixelBlinkCursor(height = 12.dp, modifier = Modifier.padding(end = 6.dp))
        }
        PixelChip(label, tone = tone)
    }
}

@Composable
private fun SubagentTask(id: String, task: String) {
    var expanded by rememberSaveable(id, "task") { mutableStateOf(false) }
    var overflows by remember(task) { mutableStateOf(false) }
    Text(
        text = task,
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        maxLines = if (expanded) Int.MAX_VALUE else SUBAGENT_TASK_LINES,
        overflow = TextOverflow.Ellipsis,
        onTextLayout = { if (!expanded) overflows = it.hasVisualOverflow },
        modifier = Modifier
            .fillMaxWidth()
            .then(
                if (overflows || expanded) {
                    Modifier.clickable(role = Role.Button, onClickLabel = if (expanded) "Show less" else "Show more") {
                        expanded = !expanded
                    }
                } else {
                    Modifier
                },
            )
            .padding(vertical = 4.dp),
    )
}

@Composable
private fun SubagentChecklist(item: ChatItem.Subagent) {
    var expanded by rememberSaveable(item.id, "plan") { mutableStateOf(false) }
    val done = item.plan.count { it.status == "completed" }
    SubagentToggleRow(
        text = AnnotatedString("Checklist $done/${item.plan.size}"),
        expanded = expanded,
        onClickLabel = if (expanded) "Hide checklist" else "Show checklist",
        onToggle = { expanded = !expanded },
    )
    if (expanded) {
        Column(
            modifier = Modifier.padding(start = 4.dp, bottom = 6.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            item.plan.forEach { entry -> PlanEntryRow(entry) }
        }
    }
}

@Composable
private fun SubagentTools(item: ChatItem.Subagent, tools: List<ChatItem.Tool>, waitingToolIds: Set<String>) {
    var expanded by rememberSaveable(item.id, "tools") { mutableStateOf(false) }
    val waiting = tools.any { it.id in waitingToolIds }
    LaunchedEffect(waiting) { if (waiting) expanded = true }
    val strong = MaterialTheme.colorScheme.onSurface
    val text = buildAnnotatedString {
        append(subagentToolsLabel(tools.size))
        if (item.state == SubagentState.RUNNING) {
            append(" · ")
            withStyle(SpanStyle(color = strong)) {
                append(subagentToolTitle(item, tools.last()).ifBlank { "Tool call" })
            }
        }
    }
    SubagentToggleRow(
        text = text,
        expanded = expanded,
        onClickLabel = if (expanded) "Hide tool calls" else "Show tool calls",
        onToggle = { expanded = !expanded },
    )
    if (expanded) {
        Column(
            modifier = Modifier.padding(bottom = 6.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            tools.forEach { tool ->
                ChatToolCard(
                    item = tool.copy(title = subagentToolTitle(item, tool)),
                    waitingForApproval = tool.id in waitingToolIds,
                )
            }
        }
    }
}

@Composable
private fun SubagentToggleRow(
    text: AnnotatedString,
    expanded: Boolean,
    onClickLabel: String,
    onToggle: () -> Unit,
) {
    val muted = MaterialTheme.colorScheme.onSurfaceVariant
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(role = Role.Button, onClickLabel = onClickLabel, onClick = onToggle)
            .semantics { stateDescription = if (expanded) "Expanded" else "Collapsed" }
            .heightIn(min = 48.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = text,
            style = MaterialTheme.typography.bodySmall,
            color = muted,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f, fill = false),
        )
        Icon(
            imageVector = if (expanded) Icons.Default.KeyboardArrowUp else Icons.Default.KeyboardArrowDown,
            contentDescription = null,
            tint = muted,
            modifier = Modifier.size(18.dp),
        )
    }
}

@Composable
private fun SubagentResult(id: String, result: String) {
    var expanded by rememberSaveable(id, "result") { mutableStateOf(false) }
    var overflows by remember(result) { mutableStateOf(false) }
    val style = MaterialTheme.typography.bodyMedium
    val lineHeight = style.lineHeight.takeIf { it.isSpecified } ?: (style.fontSize * 1.4f)
    val maxHeight = with(LocalDensity.current) { (lineHeight * SUBAGENT_RESULT_LINES).toDp() }
    Column(modifier = Modifier.fillMaxWidth().padding(top = 4.dp)) {
        ClampedHeight(
            maxHeight = maxHeight,
            clamped = !expanded,
            onOverflow = { overflows = it },
        ) {
            SelectionContainer {
                MarkdownText(text = result, style = style)
            }
        }
        if (overflows || expanded) {
            PixelTextButton(onClick = { expanded = !expanded }) {
                Text(if (expanded) "Show less" else "Show more", style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}

/** Shows at most [maxHeight] of [content] while [clamped]; reports whether the content is taller. */
@Composable
private fun ClampedHeight(
    maxHeight: Dp,
    clamped: Boolean,
    onOverflow: (Boolean) -> Unit,
    content: @Composable () -> Unit,
) {
    Layout(content = content, modifier = Modifier.fillMaxWidth().clipToBounds()) { measurables, constraints ->
        val placeable = measurables.first().measure(constraints.copy(minHeight = 0, maxHeight = Constraints.Infinity))
        val limit = maxHeight.roundToPx()
        onOverflow(placeable.height > limit)
        val height = if (clamped) minOf(placeable.height, limit) else placeable.height
        layout(placeable.width, height) { placeable.place(0, 0) }
    }
}

@Preview(name = "Subagent card light", showBackground = true)
@Preview(name = "Subagent card dark", showBackground = true, uiMode = Configuration.UI_MODE_NIGHT_YES)
@Composable
private fun ChatSubagentCardPreview() {
    val items = listOf(
        ChatItem.Subagent(
            id = "sa:a1",
            subagentId = "a1",
            parentSubagentId = null,
            title = "Find the login code",
            description = "Explore",
            task = "Find where the login handler checks the user.",
            state = SubagentState.RUNNING,
            stopReason = null,
            result = null,
            canCancel = true,
            plan = listOf(
                ChatPlanEntry("Find the handler", "completed", "medium"),
                ChatPlanEntry("Check the user test", "in_progress", "medium"),
            ),
        ),
        ChatItem.Tool(
            id = "a1:t1",
            title = "Find the login code: Read login.py",
            kind = "read",
            status = ToolStatus.IN_PROGRESS,
            subtitle = "/project/login.py",
            locations = emptyList(),
            content = emptyList(),
            input = null,
            subagentId = "a1",
        ),
        ChatItem.Subagent(
            id = "sa:a2",
            subagentId = "a2",
            parentSubagentId = "a1",
            title = "Read the tests",
            description = null,
            task = null,
            state = SubagentState.IDLE,
            stopReason = "end_turn",
            result = "The test covers **only** the happy path.",
            canCancel = false,
            plan = emptyList(),
        ),
    )
    MobaideTheme {
        Column(modifier = Modifier.padding(16.dp)) {
            ChatSubagentCard(
                item = items.first() as ChatItem.Subagent,
                context = SubagentCardContext(
                    tree = chatSubagentTree(items),
                    waitingToolIds = emptySet(),
                    stopping = emptySet(),
                    canAct = true,
                    open = setOf(subagentCardKey("sa:a1")),
                    onToggle = {},
                    onStop = {},
                ),
            )
        }
    }
}

@Preview(name = "Tool card light", showBackground = true)
@Preview(name = "Tool card dark", showBackground = true, uiMode = Configuration.UI_MODE_NIGHT_YES)
@Composable
private fun ChatToolCardPreview() {
    MobaideTheme {
        Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            ChatToolCard(
                item = ChatItem.Tool(
                    id = "t1",
                    title = "Edit login.py",
                    kind = "edit",
                    status = ToolStatus.COMPLETED,
                    subtitle = "/home/user/proj/login.py",
                    locations = listOf(ChatLocation("/home/user/proj/login.py", 12)),
                    content = listOf(
                        ChatContentPart.Diff(
                            path = "/home/user/proj/login.py",
                            newFile = false,
                            diff = "@@ -1,2 +1,2 @@\n-if user:\n+if user is not None:\n     login(user)",
                            added = 1,
                            removed = 1,
                            truncated = false,
                        ),
                    ),
                    input = null,
                ),
                waitingForApproval = false,
            )
            ChatToolCard(
                item = ChatItem.Tool(
                    id = "t2",
                    title = "rm -rf build/",
                    kind = "execute",
                    status = ToolStatus.PENDING,
                    subtitle = "rm -rf build/",
                    locations = emptyList(),
                    content = emptyList(),
                    input = null,
                ),
                waitingForApproval = true,
            )
        }
    }
}
