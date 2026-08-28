package com.mobaide.app.ui.common.pixel

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.TextFieldColors
import androidx.compose.runtime.Composable
import com.mobaide.app.ui.theme.pixel

/**
 * The web field treatment for [androidx.compose.material3.OutlinedTextField]:
 * surface fill, gray boundary, accent when focused, danger when invalid.
 * The floating label stays — a deliberate platform concession vs the web's
 * label-above layout.
 */
object PixelTextFieldDefaults {

    @Composable
    fun colors(): TextFieldColors = OutlinedTextFieldDefaults.colors(
        focusedContainerColor = MaterialTheme.colorScheme.surfaceContainerHighest,
        unfocusedContainerColor = MaterialTheme.colorScheme.surfaceContainerHighest,
        errorContainerColor = MaterialTheme.colorScheme.surfaceContainerHighest,
        focusedBorderColor = MaterialTheme.colorScheme.primary,
        unfocusedBorderColor = MaterialTheme.colorScheme.outline,
        disabledBorderColor = MaterialTheme.pixel.rule,
        errorBorderColor = MaterialTheme.colorScheme.error,
        cursorColor = MaterialTheme.colorScheme.primary,
        focusedLabelColor = MaterialTheme.colorScheme.primary,
        unfocusedLabelColor = MaterialTheme.colorScheme.onSurfaceVariant,
        unfocusedPlaceholderColor = MaterialTheme.pixel.gray,
        focusedPlaceholderColor = MaterialTheme.pixel.gray,
    )
}
