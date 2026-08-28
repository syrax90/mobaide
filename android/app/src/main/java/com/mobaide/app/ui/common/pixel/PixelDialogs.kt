package com.mobaide.app.ui.common.pixel

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.BottomSheetDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.ModalBottomSheetDefaults
import androidx.compose.material3.ModalBottomSheetProperties
import androidx.compose.material3.SheetState
import androidx.compose.material3.Snackbar
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.DialogProperties
import com.mobaide.app.ui.theme.pixel

/**
 * Drop-in for M3 [AlertDialog]: square bg-2 plate, surface-2 ring, 4dp hard
 * black shadow, accent title. The end/bottom padding reserves the shadow's
 * room inside the dialog window so it isn't clipped.
 */
@Composable
fun PixelAlertDialog(
    onDismissRequest: () -> Unit,
    confirmButton: @Composable () -> Unit,
    modifier: Modifier = Modifier,
    dismissButton: @Composable (() -> Unit)? = null,
    icon: @Composable (() -> Unit)? = null,
    title: @Composable (() -> Unit)? = null,
    text: @Composable (() -> Unit)? = null,
    properties: DialogProperties = DialogProperties(),
) {
    AlertDialog(
        onDismissRequest = onDismissRequest,
        confirmButton = confirmButton,
        modifier = modifier
            .padding(end = 4.dp, bottom = 4.dp)
            .hardShadow(MaterialTheme.pixel.modalShadow)
            .pixelFrame(MaterialTheme.pixel.surface2),
        dismissButton = dismissButton,
        icon = icon,
        title = title,
        text = text,
        shape = RectangleShape,
        containerColor = MaterialTheme.colorScheme.surfaceContainerHigh,
        titleContentColor = MaterialTheme.colorScheme.primary,
        textContentColor = MaterialTheme.colorScheme.onSurfaceVariant,
        properties = properties,
    )
}

/** The web system has no rounded pill handle: a 32x4dp rule-colored bar. */
@Composable
fun PixelDragHandle(modifier: Modifier = Modifier) {
    Box(
        modifier = modifier
            .padding(vertical = 12.dp)
            .width(32.dp)
            .height(4.dp)
            .background(MaterialTheme.pixel.rule),
    )
}

/**
 * Drop-in for M3 [ModalBottomSheet]: square bg-2 sheet, surface-2 ring,
 * square drag handle, web scrim.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PixelBottomSheet(
    onDismissRequest: () -> Unit,
    modifier: Modifier = Modifier,
    sheetState: SheetState = rememberModalBottomSheetState(),
    sheetMaxWidth: Dp = BottomSheetDefaults.SheetMaxWidth,
    dragHandle: @Composable (() -> Unit)? = { PixelDragHandle() },
    contentWindowInsets: @Composable () -> WindowInsets = { BottomSheetDefaults.windowInsets },
    properties: ModalBottomSheetProperties = ModalBottomSheetDefaults.properties,
    content: @Composable ColumnScope.() -> Unit,
) {
    ModalBottomSheet(
        onDismissRequest = onDismissRequest,
        modifier = modifier.pixelFrame(MaterialTheme.pixel.surface2),
        sheetState = sheetState,
        sheetMaxWidth = sheetMaxWidth,
        shape = RectangleShape,
        containerColor = MaterialTheme.colorScheme.surfaceContainerLow,
        contentColor = MaterialTheme.colorScheme.onSurface,
        scrimColor = MaterialTheme.pixel.scrim,
        dragHandle = dragHandle,
        contentWindowInsets = contentWindowInsets,
        properties = properties,
        content = content,
    )
}

/**
 * Drop-in for M3 [SnackbarHost]: bg-2 plate, surface-2 ring, 4dp hard black
 * shadow, accent action — the web toast/undo bar.
 */
@Composable
fun PixelSnackbarHost(
    hostState: SnackbarHostState,
    modifier: Modifier = Modifier,
) {
    SnackbarHost(hostState = hostState, modifier = modifier) { data ->
        Snackbar(
            snackbarData = data,
            modifier = Modifier
                .padding(12.dp)
                .padding(end = 4.dp, bottom = 4.dp)
                .hardShadow(MaterialTheme.pixel.modalShadow)
                .pixelFrame(MaterialTheme.pixel.surface2),
            shape = RectangleShape,
            containerColor = MaterialTheme.colorScheme.surfaceContainerLow,
            contentColor = MaterialTheme.colorScheme.onSurface,
            actionColor = MaterialTheme.colorScheme.primary,
            actionContentColor = MaterialTheme.colorScheme.primary,
            dismissActionContentColor = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}
