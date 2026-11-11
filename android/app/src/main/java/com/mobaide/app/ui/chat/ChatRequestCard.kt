package com.mobaide.app.ui.chat

import android.content.res.Configuration
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.HelpOutline
import androidx.compose.material.icons.automirrored.outlined.Login
import androidx.compose.material.icons.automirrored.outlined.OpenInNew
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.mobaide.app.data.chat.ChatContentPart
import com.mobaide.app.data.chat.ChatFieldType
import com.mobaide.app.data.chat.ChatFormField
import com.mobaide.app.data.chat.ChatFormOption
import com.mobaide.app.data.chat.ChatPermissionOption
import com.mobaide.app.data.chat.ChatRequest
import com.mobaide.app.data.chat.ChatSignInInput
import com.mobaide.app.data.chat.ChatSignInMethod
import com.mobaide.app.ui.chat.markdown.CodeFontFamily
import com.mobaide.app.ui.common.CopyableCodeBlock
import com.mobaide.app.ui.common.SecretTextField
import com.mobaide.app.ui.common.pixel.PixelBlinkCursor
import com.mobaide.app.ui.common.pixel.PixelButton
import com.mobaide.app.ui.common.pixel.PixelButtonStyle
import com.mobaide.app.ui.common.pixel.PixelCheckbox
import com.mobaide.app.ui.common.pixel.PixelRadio
import com.mobaide.app.ui.common.pixel.PixelTextButton
import com.mobaide.app.ui.common.pixel.PixelTextFieldDefaults
import com.mobaide.app.ui.common.pixel.hardShadow
import com.mobaide.app.ui.common.pixel.pixelFrame
import com.mobaide.app.ui.common.pixel.pixelSwitchColors
import com.mobaide.app.ui.theme.MobaideTheme
import com.mobaide.app.ui.theme.chromeCaps
import com.mobaide.app.ui.theme.pixel
import com.mobaide.app.ui.util.openUrlInCustomTab
import kotlinx.serialization.json.JsonPrimitive

const val CHAT_REQUEST_CARD_TAG = "chat_request_card"
const val CHAT_SIGN_IN_OPEN_TAG = "chat_sign_in_open"
const val CHAT_SIGN_IN_FIELD_TAG = "chat_sign_in_field"

/**
 * The oldest pending permission prompt or question, raised above the composer.
 * Never modal: the timeline stays scrollable behind it.
 */
