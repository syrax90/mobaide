package com.mobaide.app.ui.common.pixel

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.indication
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.outlined.Keyboard
import androidx.compose.material3.Icon
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.minimumInteractiveComponentSize
import androidx.compose.material3.ripple
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.mobaide.app.ui.theme.MobaideTheme
import com.mobaide.app.ui.theme.pixel

/**
 * Drop-in for M3 [androidx.compose.material3.IconButton]: the web
 * `.button.button-icon` — a square ghost plate with the brand-2 ring and hard
 * shadow, pressed content travelling into the shadow. [selected] is the web
 * `aria-pressed` state (accent ring, text-colored glyph). The plate is 40dp
 * (32dp when [compact], matching the terminal key row) centred in the 48dp
 * touch target, so the shadow never leaves the button's own bounds.
 */
@Composable
fun PixelIconButton(
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    selected: Boolean = false,
    compact: Boolean = false,
    interactionSource: MutableInteractionSource? = null,
    content: @Composable () -> Unit,
) {
    val source = interactionSource ?: remember { MutableInteractionSource() }
    val pressed by source.collectIsPressedAsState()
    val pixel = MaterialTheme.pixel

    val fill: Color
    val contentColor: Color
    val ring: Color
    val shadow: Color?
    when {
        !enabled -> {
            fill = pixel.surface2
            contentColor = pixel.gray
            ring = pixel.rule
            shadow = null
        }
        selected -> {
            fill = Color.Transparent
            contentColor = MaterialTheme.colorScheme.onSurface
            ring = MaterialTheme.colorScheme.primary
            shadow = pixel.btnShadow
        }
        else -> {
            fill = Color.Transparent
            contentColor = MaterialTheme.colorScheme.primary
            ring = pixel.brand2
            shadow = pixel.btnShadow
        }
    }

    val travelling = enabled && pressed
    Box(
        modifier = modifier
            .minimumInteractiveComponentSize()
            .clickable(
                interactionSource = source,
                indication = null,
                enabled = enabled,
                role = Role.Button,
                onClick = onClick,
            ),
        contentAlignment = Alignment.Center,
    ) {
        Box(
            modifier = Modifier
                .size(if (compact) 32.dp else 40.dp)
                .hardShadow(shadow ?: Color.Transparent, visible = shadow != null && !travelling)
                .pressTravel(travelling)
                .background(fill)
                .pixelFrame(ring)
                .indication(source, ripple()),
            contentAlignment = Alignment.Center,
        ) {
            CompositionLocalProvider(LocalContentColor provides contentColor) {
                content()
            }
        }
    }
}

/** Icon convenience overload; the glyph is 24dp, or 20dp on a compact plate. */
@Composable
fun PixelIconButton(
    imageVector: ImageVector,
    contentDescription: String?,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    selected: Boolean = false,
    compact: Boolean = false,
) {
    PixelIconButton(
        onClick = onClick,
        modifier = modifier,
        enabled = enabled,
        selected = selected,
        compact = compact,
    ) {
        Icon(
            imageVector = imageVector,
            contentDescription = contentDescription,
            modifier = Modifier.size(if (compact) 20.dp else 24.dp),
        )
    }
}

@Preview(name = "Icon buttons light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun PixelIconButtonPreviewLight() {
    MobaideTheme(darkTheme = false) { PixelIconButtonPreviewContent() }
}

@Preview(name = "Icon buttons dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun PixelIconButtonPreviewDark() {
    MobaideTheme(darkTheme = true) { PixelIconButtonPreviewContent() }
}

@Composable
private fun PixelIconButtonPreviewContent() {
    Row(
        modifier = Modifier.padding(8.dp),
        horizontalArrangement = Arrangement.spacedBy(4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        PixelIconButton(Icons.Default.Folder, "Projects", onClick = {})
        PixelIconButton(Icons.Default.Settings, "Settings", onClick = {})
        PixelIconButton(Icons.Outlined.Keyboard, "Hide keys", onClick = {}, selected = true, compact = true)
        PixelIconButton(Icons.AutoMirrored.Filled.Send, "Send", onClick = {}, compact = true)
        PixelIconButton(Icons.AutoMirrored.Filled.Send, "Send", onClick = {}, enabled = false, compact = true)
    }
}
