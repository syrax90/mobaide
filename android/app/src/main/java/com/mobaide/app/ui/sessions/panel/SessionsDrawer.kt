package com.mobaide.app.ui.sessions.panel

import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.systemGestures
import androidx.compose.material3.DrawerState
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.positionChange
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.mobaide.app.ui.common.pixel.dotGrid
import com.mobaide.app.ui.theme.pixel
import kotlin.math.abs

/**
 * Host for the sessions drawer. The M3 drawer's own drag handler spans the
 * whole content, so it is enabled only while the drawer is open (swipe or
 * scrim tap to close): closed, every touch belongs to the terminal views
 * underneath, and opening is the job of [edgeSwipeToOpen] plus the top-bar
 * button.
 */
@Composable
fun SessionsDrawer(
    drawerState: DrawerState,
    drawerContent: @Composable () -> Unit,
    modifier: Modifier = Modifier,
    content: @Composable () -> Unit,
) {
    ModalNavigationDrawer(
        drawerState = drawerState,
        modifier = modifier,
        gesturesEnabled = drawerState.isOpen,
        scrimColor = MaterialTheme.pixel.scrim,
        drawerContent = {
            ModalDrawerSheet(
                drawerShape = RectangleShape,
                drawerContainerColor = MaterialTheme.colorScheme.background,
                drawerContentColor = MaterialTheme.colorScheme.onBackground,
                drawerTonalElevation = 0.dp,
                modifier = Modifier.dotGrid(MaterialTheme.colorScheme.outlineVariant),
            ) {
                drawerContent()
            }
        },
        content = content,
    )
}

/**
 * Opens the drawer on a horizontal drag that starts at the left edge.
 *
 * Applied to an ancestor of the terminal's `AndroidView`, not to a sibling
 * strip over it: Compose hit-tests the topmost child only, so a strip would
 * swallow every tap on column 0. Watching the initial pass instead lets the
 * ancestor see the touch first, bail out for anything that is not an edge
 * drag, and consume only the drag it claims — which the interop layer turns
 * into an `ACTION_CANCEL` for the Android views underneath.
 *
 * The edge is the system gesture inset plus [extra]: under gesture
 * navigation the system owns the inset itself and `systemGestureExclusion`
 * is capped at 200dp per edge, so the top-bar button stays the primary path
 * there; with three-button navigation the inset is zero and the strip is
 * just [extra] wide.
 */
@Composable
fun Modifier.edgeSwipeToOpen(
    enabled: Boolean,
    onOpen: () -> Unit,
    extra: Dp = 24.dp,
): Modifier {
    val density = LocalDensity.current
    val layoutDirection = LocalLayoutDirection.current
    val edgePx = WindowInsets.systemGestures.getLeft(density, layoutDirection) +
        with(density) { extra.toPx() }
    if (!enabled) return this
    return pointerInput(edgePx) {
        val slop = viewConfiguration.touchSlop
        awaitEachGesture {
            val down = awaitFirstDown(requireUnconsumed = false, pass = PointerEventPass.Initial)
            if (down.position.x > edgePx) return@awaitEachGesture
            var dx = 0f
            var dy = 0f
            while (true) {
                val event = awaitPointerEvent(PointerEventPass.Initial)
                val change = event.changes.firstOrNull { it.id == down.id } ?: return@awaitEachGesture
                if (!change.pressed) return@awaitEachGesture
                val delta = change.positionChange()
                dx += delta.x
                dy += delta.y
                if (abs(dy) > slop && abs(dy) >= abs(dx)) return@awaitEachGesture
                if (dx > slop && dx > abs(dy)) {
                    change.consume()
                    onOpen()
                    break
                }
            }
            // Claimed: the rest of the touch must not leak into the terminal.
            while (true) {
                val event = awaitPointerEvent(PointerEventPass.Initial)
                event.changes.forEach { it.consume() }
                if (event.changes.none { it.pressed }) return@awaitEachGesture
            }
        }
    }
}