@Composable
fun ChatRequestCard(
    request: ChatRequest,
    moreWaiting: Int,
    answering: String?,
    error: String?,
    draft: Map<String, ChatFormValue>?,
    enabled: Boolean,
    onPermission: (ChatRequest.Permission, String) -> Unit,
    onFormValue: (ChatRequest.Form, String, ChatFormValue) -> Unit,
    onFormAction: (ChatRequest.Form, String) -> Unit,
    onSignInStart: (ChatRequest.SignIn, String) -> Unit,
    onSignInSubmit: (ChatRequest.SignIn, String) -> Unit,
    onSignInCancel: (ChatRequest.SignIn) -> Unit,
    modifier: Modifier = Modifier,
) {
    val maxHeight = (LocalConfiguration.current.screenHeightDp * 0.5f).dp
    val (label, icon) = when (request) {
        is ChatRequest.Permission -> "Permission needed" to toolKindIcon(request.toolKind)
        is ChatRequest.Form -> "Question" to Icons.AutoMirrored.Outlined.HelpOutline
        is ChatRequest.SignIn -> "Sign-in needed" to Icons.AutoMirrored.Outlined.Login
        is ChatRequest.Other -> "Waiting for you" to Icons.AutoMirrored.Outlined.HelpOutline
    }
    Column(
        modifier = modifier
            .fillMaxWidth()
            .padding(end = 4.dp, bottom = 4.dp)
            .hardShadow(MaterialTheme.pixel.btnShadow)
            .background(MaterialTheme.colorScheme.surfaceContainerLow)
            .pixelFrame(MaterialTheme.pixel.brand2)
            .padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(icon, contentDescription = null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(18.dp))
            Spacer(modifier = Modifier.width(8.dp))
            Text(
                text = label.chromeCaps(),
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.primary,
                modifier = Modifier
                    .weight(1f)
                    .semantics { heading() },
            )
            if (moreWaiting > 0) {
                Text(
                    text = "+$moreWaiting more waiting",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        when (request) {
            is ChatRequest.Permission -> PermissionBody(request, answering, enabled, maxHeight, onPermission)
            is ChatRequest.Form -> FormBody(request, draft, answering, enabled, maxHeight, onFormValue, onFormAction)
            is ChatRequest.SignIn -> SignInBody(
                request = request,
                answering = answering,
                enabled = enabled,
                maxHeight = maxHeight,
                onStart = onSignInStart,
                onSubmit = onSignInSubmit,
                onCancel = onSignInCancel,
            )
            is ChatRequest.Other -> Text(text = request.title, style = MaterialTheme.typography.bodyMedium)
        }
        val agentError = (request as? ChatRequest.SignIn)?.error?.takeIf { it.isNotBlank() }
        (error ?: agentError)?.let {
            Text(text = it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.pixel.danger)
        }
    }
}

/**
 * Sign-in to the coding agent on the PC: pick a method, then open the sign-in
 * page, enter the one-time code shown here on it, or paste the code it shows.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun androidx.compose.foundation.layout.ColumnScope.SignInBody(
    request: ChatRequest.SignIn,
    answering: String?,
    enabled: Boolean,
    maxHeight: Dp,
    onStart: (ChatRequest.SignIn, String) -> Unit,
    onSubmit: (ChatRequest.SignIn, String) -> Unit,
    onCancel: (ChatRequest.SignIn) -> Unit,
) {
    val context = LocalContext.current
    // Not saveable: the field can hold an API key.
    var value by remember(request.id, request.stage) { mutableStateOf("") }
    val input = request.input?.takeIf { request.waiting }
    val canSubmit = enabled && answering == null && !request.busy && value.isNotBlank()
    val submit = { if (canSubmit) onSubmit(request, value) }
    Column(
        modifier = Modifier
            .weight(1f, fill = false)
            .heightIn(max = maxHeight)
            .verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(
            text = request.title,
            style = MaterialTheme.typography.bodyLarge,
            fontWeight = FontWeight.SemiBold,
        )
        if (request.text.isNotBlank()) {
            Text(text = request.text, style = MaterialTheme.typography.bodyMedium)
        }
        if (!request.waiting && request.methods.size > 1) {
            request.methods.forEach { method -> SignInMethodLine(method) }
        }
        if (request.waiting && request.url != null) {
            PixelButton(
                onClick = { openUrlInCustomTab(context, request.url) },
                style = PixelButtonStyle.Primary,
                enabled = enabled,
                modifier = Modifier.testTag(CHAT_SIGN_IN_OPEN_TAG),
            ) {
                Icon(Icons.AutoMirrored.Outlined.OpenInNew, contentDescription = null, modifier = Modifier.size(18.dp))
                Spacer(modifier = Modifier.width(8.dp))
                Text("Open sign-in page")
            }
        }
        if (request.waiting && request.userCode != null) {
            CopyableCodeBlock(label = "One-time code", code = request.userCode)
        }
        if (input != null) {
            SignInField(
                input = input,
                value = value,
                enabled = enabled && answering == null && !request.busy,
                onChange = { value = it },
                onDone = submit,
            )
        }
        if (request.waiting && request.busy && input == null) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                PixelBlinkCursor(height = 14.dp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Spacer(modifier = Modifier.width(8.dp))
                Text(
                    text = if (request.url == null) {
                        "Starting the sign-in…"
                    } else {
                        "Waiting for you to finish on the sign-in page…"
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
    FlowRow(
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        if (!request.waiting) {
            request.methods.forEachIndexed { index, method ->
                val key = ChatViewModel.signInStartKey(method.id)
                PixelButton(
                    onClick = { if (answering == null) onStart(request, method.id) },
                    style = if (index == 0) PixelButtonStyle.Primary else PixelButtonStyle.Ghost,
                    enabled = enabled && (answering == null || answering == key),
                ) {
                    if (answering == key) {
                        PixelBlinkCursor(height = 14.dp, color = androidx.compose.material3.LocalContentColor.current)
                        Spacer(modifier = Modifier.width(8.dp))
                    }
                    Text(if (request.methods.size == 1) "Sign in" else method.name)
                }
            }
        }
        if (input != null) {
            val sending = request.busy || answering == ChatViewModel.SIGN_IN_SUBMIT
            PixelButton(onClick = submit, style = PixelButtonStyle.Primary, enabled = canSubmit || sending) {
                if (sending) {
                    PixelBlinkCursor(height = 14.dp, color = androidx.compose.material3.LocalContentColor.current)
                    Spacer(modifier = Modifier.width(8.dp))
                }
                Text("Submit")
            }
        }
        PixelTextButton(
            onClick = { if (answering == null) onCancel(request) },
            enabled = enabled && (answering == null || answering == ChatViewModel.SIGN_IN_CANCEL),
        ) { Text(if (request.waiting) "Cancel" else "Not now") }
    }
}

@Composable
private fun SignInMethodLine(method: ChatSignInMethod) {
    Text(
        text = buildAnnotatedString {
            withStyle(SpanStyle(fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface)) {
                append(method.name)
            }
            if (method.description.isNotBlank()) append(" — ${method.description}")
        },
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

@Composable
private fun SignInField(
    input: ChatSignInInput,
    value: String,
    enabled: Boolean,
    onChange: (String) -> Unit,
    onDone: () -> Unit,
) {
    val label = input.label.ifBlank { "Code" }
    if (input.secret) {
        // Not saveable: it guards an API key.
        var revealed by remember { mutableStateOf(false) }
        SecretTextField(
            value = value,
            onValueChange = onChange,
            label = label,
            revealed = revealed,
            onToggleRevealed = { revealed = !revealed },
            enabled = enabled,
            imeAction = ImeAction.Done,
            keyboardActions = KeyboardActions(onDone = { onDone() }),
            modifier = Modifier
                .fillMaxWidth()
                .testTag(CHAT_SIGN_IN_FIELD_TAG),
        )
        return
    }
    OutlinedTextField(
        value = value,
        onValueChange = onChange,
        enabled = enabled,
        singleLine = true,
        label = { Text(label) },
        colors = PixelTextFieldDefaults.colors(),
        keyboardOptions = KeyboardOptions(
            keyboardType = KeyboardType.Ascii,
            autoCorrectEnabled = false,
            imeAction = ImeAction.Done,
        ),
        keyboardActions = KeyboardActions(onDone = { onDone() }),
        modifier = Modifier
            .fillMaxWidth()
            .testTag(CHAT_SIGN_IN_FIELD_TAG),
    )
}

@Composable
private fun androidx.compose.foundation.layout.ColumnScope.PermissionBody(
    request: ChatRequest.Permission,
    answering: String?,
    enabled: Boolean,
    maxHeight: Dp,
    onPermission: (ChatRequest.Permission, String) -> Unit,
) {
    val haptic = LocalHapticFeedback.current
    Column(
        modifier = Modifier
            .weight(1f, fill = false)
            .heightIn(max = maxHeight)
            .verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(
            text = request.title,
            style = MaterialTheme.typography.bodyLarge,
            fontWeight = FontWeight.SemiBold,
        )
        request.subtitle?.takeIf { it.isNotBlank() && it != request.title }?.let {
            Text(
                text = it,
                style = MaterialTheme.typography.bodySmall.copy(fontFamily = CodeFontFamily, fontSize = 12.sp),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        if (request.content.isNotEmpty()) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .heightIn(max = 200.dp)
                    .verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                request.content.forEach { part -> ContentPartView(part) }
            }
        }
    }
    val firstAllowOnce = request.options.indexOfFirst { it.kind == "allow_once" }
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        request.options.forEachIndexed { index, option ->
            val busy = answering == option.id
            PixelButton(
                onClick = {
                    if (answering == null) {
                        haptic.performHapticFeedback(HapticFeedbackType.LongPress)
                        onPermission(request, option.id)
                    }
                },
                style = permissionStyle(option, index == firstAllowOnce),
                enabled = enabled && (answering == null || busy),
                modifier = Modifier.fillMaxWidth(),
            ) {
                if (busy) {
                    PixelBlinkCursor(height = 14.dp, color = androidx.compose.material3.LocalContentColor.current)
                    Spacer(modifier = Modifier.width(8.dp))
                }
                Text(option.name)
            }
        }
    }
}

private fun permissionStyle(option: ChatPermissionOption, primary: Boolean): PixelButtonStyle = when {
    primary -> PixelButtonStyle.Primary
    option.isReject -> PixelButtonStyle.DangerOutline
    else -> PixelButtonStyle.Ghost
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun androidx.compose.foundation.layout.ColumnScope.FormBody(
    request: ChatRequest.Form,
    draft: Map<String, ChatFormValue>?,
    answering: String?,
    enabled: Boolean,
    maxHeight: Dp,
    onFormValue: (ChatRequest.Form, String, ChatFormValue) -> Unit,
    onFormAction: (ChatRequest.Form, String) -> Unit,
) {
    val values = draft ?: initialFormDraft(request.fields)
    val inputsEnabled = enabled && answering == null
    Column(
        modifier = Modifier
            .weight(1f, fill = false)
            .heightIn(max = maxHeight)
            .verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(
            text = request.title,
            style = MaterialTheme.typography.bodyLarge,
            fontWeight = FontWeight.SemiBold,
        )
        request.fields.forEach { field ->
            FormFieldView(
                field = field,
                value = values[field.key],
                enabled = inputsEnabled,
                onChange = { onFormValue(request, field.key, it) },
            )
        }
    }
    val canSubmit = formValues(request, draft.orEmpty()) != null
    FlowRow(
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        PixelButton(
            onClick = { if (answering == null) onFormAction(request, ChatViewModel.FORM_ACCEPT) },
            style = PixelButtonStyle.Primary,
            enabled = enabled && canSubmit && (answering == null || answering == ChatViewModel.FORM_ACCEPT),
        ) {
            if (answering == ChatViewModel.FORM_ACCEPT) {
                PixelBlinkCursor(height = 14.dp, color = androidx.compose.material3.LocalContentColor.current)
                Spacer(modifier = Modifier.width(8.dp))
            }
            Text("Submit")
        }
        PixelButton(
            onClick = { if (answering == null) onFormAction(request, ChatViewModel.FORM_DECLINE) },
            enabled = enabled && (answering == null || answering == ChatViewModel.FORM_DECLINE),
        ) { Text("Decline") }
        PixelTextButton(
            onClick = { if (answering == null) onFormAction(request, ChatViewModel.FORM_CANCEL) },
            enabled = enabled && (answering == null || answering == ChatViewModel.FORM_CANCEL),
        ) { Text("Cancel") }
    }
}

@Composable
private fun FormFieldView(
    field: ChatFormField,
    value: ChatFormValue?,
    enabled: Boolean,
    onChange: (ChatFormValue) -> Unit,
) {
    val title = if (field.required) "${field.title} *" else field.title
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        if (field.type != ChatFieldType.Boolean) {
            Text(text = title, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold)
            field.description?.takeIf { it.isNotBlank() }?.let {
                Text(text = it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        when (field.type) {
            ChatFieldType.Select -> {
                val selected = (value as? ChatFormValue.Choice)?.value
                field.options.forEach { option ->
                    OptionRow(
                        option = option,
                        selected = option.value == selected,
                        multi = false,
                        enabled = enabled,
                        onClick = { onChange(ChatFormValue.Choice(option.value)) },
                    )
                }
            }
            ChatFieldType.MultiSelect -> {
                val selected = (value as? ChatFormValue.Choices)?.values.orEmpty()
                field.options.forEach { option ->
                    val on = option.value in selected
                    OptionRow(
                        option = option,
                        selected = on,
                        multi = true,
                        enabled = enabled,
                        onClick = {
                            onChange(ChatFormValue.Choices(if (on) selected - option.value else selected + option.value))
                        },
                    )
                }
            }
            ChatFieldType.Boolean -> {
                val checked = (value as? ChatFormValue.Toggle)?.value == true
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .toggleable(
                            value = checked,
                            enabled = enabled,
                            role = Role.Switch,
                            onValueChange = { onChange(ChatFormValue.Toggle(it)) },
                        )
                        .heightIn(min = 48.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text(text = title, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold)
                        field.description?.takeIf { it.isNotBlank() }?.let {
                            Text(
                                text = it,
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                    Switch(checked = checked, onCheckedChange = null, enabled = enabled, colors = pixelSwitchColors())
                }
            }
            ChatFieldType.Number, ChatFieldType.Integer, ChatFieldType.Text, ChatFieldType.Unknown -> {
                val text = (value as? ChatFormValue.Text)?.value.orEmpty()
                val numeric = field.type == ChatFieldType.Number || field.type == ChatFieldType.Integer
                OutlinedTextField(
                    value = text,
                    onValueChange = { onChange(ChatFormValue.Text(it)) },
                    enabled = enabled,
                    singleLine = true,
                    colors = PixelTextFieldDefaults.colors(),
                    keyboardOptions = KeyboardOptions(
                        keyboardType = when (field.type) {
                            ChatFieldType.Integer -> KeyboardType.Number
                            ChatFieldType.Number -> KeyboardType.Decimal
                            else -> KeyboardType.Text
                        },
                    ),
                    isError = numeric && text.isNotBlank() && when (field.type) {
                        ChatFieldType.Integer -> text.trim().toLongOrNull() == null
                        else -> text.trim().toDoubleOrNull() == null
                    },
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        }
    }
}

@Composable
private fun OptionRow(
    option: ChatFormOption,
    selected: Boolean,
    multi: Boolean,
    enabled: Boolean,
    onClick: () -> Unit,
) {
    val rowModifier = if (multi) {
        Modifier.toggleable(value = selected, enabled = enabled, role = Role.Checkbox, onValueChange = { onClick() })
    } else {
        Modifier.selectable(selected = selected, enabled = enabled, role = Role.RadioButton, onClick = onClick)
    }
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .then(rowModifier)
            .heightIn(min = 48.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (multi) {
            PixelCheckbox(checked = selected, onCheckedChange = null, enabled = enabled)
        } else {
            PixelRadio(selected = selected, onClick = null, enabled = enabled)
        }
        Column(modifier = Modifier.weight(1f)) {
            Text(text = option.label, style = MaterialTheme.typography.bodyMedium)
            option.description?.takeIf { it.isNotBlank() }?.let {
                Text(text = it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

@Preview(name = "Request card light", showBackground = true)
@Preview(name = "Request card dark", showBackground = true, uiMode = Configuration.UI_MODE_NIGHT_YES)
@Composable
private fun ChatRequestCardPreview() {
    MobaideTheme {
        Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
            ChatRequestCard(
                request = ChatRequest.Permission(
                    id = "r:9",
                    toolCallId = "toolu_02",
                    title = "rm -rf build/",
                    toolKind = "execute",
                    subtitle = "rm -rf build/",
                    content = listOf(ChatContentPart.Text("Removes the build folder.", truncated = false)),
                    options = listOf(
                        ChatPermissionOption("allow", "Allow once", "allow_once"),
                        ChatPermissionOption("always", "Always allow rm in this project", "allow_always"),
                        ChatPermissionOption("reject", "Reject", "reject_once"),
                    ),
                ),
                moreWaiting = 1,
                answering = null,
                error = null,
                draft = null,
                enabled = true,
                onPermission = { _, _ -> },
                onFormValue = { _, _, _ -> },
                onFormAction = { _, _ -> },
                onSignInStart = { _, _ -> },
                onSignInSubmit = { _, _ -> },
                onSignInCancel = {},
            )
            ChatRequestCard(
                request = ChatRequest.Form(
                    id = "r:12",
                    toolCallId = null,
                    title = "How should I set up the project?",
                    fields = listOf(
                        ChatFormField(
                            key = "framework",
                            title = "Framework",
                            description = null,
                            required = true,
                            type = ChatFieldType.Select,
                            options = listOf(ChatFormOption("react", "React", "Component UI"), ChatFormOption("vue", "Vue", null)),
                            default = null,
                        ),
                        ChatFormField("strict", "Strict mode", null, false, ChatFieldType.Boolean, emptyList(), JsonPrimitive(true)),
                    ),
                ),
                moreWaiting = 0,
                answering = null,
                error = "Pick a framework",
                draft = null,
                enabled = true,
                onPermission = { _, _ -> },
                onFormValue = { _, _, _ -> },
                onFormAction = { _, _ -> },
                onSignInStart = { _, _ -> },
                onSignInSubmit = { _, _ -> },
                onSignInCancel = {},
            )
            ChatRequestCard(
                request = ChatRequest.SignIn(
                    id = "sign-in",
                    toolCallId = null,
                    title = "Sign in to Codex",
                    text = "Open the sign-in page, sign in to ChatGPT and enter the code below.",
                    stage = ChatRequest.SignIn.STAGE_WAITING,
                    methods = listOf(ChatSignInMethod("chatgpt", "ChatGPT account", "Plus or Pro plan")),
                    url = "https://auth.openai.com/codex/device",
                    userCode = "CIQN-Y66ME",
                    input = null,
                    busy = true,
                    error = null,
                ),
                moreWaiting = 0,
                answering = null,
                error = null,
                draft = null,
                enabled = true,
                onPermission = { _, _ -> },
                onFormValue = { _, _, _ -> },
                onFormAction = { _, _ -> },
                onSignInStart = { _, _ -> },
                onSignInSubmit = { _, _ -> },
                onSignInCancel = {},
            )
        }
    }
}
