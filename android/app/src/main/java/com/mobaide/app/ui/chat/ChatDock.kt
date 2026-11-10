package com.mobaide.app.ui.chat

import android.content.res.Configuration
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.indication
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.LibraryBooks
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.BookmarkAdd
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.filled.OpenInFull
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.outlined.AddPhotoAlternate
import androidx.compose.material.icons.outlined.CheckBoxOutlineBlank
import androidx.compose.material.icons.outlined.Checklist
import androidx.compose.material.icons.outlined.IndeterminateCheckBox
import androidx.compose.material.icons.outlined.Memory
import androidx.compose.material.icons.outlined.Psychology
import androidx.compose.material.icons.outlined.Shield
import androidx.compose.material.icons.outlined.Tune
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.minimumInteractiveComponentSize
import androidx.compose.material3.ripple
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.isCtrlPressed
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.OffsetMapping
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.text.input.TransformedText
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.mobaide.app.data.chat.ChatConfigOption
import com.mobaide.app.data.chat.ChatImageEncoder
import com.mobaide.app.data.chat.ChatPlanEntry
import com.mobaide.app.data.chat.ChatQueuedMessage
import com.mobaide.app.data.chat.ChatSlashCommand
import com.mobaide.app.ui.chat.markdown.CodeFontFamily
import com.mobaide.app.ui.common.pixel.PixelBlinkCursor
import com.mobaide.app.ui.common.pixel.PixelButton
import com.mobaide.app.ui.common.pixel.PixelButtonStyle
import com.mobaide.app.ui.common.pixel.PixelChip
import com.mobaide.app.ui.common.pixel.PixelIconButton
import com.mobaide.app.ui.common.pixel.PixelTextFieldDefaults
import com.mobaide.app.ui.common.pixel.pixelFrame
import com.mobaide.app.ui.theme.MobaideTheme
import com.mobaide.app.ui.theme.chromeCaps
import com.mobaide.app.ui.theme.pixel

const val CHAT_COMPOSER_FIELD_TAG = "chat_composer_field"
const val CHAT_SEND_BUTTON_TAG = "chat_send_button"
const val CHAT_CONFIG_CHIPS_TAG = "chat_config_chips"
const val CHAT_EXPAND_COMPOSER_TAG = "chat_expand_composer"

@Composable
fun ChatPlanBar(plan: List<ChatPlanEntry>, modifier: Modifier = Modifier) {
    var expanded by rememberSaveable { mutableStateOf(false) }
    val done = plan.count { it.status == "completed" }
    val current = plan.firstOrNull { it.status == "in_progress" } ?: plan.firstOrNull { it.status == "pending" }
    val maxHeight = (LocalConfiguration.current.screenHeightDp * 0.4f).dp
    Column(
        modifier = modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surfaceContainerLow)
            .pixelFrame(MaterialTheme.pixel.surface2),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .clickable(role = Role.Button, onClickLabel = if (expanded) "Hide plan" else "Show plan") {
                    expanded = !expanded
                }
                .heightIn(min = 48.dp)
                .padding(horizontal = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(
                Icons.Outlined.Checklist,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.primary,
                modifier = Modifier.size(18.dp),
            )
            Spacer(modifier = Modifier.width(8.dp))
            Text(
                text = "Plan · $done/${plan.size}".chromeCaps(),
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.primary,
            )
            Spacer(modifier = Modifier.width(10.dp))
            Text(
                text = if (expanded) "" else current?.content.orEmpty(),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f),
            )
            Icon(
                imageVector = if (expanded) Icons.Default.KeyboardArrowUp else Icons.Default.KeyboardArrowDown,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.size(20.dp),
            )
        }
        if (expanded) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .heightIn(max = maxHeight)
                    .verticalScroll(rememberScrollState())
                    .padding(start = 12.dp, end = 12.dp, bottom = 10.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                plan.forEach { entry -> PlanEntryRow(entry) }
            }
        }
    }
}

