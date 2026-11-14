package com.mobaide.app.ui.common

import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController

/**
 * Hides the keyboard on a tap anywhere inside, as chat apps do outside the
 * text field. It watches the initial pass and never consumes, so the rows'
 * own clicks, text selection and list scrolling still get every touch; a
 * drag past touch slop is a scroll, and a long press starts a text selection.
 */
@Composable
fun Modifier.dismissKeyboardOnTap(): Modifier {
    val focusManager = LocalFocusManager.current
    val keyboard = LocalSoftwareKeyboardController.current
    return pointerInput(focusManager, keyboard) {
        val slop = viewConfiguration.touchSlop
        awaitEachGesture {
            val down = awaitFirstDown(requireUnconsumed = false, pass = PointerEventPass.Initial)
            while (true) {
                val event = awaitPointerEvent(PointerEventPass.Initial)
                if (event.changes.size > 1) return@awaitEachGesture
                val change = event.changes.firstOrNull { it.id == down.id } ?: return@awaitEachGesture
                if ((change.position - down.position).getDistance() > slop) return@awaitEachGesture
                if (change.uptimeMillis - down.uptimeMillis > viewConfiguration.longPressTimeoutMillis) {
                    return@awaitEachGesture
                }
                if (!change.pressed) {
                    focusManager.clearFocus()
                    keyboard?.hide()
                    return@awaitEachGesture
                }
            }
        }
    }
}
