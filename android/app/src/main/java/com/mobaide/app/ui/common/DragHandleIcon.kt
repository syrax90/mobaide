package com.mobaide.app.ui.common

import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.DragHandle
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp

/** The grip a reorderable row is dragged by (Projects screen, new-session wizard). */
@Composable
fun DragHandleIcon(
    modifier: Modifier = Modifier,
    contentDescription: String = "Reorder",
    tint: Color = MaterialTheme.colorScheme.onSurfaceVariant,
) {
    Icon(
        imageVector = Icons.Default.DragHandle,
        contentDescription = contentDescription,
        tint = tint,
        modifier = modifier.size(22.dp),
    )
}
