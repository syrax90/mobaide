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
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
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
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLayoutResult
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.mobaide.app.data.chat.ChatContentPart
import com.mobaide.app.data.chat.ChatImageDecoder
import com.mobaide.app.data.chat.ChatItem
import com.mobaide.app.data.chat.ChatLocation
import com.mobaide.app.data.chat.DecodedChatImage
import com.mobaide.app.data.chat.ToolStatus
import com.mobaide.app.ui.chat.markdown.CodeBlock
import com.mobaide.app.ui.chat.markdown.CodeFontFamily
import com.mobaide.app.ui.chat.markdown.Markdown
import com.mobaide.app.ui.chat.markdown.MarkdownText
import com.mobaide.app.ui.common.pixel.PixelBlinkCursor
import com.mobaide.app.ui.common.pixel.PixelChip
import com.mobaide.app.ui.common.pixel.PixelTextButton
import com.mobaide.app.ui.common.pixel.pixelFrame
import com.mobaide.app.ui.theme.MobaideTheme
import com.mobaide.app.ui.theme.chromeCaps
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
                        text = "Waiting for approval".chromeCaps(),
                        style = MaterialTheme.typography.labelSmall,
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
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.pixel.ok,
        )
        Text(
            text = "−$removed",
            style = MaterialTheme.typography.labelMedium,
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
                text = "Input".chromeCaps(),
                style = MaterialTheme.typography.labelMedium,
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
            if (part.newFile) PixelChip("New file", tone = pixel.ok)
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
            PixelTextButton(onClick = { showAll = true }) { Text("Show all ${lines.size} lines") }
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
