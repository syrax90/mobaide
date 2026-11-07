package com.mobaide.app.ui.common.pixel

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ProvideTextStyle
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.ripple
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.mobaide.app.ui.theme.MobaideTheme
import com.mobaide.app.ui.theme.chromeCaps
import com.mobaide.app.ui.theme.pixel

/**
 * The web button variants: [Primary] = brand fill (the screen's main CTA),
 * [Ghost] = transparent with a brand-2 ring (everything else), [Danger] =
 * destructive fill, [DangerOutline] = a destructive choice beside a primary one.
 */
enum class PixelButtonStyle { Primary, Ghost, Danger, DangerOutline }

/**
 * The web `.button`: square, hard-shadowed, and on press the content travels
 * into the shadow. Default sizing matches web buttons (body-size label,
 * 48dp-tall); `compact` matches the terminal-bar buttons (11sp chrome caps,
 * 32dp visual height — callers own the dense-toolbar context).
 */
@Composable
fun PixelButton(
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    style: PixelButtonStyle = PixelButtonStyle.Ghost,
    enabled: Boolean = true,
    compact: Boolean = false,
    contentPadding: PaddingValues =
        if (compact) PaddingValues(horizontal = 10.dp, vertical = 5.dp)
        else PaddingValues(horizontal = 16.dp, vertical = 9.dp),
    interactionSource: MutableInteractionSource? = null,
    content: @Composable RowScope.() -> Unit,
) {
    val source = interactionSource ?: remember { MutableInteractionSource() }
    val pressed by source.collectIsPressedAsState()
    val pixel = MaterialTheme.pixel

    val fill: Color
    val contentColor: Color
    val ring: Color?
    val shadow: Color?
    if (!enabled) {
        fill = pixel.surface2
        contentColor = pixel.gray
        ring = pixel.rule
        shadow = null
    } else {
        when (style) {
            PixelButtonStyle.Primary -> {
                fill = pixel.brand
                contentColor = Color.White
                ring = null
                shadow = pixel.btnShadow
            }
            PixelButtonStyle.Ghost -> {
                fill = Color.Transparent
                contentColor = MaterialTheme.colorScheme.primary
                ring = pixel.brand2
                shadow = pixel.btnShadow
            }
            PixelButtonStyle.Danger -> {
                fill = pixel.dangerFill
                contentColor = Color.White
                ring = pixel.dangerFillShadow
                shadow = pixel.dangerFillShadow
            }
            PixelButtonStyle.DangerOutline -> {
                fill = Color.Transparent
                contentColor = pixel.danger
                ring = pixel.danger
                shadow = pixel.dangerFillShadow
            }
        }
    }

    val travelling = enabled && pressed
    Row(
        modifier = modifier
            .hardShadow(shadow ?: Color.Transparent, visible = shadow != null && !travelling)
            .pressTravel(travelling)
            .background(fill)
            .then(if (ring != null) Modifier.pixelFrame(ring) else Modifier)
            .clickable(
                interactionSource = source,
                indication = ripple(),
                enabled = enabled,
                role = Role.Button,
                onClick = onClick,
            )
            .defaultMinSize(
                minWidth = if (compact) 32.dp else 48.dp,
                minHeight = if (compact) 32.dp else 48.dp,
            )
            .padding(contentPadding),
        horizontalArrangement = Arrangement.Center,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        CompositionLocalProvider(LocalContentColor provides contentColor) {
            ProvideTextStyle(
                if (compact) MaterialTheme.typography.labelLarge
                else MaterialTheme.typography.bodyLarge,
            ) {
                content()
            }
        }
    }
}

/**
 * Text convenience overload. Compact buttons render the web's chrome-caps
 * treatment; regular buttons keep sentence case like web `.button` labels.
 */
@Composable
fun PixelButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    style: PixelButtonStyle = PixelButtonStyle.Ghost,
    enabled: Boolean = true,
    compact: Boolean = false,
    interactionSource: MutableInteractionSource? = null,
) {
    PixelButton(
        onClick = onClick,
        modifier = modifier,
        style = style,
        enabled = enabled,
        compact = compact,
        interactionSource = interactionSource,
    ) {
        Text(if (compact) text.chromeCaps() else text)
    }
}

/**
 * Drop-in for M3 [TextButton]: square, accent content, no shadow — the web's
 * inline link-style action.
 */
@Composable
fun PixelTextButton(
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    contentPadding: PaddingValues = ButtonDefaults.TextButtonContentPadding,
    interactionSource: MutableInteractionSource? = null,
    content: @Composable RowScope.() -> Unit,
) {
    TextButton(
        onClick = onClick,
        modifier = modifier,
        enabled = enabled,
        shape = RectangleShape,
        colors = ButtonDefaults.textButtonColors(
            contentColor = MaterialTheme.colorScheme.primary,
            disabledContentColor = MaterialTheme.pixel.gray,
        ),
        contentPadding = contentPadding,
        interactionSource = interactionSource,
        content = content,
    )
}

@Preview(name = "Buttons light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun PixelButtonPreviewLight() {
    MobaideTheme(darkTheme = false) { PixelButtonPreviewContent() }
}

@Preview(name = "Buttons dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun PixelButtonPreviewDark() {
    MobaideTheme(darkTheme = true) { PixelButtonPreviewContent() }
}

@Composable
private fun PixelButtonPreviewContent() {
    Column(
        modifier = Modifier.padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        PixelButton(text = "New session", onClick = {}, style = PixelButtonStyle.Primary)
        PixelButton(text = "Refresh", onClick = {})
        PixelButton(text = "Delete", onClick = {}, style = PixelButtonStyle.Danger)
        PixelButton(text = "Reject", onClick = {}, style = PixelButtonStyle.DangerOutline)
        PixelButton(text = "Disabled", onClick = {}, enabled = false)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            PixelButton(text = "Esc", onClick = {}, compact = true)
            PixelButton(text = "Tab", onClick = {}, compact = true)
        }
        PixelTextButton(onClick = {}) { Text("Forgot password?") }
    }
}
