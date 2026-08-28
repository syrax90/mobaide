package com.mobaide.app.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color

/**
 * Extended "modern pixel" roles that have no Material 3 slot. Ring grammar
 * (mirrors the web system): [surface2] frames plates, [gray] bounds
 * interactive controls, [rule] is decorative only, [brand2] marks
 * selected/active, status tones ring status chips. [brand]/[brand2] are fill
 * and border colours only — never text on dark.
 */
@Immutable
data class PixelColors(
    val brand: Color,
    val brand2: Color,
    val btnShadow: Color,
    val dangerFill: Color,
    val dangerFillShadow: Color,
    val plate: Color,
    val modalShadow: Color,
    val accent: Color,
    val bg2: Color,
    val surface: Color,
    val surface2: Color,
    val rule: Color,
    val gray: Color,
    val ok: Color,
    val warning: Color,
    val danger: Color,
    val textMuted: Color,
    val scrim: Color,
    val statusRunning: Color,
    val statusStarting: Color,
    val statusStopping: Color,
    val statusStopped: Color,
    val statusCrashed: Color,
)

val DarkPixelColors = PixelColors(
    brand = PxBrand,
    brand2 = PxBrand2,
    btnShadow = PxBtnShadow,
    dangerFill = PxDangerFill,
    dangerFillShadow = PxDangerFillShadow,
    plate = PxPlate,
    modalShadow = PxModalShadow,
    accent = PxDarkAccent,
    bg2 = PxDarkBg2,
    surface = PxDarkSurface,
    surface2 = PxDarkSurface2,
    rule = PxDarkRule,
    gray = PxDarkGray,
    ok = PxDarkOk,
    warning = PxDarkWarning,
    danger = PxDarkDanger,
    textMuted = PxDarkTextMuted,
    scrim = PxDarkScrim,
    statusRunning = PxDarkOk,
    statusStarting = PxDarkAccent,
    statusStopping = PxDarkWarning,
    statusStopped = PxDarkTextMuted,
    statusCrashed = PxDarkDanger,
)

val LightPixelColors = PixelColors(
    brand = PxBrand,
    brand2 = PxBrand2,
    btnShadow = PxBtnShadow,
    dangerFill = PxDangerFill,
    dangerFillShadow = PxDangerFillShadow,
    plate = PxPlate,
    modalShadow = PxModalShadow,
    accent = PxLightAccent,
    bg2 = PxLightBg2,
    surface = PxLightSurface,
    surface2 = PxLightSurface2,
    rule = PxLightRule,
    gray = PxLightGray,
    ok = PxLightOk,
    warning = PxLightWarning,
    danger = PxLightDanger,
    textMuted = PxLightTextMuted,
    scrim = PxLightScrim,
    statusRunning = PxLightOk,
    statusStarting = PxLightAccent,
    statusStopping = PxLightWarning,
    statusStopped = PxLightTextMuted,
    statusCrashed = PxLightDanger,
)

val LocalPixelColors = staticCompositionLocalOf { DarkPixelColors }

val MaterialTheme.pixel: PixelColors
    @Composable
    @ReadOnlyComposable
    get() = LocalPixelColors.current