@Composable
private fun PlanEntryRow(entry: ChatPlanEntry) {
    val muted = MaterialTheme.colorScheme.onSurfaceVariant
    val (icon, tint, label) = when (entry.status) {
        "completed" -> Triple(Icons.Default.Check, MaterialTheme.pixel.ok, "Done")
        "in_progress" -> Triple(Icons.Outlined.IndeterminateCheckBox, MaterialTheme.colorScheme.primary, "In progress")
        else -> Triple(Icons.Outlined.CheckBoxOutlineBlank, muted, "To do")
    }
    Row(
        modifier = Modifier.semantics(mergeDescendants = true) { contentDescription = "$label: ${entry.content}" },
        verticalAlignment = Alignment.Top,
    ) {
        Icon(icon, contentDescription = null, tint = tint, modifier = Modifier.size(18.dp))
        Spacer(modifier = Modifier.width(8.dp))
        Text(
            text = entry.content,
            style = MaterialTheme.typography.bodySmall.copy(
                textDecoration = if (entry.status == "completed") TextDecoration.LineThrough else null,
            ),
            color = if (entry.status == "completed") muted else MaterialTheme.colorScheme.onSurface,
        )
    }
}

@Composable
fun ChatQueueRows(
    queue: List<ChatQueuedMessage>,
    enabled: Boolean,
    onDequeue: (String) -> Unit,
    modifier: Modifier = Modifier,
    // Non-null while the agent can take a message during the running turn.
    onSendNow: ((String) -> Unit)? = null,
    // Rows with a command in flight: their buttons wait for its answer.
    busy: Set<String> = emptySet(),
) {
    Column(modifier = modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        queue.forEach { message ->
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(MaterialTheme.colorScheme.surfaceContainerLow)
                    .pixelFrame(MaterialTheme.pixel.rule)
                    .padding(start = 10.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                PixelChip("Queued", tone = MaterialTheme.colorScheme.onSurfaceVariant)
                Spacer(modifier = Modifier.width(8.dp))
                Text(
                    text = message.text.ifBlank { if (message.images > 0) "Image" else "" }.replace('\n', ' '),
                    style = MaterialTheme.typography.bodySmall,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                if (message.images > 0) {
                    Text(
                        text = if (message.images == 1) "+1 image" else "+${message.images} images",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(start = 6.dp),
                    )
                }
                val rowEnabled = enabled && message.id !in busy
                if (onSendNow != null) {
                    IconButton(onClick = { onSendNow(message.id) }, enabled = rowEnabled) {
                        Icon(
                            Icons.AutoMirrored.Filled.Send,
                            contentDescription = "Send now",
                            modifier = Modifier.size(18.dp),
                        )
                    }
                }
                IconButton(onClick = { onDequeue(message.id) }, enabled = rowEnabled) {
                    Icon(Icons.Default.Close, contentDescription = "Remove queued message", modifier = Modifier.size(18.dp))
                }
            }
        }
    }
}

/** The select options shown as their own chips, in this order: first of each category. */
fun primaryConfigChips(config: List<ChatConfigOption>): List<ChatConfigOption> =
    listOf(ChatConfigOption.CATEGORY_MODE, ChatConfigOption.CATEGORY_MODEL, ChatConfigOption.CATEGORY_THOUGHT)
        .mapNotNull { category -> config.firstOrNull { !it.isBoolean && it.category == category } }

@Composable
fun modeTone(value: String?): Color {
    val id = value.orEmpty().lowercase()
    return when {
        "plan" in id -> MaterialTheme.colorScheme.primary
        "bypass" in id || "yolo" in id -> MaterialTheme.pixel.danger
        "accept" in id || "auto" in id -> MaterialTheme.pixel.warning
        else -> MaterialTheme.colorScheme.onSurfaceVariant
    }
}

/**
 * The mode, model and effort chips, then the other settings. They wrap onto a
 * second line rather than scroll, so no chip is ever out of sight.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ChatConfigChips(
    config: List<ChatConfigOption>,
    enabled: Boolean,
    onPick: (ChatConfigOption) -> Unit,
    onOpenSettings: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val chips = remember(config) { primaryConfigChips(config) }
    val hasOthers = config.size > chips.size
    if (chips.isEmpty() && !hasOthers) return
    FlowRow(
        modifier = modifier
            .fillMaxWidth()
            .testTag(CHAT_CONFIG_CHIPS_TAG),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        chips.forEach { option ->
            val (icon, tone) = when (option.category) {
                ChatConfigOption.CATEGORY_MODE -> Icons.Outlined.Shield to modeTone(option.selectValue)
                // A chip for the model, a brain for the effort: the same symbols as iOS and the web.
                ChatConfigOption.CATEGORY_MODEL -> Icons.Outlined.Memory to MaterialTheme.colorScheme.onSurfaceVariant
                else -> Icons.Outlined.Psychology to MaterialTheme.colorScheme.onSurfaceVariant
            }
            ConfigChip(
                icon = icon,
                label = option.currentName() ?: option.name,
                tone = tone,
                enabled = enabled,
                description = "${option.name}: ${option.currentName().orEmpty()}",
                onClick = { onPick(option) },
            )
        }
        if (hasOthers) {
            ConfigChip(
                icon = Icons.Outlined.Tune,
                label = null,
                tone = MaterialTheme.colorScheme.onSurfaceVariant,
                enabled = enabled,
                description = "More settings",
                onClick = onOpenSettings,
            )
        }
    }
}

@Composable
private fun ConfigChip(
    icon: ImageVector,
    label: String?,
    tone: Color,
    enabled: Boolean,
    description: String,
    onClick: () -> Unit,
) {
    val color = if (enabled) tone else MaterialTheme.pixel.gray
    Box(
        modifier = Modifier
            .minimumInteractiveComponentSize()
            .semantics(mergeDescendants = true) { contentDescription = description }
            .clickable(enabled = enabled, role = Role.Button, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        Row(
            modifier = Modifier
                .background(MaterialTheme.colorScheme.surfaceContainerHighest)
                .pixelFrame(color)
                .padding(horizontal = 8.dp, vertical = 5.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(icon, contentDescription = null, tint = color, modifier = Modifier.size(16.dp))
            if (label != null) {
                Spacer(modifier = Modifier.width(6.dp))
                Text(
                    text = label.chromeCaps(),
                    style = MaterialTheme.typography.labelMedium,
                    color = color,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.widthIn(max = 160.dp),
                )
                Icon(Icons.Default.ArrowDropDown, contentDescription = null, tint = color, modifier = Modifier.size(16.dp))
            }
        }
    }
}

@Composable
fun SlashSuggestions(
    suggestions: List<ChatSlashCommand>,
    onPick: (ChatSlashCommand) -> Unit,
    modifier: Modifier = Modifier,
) {
    LazyColumn(
        modifier = modifier
            .fillMaxWidth()
            .heightIn(max = (48 * 6).dp)
            .background(MaterialTheme.colorScheme.surfaceContainerLow)
            .pixelFrame(MaterialTheme.pixel.surface2),
    ) {
        items(suggestions, key = { it.name }) { command ->
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable(role = Role.Button) { onPick(command) }
                    .heightIn(min = 48.dp)
                    .padding(horizontal = 12.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = "/${command.name}",
                    style = MaterialTheme.typography.bodyMedium.copy(fontFamily = CodeFontFamily),
                    maxLines = 1,
                )
                if (command.description.isNotBlank()) {
                    Spacer(modifier = Modifier.width(10.dp))
                    Text(
                        text = command.description,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f),
                    )
                }
            }
        }
    }
}

/** Shows [hint] muted after the text while the field holds exactly the inserted `/name `. */
private class TrailingHint(private val hint: String, private val color: Color) : VisualTransformation {
    override fun filter(text: AnnotatedString): TransformedText {
        val original = text.length
        val shown = buildAnnotatedString {
            append(text)
            withStyle(SpanStyle(color = color)) { append(hint) }
        }
        return TransformedText(
            shown,
            object : OffsetMapping {
                override fun originalToTransformed(offset: Int): Int = offset
                override fun transformedToOriginal(offset: Int): Int = offset.coerceAtMost(original)
            },
        )
    }

    override fun equals(other: Any?): Boolean = other is TrailingHint && other.hint == hint && other.color == color
    override fun hashCode(): Int = hint.hashCode() * 31 + color.hashCode()
}

@Composable
fun ChatComposer(
    value: TextFieldValue,
    onValueChange: (TextFieldValue) -> Unit,
    placeholder: String,
    commandHint: String?,
    inputEnabled: Boolean,
    canAct: Boolean,
    running: Boolean,
    images: List<ChatComposerImage>,
    imagesLoading: Boolean,
    showAttach: Boolean,
    onAttach: () -> Unit,
    onRemoveImage: (Long) -> Unit,
    onOpenTemplates: () -> Unit,
    onSaveTemplate: () -> Unit,
    onSend: () -> Unit,
    onStop: () -> Unit,
    micButton: (@Composable () -> Unit)?,
    dictationBar: (@Composable () -> Unit)?,
    modifier: Modifier = Modifier,
    onExpand: (() -> Unit)? = null,
) {
    val hasContent = value.text.isNotBlank() || images.isNotEmpty()
    val showStop = running && !hasContent
    val hintColor = MaterialTheme.pixel.gray
    val transformation = remember(commandHint, value.text, hintColor) {
        if (commandHint != null) TrailingHint(commandHint, hintColor) else VisualTransformation.None
    }
    Column(modifier = modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        if (images.isNotEmpty() || imagesLoading) {
            Row(
                modifier = Modifier.horizontalScroll(rememberScrollState()),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                images.forEach { image -> Thumbnail(image, onRemove = { onRemoveImage(image.id) }) }
                if (imagesLoading) PixelBlinkCursor(height = 18.dp, modifier = Modifier.padding(8.dp))
            }
        }
        OutlinedTextField(
            value = value,
            onValueChange = onValueChange,
            enabled = inputEnabled,
            placeholder = { Text(placeholder) },
            minLines = 1,
            maxLines = 6,
            visualTransformation = transformation,
            textStyle = MaterialTheme.typography.bodyMedium,
            colors = PixelTextFieldDefaults.colors(),
            keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences),
            modifier = Modifier
                .fillMaxWidth()
                .testTag(CHAT_COMPOSER_FIELD_TAG)
                .onPreviewKeyEvent { event ->
                    val stopKey = event.key == Key.Escape || (event.isCtrlPressed && event.key == Key.C && value.selection.collapsed)
                    if (event.type == KeyEventType.KeyDown && running && stopKey) {
                        onStop()
                        true
                    } else {
                        false
                    }
                },
        )
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (dictationBar != null) {
                Box(modifier = Modifier.weight(1f)) { dictationBar() }
            } else {
                ComposerActions(
                    showAttach = showAttach,
                    attachEnabled = inputEnabled && !imagesLoading && images.size < ChatImageEncoder.MAX_IMAGES,
                    canSaveTemplate = value.text.isNotBlank(),
                    onAttach = onAttach,
                    onOpenTemplates = onOpenTemplates,
                    onSaveTemplate = onSaveTemplate,
                )
                Spacer(modifier = Modifier.weight(1f))
                micButton?.invoke()
                if (onExpand != null) {
                    // Same slot as the terminal's: right before Send.
                    PixelIconButton(
                        imageVector = Icons.Default.OpenInFull,
                        contentDescription = "Expand editor",
                        onClick = onExpand,
                        enabled = inputEnabled,
                        compact = true,
                        modifier = Modifier.testTag(CHAT_EXPAND_COMPOSER_TAG),
                    )
                }
                SendButton(
                    stop = showStop,
                    queue = running && hasContent,
                    enabled = canAct && (showStop || (hasContent && !imagesLoading)),
                    onClick = if (showStop) onStop else onSend,
                )
            }
        }
    }
}

