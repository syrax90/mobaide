package com.mobaide.app.ui.common.pixel

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.toggleable
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.FloatingActionButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SwitchColors
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.minimumInteractiveComponentSize
import androidx.compose.material3.ripple
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.mobaide.app.ui.theme.MobaideTheme
import com.mobaide.app.ui.theme.pixel

/**
 * The web checkbox: an 18dp square, no checkmark glyph — checked is a solid
 * brand fill ringed in brand-2.
 */
@Composable
fun PixelCheckbox(
    checked: Boolean,
    onCheckedChange: ((Boolean) -> Unit)?,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
) {
    val fill =
        if (checked) MaterialTheme.pixel.brand
        else MaterialTheme.colorScheme.surfaceContainerHighest
    val ring = if (checked) MaterialTheme.pixel.brand2 else MaterialTheme.colorScheme.outline
    Box(
        modifier = modifier
            .minimumInteractiveComponentSize()
            .then(
                if (onCheckedChange != null) {
                    Modifier.toggleable(
                        value = checked,
                        interactionSource = remember { MutableInteractionSource() },
                        indication = ripple(bounded = false, radius = 20.dp),
                        enabled = enabled,
                        role = Role.Checkbox,
                        onValueChange = onCheckedChange,
                    )
                } else {
                    Modifier
                },
            ),
        contentAlignment = Alignment.Center,
    ) {
        Box(
            modifier = Modifier
                .size(18.dp)
                .alpha(if (enabled) 1f else 0.55f)
                .background(fill)
                .pixelFrame(ring),
        )
    }
}

/**
 * The web radio: a 16dp square — selected is a brand-2 ring, a surface gap,
 * and a brand core.
 */
@Composable
fun PixelRadio(
    selected: Boolean,
    onClick: (() -> Unit)?,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
) {
    val ring = if (selected) MaterialTheme.pixel.brand2 else MaterialTheme.colorScheme.outline
    Box(
        modifier = modifier
            .minimumInteractiveComponentSize()
            .then(
                if (onClick != null) {
                    Modifier.selectable(
                        selected = selected,
                        interactionSource = remember { MutableInteractionSource() },
                        indication = ripple(bounded = false, radius = 20.dp),
                        enabled = enabled,
                        role = Role.RadioButton,
                        onClick = onClick,
                    )
                } else {
                    Modifier
                },
            ),
        contentAlignment = Alignment.Center,
    ) {
        Box(
            modifier = Modifier
                .size(16.dp)
                .alpha(if (enabled) 1f else 0.55f)
                .background(MaterialTheme.colorScheme.surfaceContainerHighest)
                .pixelFrame(ring),
            contentAlignment = Alignment.Center,
        ) {
            if (selected) {
                Box(
                    modifier = Modifier
                        .size(6.dp)
                        .background(MaterialTheme.pixel.brand),
                )
            }
        }
    }
}

/** Web has no switches; the M3 Switch stays, recolored into the system. */
@Composable
fun pixelSwitchColors(): SwitchColors = SwitchDefaults.colors(
    checkedThumbColor = Color.White,
    checkedTrackColor = MaterialTheme.pixel.brand,
    checkedBorderColor = MaterialTheme.pixel.brand2,
    uncheckedThumbColor = MaterialTheme.colorScheme.outline,
    uncheckedTrackColor = MaterialTheme.colorScheme.surfaceContainerHighest,
    uncheckedBorderColor = MaterialTheme.colorScheme.outline,
)

/**
 * Drop-in for M3 [FloatingActionButton]: square brand plate with the hard
 * shadow + press travel (the web terminal's jump button).
 */
@Composable
fun PixelFab(
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    content: @Composable () -> Unit,
) {
    val source = remember { MutableInteractionSource() }
    val pressed by source.collectIsPressedAsState()
    FloatingActionButton(
        onClick = onClick,
        modifier = modifier
            .hardShadow(MaterialTheme.pixel.btnShadow, visible = !pressed)
            .pressTravel(pressed),
        shape = RectangleShape,
        containerColor = MaterialTheme.colorScheme.primaryContainer,
        contentColor = MaterialTheme.colorScheme.onPrimaryContainer,
        elevation = FloatingActionButtonDefaults.elevation(0.dp, 0.dp, 0.dp, 0.dp),
        interactionSource = source,
        content = content,
    )
}

/** Chip ring per the web grammar: rule = decorative, brand-2 = selected. */
@Composable
fun pixelChipBorder(selected: Boolean = false, tone: Color? = null): BorderStroke =
    BorderStroke(
        2.dp,
        tone ?: if (selected) MaterialTheme.pixel.brand2 else MaterialTheme.pixel.rule,
    )

@Preview(name = "Controls dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun PixelControlsPreviewDark() {
    MobaideTheme(darkTheme = true) { PixelControlsPreviewContent() }
}

@Preview(name = "Controls light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun PixelControlsPreviewLight() {
    MobaideTheme(darkTheme = false) { PixelControlsPreviewContent() }
}

@Composable
private fun PixelControlsPreviewContent() {
    Row(
        modifier = Modifier.padding(8.dp),
        horizontalArrangement = Arrangement.spacedBy(4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        PixelCheckbox(checked = true, onCheckedChange = {})
        PixelCheckbox(checked = false, onCheckedChange = {})
        PixelRadio(selected = true, onClick = {})
        PixelRadio(selected = false, onClick = {})
        PixelFab(onClick = {}) { PixelStatusDot(Color.White) }
    }
}
