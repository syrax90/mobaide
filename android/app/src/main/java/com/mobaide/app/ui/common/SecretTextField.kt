package com.mobaide.app.ui.common

import android.content.res.Configuration
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.mobaide.app.ui.theme.MobaideTheme

/**
 * Single-line secret-entry field with a reveal/hide toggle. Masks input with a
 * [PasswordVisualTransformation] unless [revealed]. Stateless and hoisted —
 * [value]/[onValueChange] and [revealed]/[onToggleRevealed] are owned by the
 * caller. Extracted from the project Linear-key field so any secret entry
 * (Codex API key, etc.) shares one widget.
 */
@Composable
fun SecretTextField(
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    revealed: Boolean,
    onToggleRevealed: () -> Unit,
    modifier: Modifier = Modifier,
    placeholder: String? = null,
    enabled: Boolean = true,
) {
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        label = { Text(label) },
        placeholder = placeholder?.let { p -> { Text(p) } },
        enabled = enabled,
        singleLine = true,
        // KeyboardType.Password tells the IME to treat this as a secret —
        // no autocorrect, suggestions, or learned-text caching of the key.
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
        visualTransformation = if (revealed) VisualTransformation.None else PasswordVisualTransformation(),
        trailingIcon = {
            IconButton(onClick = onToggleRevealed) {
                Icon(
                    imageVector = if (revealed) Icons.Default.VisibilityOff else Icons.Default.Visibility,
                    contentDescription = if (revealed) "Hide $label" else "Show $label",
                )
            }
        },
        modifier = modifier.testTag("secret-field"),
    )
}

@Preview(name = "SecretTextField light", showBackground = true)
@Preview(
    name = "SecretTextField dark",
    showBackground = true,
    uiMode = Configuration.UI_MODE_NIGHT_YES,
)
@Composable
private fun SecretTextFieldPreview() {
    var value by remember { mutableStateOf("sk-demo-1234567890") }
    var revealed by remember { mutableStateOf(false) }
    MobaideTheme {
        SecretTextField(
            value = value,
            onValueChange = { value = it },
            label = "OpenAI API key",
            revealed = revealed,
            onToggleRevealed = { revealed = !revealed },
            modifier = Modifier.padding(16.dp),
        )
    }
}