@Composable
private fun ComposerActions(
    showAttach: Boolean,
    attachEnabled: Boolean,
    canSaveTemplate: Boolean,
    onAttach: () -> Unit,
    onOpenTemplates: () -> Unit,
    onSaveTemplate: () -> Unit,
) {
    if (showAttach) {
        PixelIconButton(
            imageVector = Icons.Outlined.AddPhotoAlternate,
            contentDescription = "Attach image",
            onClick = onAttach,
            enabled = attachEnabled,
            compact = true,
        )
    }
    PixelIconButton(
        imageVector = Icons.AutoMirrored.Filled.LibraryBooks,
        contentDescription = "Templates",
        onClick = onOpenTemplates,
        compact = true,
    )
    PixelIconButton(
        imageVector = Icons.Default.BookmarkAdd,
        contentDescription = "Save as template",
        onClick = onSaveTemplate,
        enabled = canSaveTemplate,
        compact = true,
    )
}

@Composable
private fun SendButton(stop: Boolean, queue: Boolean, enabled: Boolean, onClick: () -> Unit) {
    val label = when {
        stop -> "Stop"
        queue -> "Queue message"
        else -> "Send"
    }
    Box(
        modifier = Modifier
            .minimumInteractiveComponentSize()
            .testTag(CHAT_SEND_BUTTON_TAG),
        contentAlignment = Alignment.Center,
    ) {
        PixelButton(
            onClick = onClick,
            style = if (stop) PixelButtonStyle.Danger else PixelButtonStyle.Primary,
            enabled = enabled,
            compact = true,
            contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 12.dp, vertical = 6.dp),
            modifier = Modifier.semantics { contentDescription = label },
        ) {
            Icon(
                imageVector = if (stop) Icons.Default.Stop else Icons.AutoMirrored.Filled.Send,
                contentDescription = null,
                modifier = Modifier.size(20.dp),
            )
        }
    }
}

