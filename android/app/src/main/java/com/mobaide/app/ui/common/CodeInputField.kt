package com.mobaide.app.ui.common

import androidx.compose.foundation.border
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsFocusedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

private const val DEFAULT_CODE_LENGTH = 6

/**
 * Segmented numeric code entry: one box per digit. [value]/[onValueChange] hoist the raw string;
 * digit filtering and length capping belong to the caller (the ViewModel) — this only renders
 * [length] cells from [value] and forwards keystrokes verbatim.
 *
 * Backed by a single [BasicTextField] whose selection is pinned to the end every recomposition, so
 * entry is append-only and backspace trims the last digit. The real text field is laid out at zero
 * size (kept, never omitted) so the IME connection, autofill, and accessibility node stay intact
 * while the visible cells are drawn by [CodeCell]. [contentDescription] names the field for screen
 * readers (e.g. "Verification code" vs "Reset code").
 */
@Composable
fun CodeInputField(
    value: String,
    onValueChange: (String) -> Unit,
    length: Int = DEFAULT_CODE_LENGTH,
    enabled: Boolean = true,
    isError: Boolean = false,
    imeAction: ImeAction = ImeAction.Done,
    onImeAction: () -> Unit = {},
    autoFocus: Boolean = true,
    contentDescription: String = "Verification code",
    modifier: Modifier = Modifier,
) {
    val capped = value.take(length)
    val fieldValue = remember(capped) { TextFieldValue(capped, TextRange(capped.length)) }
    val fieldDescription = "$contentDescription, $length digits, ${capped.length} entered"
    val focusRequester = remember { FocusRequester() }
    val interactionSource = remember { MutableInteractionSource() }
    val isFocused by interactionSource.collectIsFocusedAsState()

    if (autoFocus) {
        LaunchedEffect(Unit) {
            if (enabled) focusRequester.requestFocus()
        }
    }

    BasicTextField(
        value = fieldValue,
        onValueChange = { onValueChange(it.text) },
        modifier = modifier
            .focusRequester(focusRequester)
            .semantics { this.contentDescription = fieldDescription },
        enabled = enabled,
        textStyle = TextStyle(color = Color.Transparent),
        keyboardOptions = KeyboardOptions(
            keyboardType = KeyboardType.Number,
            imeAction = imeAction,
        ),
        keyboardActions = KeyboardActions(
            onDone = { onImeAction() },
            onGo = { onImeAction() },
            onNext = { onImeAction() },
            onSearch = { onImeAction() },
            onSend = { onImeAction() },
        ),
        singleLine = true,
        interactionSource = interactionSource,
        decorationBox = { innerTextField ->
            Box(contentAlignment = Alignment.Center) {
                Box(modifier = Modifier.size(0.dp)) { innerTextField() }
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clearAndSetSemantics {},
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    val activeIndex = capped.length.coerceAtMost(length - 1)
                    repeat(length) { index ->
                        CodeCell(
                            char = capped.getOrNull(index),
                            isActive = isFocused && enabled && index == activeIndex,
                            isError = isError,
                            enabled = enabled,
                            modifier = Modifier.weight(1f),
                        )
                    }
                }
            }
        },
    )
}

@Composable
private fun CodeCell(
    char: Char?,
    isActive: Boolean,
    isError: Boolean,
    enabled: Boolean,
    modifier: Modifier = Modifier,
) {
    val colors = MaterialTheme.colorScheme
    val borderColor = when {
        isError -> colors.error
        isActive -> colors.primary
        char != null -> colors.outline
        else -> colors.outlineVariant
    }
    Box(
        modifier = modifier
            .aspectRatio(0.8f)
            .clip(RoundedCornerShape(8.dp))
            .border(
                width = if (isActive) 2.dp else 1.dp,
                color = borderColor,
                shape = RoundedCornerShape(8.dp),
            )
            .testTag("codeCell"),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text = char?.toString().orEmpty(),
            style = TextStyle(
                fontSize = 24.sp,
                fontFamily = FontFamily.Monospace,
                fontWeight = FontWeight.Medium,
                textAlign = TextAlign.Center,
                color = if (enabled) colors.onSurface else colors.onSurfaceVariant,
            ),
        )
    }
}
