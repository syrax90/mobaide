package com.mobaide.app.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.graphics.Color

// Role mapping intent: `primary` carries ACCENT (text/emphasis — the web's
// --accent), never the brand fill; brand purple is fill/border only and lives
// in MaterialTheme.pixel. surfaceContainer* = the bg-2 plate family,
// surfaceContainerHighest = the inset --surface, outline = --gray
// (interactive boundary), outlineVariant = --surface-2 (plate frames,
// dividers). surfaceTint = background so tonal elevation never tints.
private val DarkColorScheme = darkColorScheme(
    primary = PxDarkAccent,
    onPrimary = Color(0xFF381E72),
    primaryContainer = PxBrand,
    onPrimaryContainer = Color.White,
    inversePrimary = PxBrand,
    secondary = PxDarkTextMuted,
    onSecondary = Color(0xFF332D41),
    secondaryContainer = PxDarkSurface,
    onSecondaryContainer = PxDarkAccent,
    tertiary = PxBrand2,
    onTertiary = Color.White,
    tertiaryContainer = PxDarkSurface2,
    onTertiaryContainer = PxDarkText,
    background = PxDarkBg,
    onBackground = PxDarkText,
    surface = PxDarkBg,
    onSurface = PxDarkText,
    surfaceVariant = PxDarkSurface,
    onSurfaceVariant = PxDarkTextMuted,
    surfaceTint = PxDarkBg,
    inverseSurface = PxDarkText,
    inverseOnSurface = PxLightText,
    error = PxDarkDanger,
    onError = Color(0xFF601410),
    errorContainer = PxDangerFill,
    onErrorContainer = PxDarkDanger,
    outline = PxDarkGray,
    outlineVariant = PxDarkSurface2,
    scrim = Color.Black,
    surfaceContainerLowest = PxDarkBg,
    surfaceContainerLow = PxDarkBg2,
    surfaceContainer = PxDarkBg2,
    surfaceContainerHigh = PxDarkBg2,
    surfaceContainerHighest = PxDarkSurface,
)

private val LightColorScheme = lightColorScheme(
    primary = PxLightAccent,
    onPrimary = Color.White,
    primaryContainer = PxBrand,
    onPrimaryContainer = Color.White,
    inversePrimary = PxDarkAccent,
    secondary = PxLightTextMuted,
    onSecondary = Color.White,
    secondaryContainer = PxLightSurface,
    onSecondaryContainer = PxLightAccent,
    tertiary = PxBrand2,
    onTertiary = Color.White,
    tertiaryContainer = PxLightSurface2,
    onTertiaryContainer = PxLightText,
    background = PxLightBg,
    onBackground = PxLightText,
    surface = PxLightBg,
    onSurface = PxLightText,
    surfaceVariant = PxLightSurface,
    onSurfaceVariant = PxLightTextMuted,
    surfaceTint = PxLightBg,
    inverseSurface = PxDarkSurface,
    inverseOnSurface = PxDarkText,
    error = PxLightDanger,
    onError = Color.White,
    // Cross-palette reuse: PxDarkDanger is the dark THEME's danger tone but
    // its value is a light pink (#F2B8B5) — on it, #4A0F0C text is ~9:1.
    errorContainer = PxDarkDanger,
    onErrorContainer = PxDangerFillShadow,
    outline = PxLightGray,
    outlineVariant = PxLightSurface2,
    scrim = Color.Black,
    surfaceContainerLowest = PxLightBg,
    surfaceContainerLow = PxLightBg2,
    surfaceContainer = PxLightBg2,
    surfaceContainerHigh = PxLightBg2,
    surfaceContainerHighest = PxLightSurface,
)

@Composable
fun MobaideTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    val pixelColors = if (darkTheme) DarkPixelColors else LightPixelColors
    val colorScheme = if (darkTheme) DarkColorScheme else LightColorScheme

    CompositionLocalProvider(LocalPixelColors provides pixelColors) {
        MaterialTheme(
            colorScheme = colorScheme,
            shapes = PixelShapes,
            typography = Typography,
            content = content,
        )
    }
}