@Composable
private fun Thumbnail(image: ChatComposerImage, onRemove: () -> Unit) {
    Box(modifier = Modifier.size(66.dp)) {
        Image(
            bitmap = image.thumbnail,
            contentDescription = "Attached image",
            contentScale = ContentScale.Crop,
            modifier = Modifier
                .size(56.dp)
                .align(Alignment.BottomStart)
                .pixelFrame(MaterialTheme.colorScheme.outlineVariant),
        )
        val source = remember { MutableInteractionSource() }
        // A 48dp touch target pinned to the corner, so the badge keeps its overhang.
        Box(
            modifier = Modifier
                .align(Alignment.TopEnd)
                .size(48.dp)
                .clickable(
                    interactionSource = source,
                    indication = null,
                    role = Role.Button,
                    onClickLabel = "Remove image",
                    onClick = onRemove,
                ),
            contentAlignment = Alignment.TopEnd,
        ) {
            Box(
                modifier = Modifier
                    .size(28.dp)
                    .background(MaterialTheme.colorScheme.surfaceContainerHighest)
                    .pixelFrame(MaterialTheme.pixel.rule)
                    .indication(source, ripple()),
                contentAlignment = Alignment.Center,
            ) {
                Icon(Icons.Default.Close, contentDescription = "Remove image", modifier = Modifier.size(14.dp))
            }
        }
    }
}

@Preview(name = "Dock light", showBackground = true)
@Preview(name = "Dock dark", showBackground = true, uiMode = Configuration.UI_MODE_NIGHT_YES)
@Composable
private fun ChatDockPreview() {
    MobaideTheme {
        Column(modifier = Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            ChatPlanBar(
                plan = listOf(
                    ChatPlanEntry("Read the code", "completed", "high"),
                    ChatPlanEntry("Write the fix", "in_progress", "medium"),
                    ChatPlanEntry("Run the tests", "pending", "medium"),
                ),
            )
            ChatQueueRows(
                queue = listOf(ChatQueuedMessage("q:1", "Also update the README", 0)),
                enabled = true,
                onDequeue = {},
                onSendNow = {},
            )
            ChatConfigChips(
                config = listOf(
                    ChatConfigOption("mode", "Mode", null, "mode", false, "plan", false, emptyList()),
                    ChatConfigOption("model", "Model", null, "model", false, "opus", false, emptyList()),
                    ChatConfigOption("fast", "Fast", null, "model_config", true, null, false, emptyList()),
                ),
                enabled = true,
                onPick = {},
                onOpenSettings = {},
            )
            ChatComposer(
                value = TextFieldValue(""),
                onValueChange = {},
                placeholder = "Message Claude Code…",
                commandHint = null,
                inputEnabled = true,
                canAct = true,
                running = true,
                images = emptyList(),
                imagesLoading = false,
                showAttach = true,
                onAttach = {},
                onRemoveImage = {},
                onOpenTemplates = {},
                onSaveTemplate = {},
                onSend = {},
                onStop = {},
                micButton = null,
                dictationBar = null,
            )
        }
    }
}
