package com.mobaide.app.ui.theme

import androidx.compose.ui.graphics.Color

// Terminal colors
val TerminalBackground = Color(0xFF1E1E1E)
val TerminalForeground = Color(0xFFD4D4D4)

val AnsiBlack = Color(0xFF000000)
val AnsiRed = Color(0xFFCD3131)
val AnsiGreen = Color(0xFF0DBC79)
val AnsiYellow = Color(0xFFE5E510)
val AnsiBlue = Color(0xFF2472C8)
val AnsiMagenta = Color(0xFFBC3FBC)
val AnsiCyan = Color(0xFF11A8CD)
val AnsiWhite = Color(0xFFE5E5E5)

val AnsiBrightBlack = Color(0xFF666666)
val AnsiBrightRed = Color(0xFFF14C4C)
val AnsiBrightGreen = Color(0xFF23D18B)
val AnsiBrightYellow = Color(0xFFF5F543)
val AnsiBrightBlue = Color(0xFF3B8EEA)
val AnsiBrightMagenta = Color(0xFFD670D6)
val AnsiBrightCyan = Color(0xFF29B8DB)
val AnsiBrightWhite = Color(0xFFFFFFFF)

// Standard ANSI color array (indexed 0-15)
val AnsiColors = arrayOf(
    AnsiBlack, AnsiRed, AnsiGreen, AnsiYellow,
    AnsiBlue, AnsiMagenta, AnsiCyan, AnsiWhite,
    AnsiBrightBlack, AnsiBrightRed, AnsiBrightGreen, AnsiBrightYellow,
    AnsiBrightBlue, AnsiBrightMagenta, AnsiBrightCyan, AnsiBrightWhite,
)

// "Modern pixel" design-system tokens, mirrored from web/src/styles/theme.css
// (dark values verbatim from central_server/static/site/site.css). Keep hex
// values in sync token-for-token with the web client.

// Theme-invariant. PxBrand and PxBrand2 are FILL AND BORDER colours only —
// PxBrand2 on PxDarkSurface is ~2.6:1 and fails WCAG AA as text; accent text
// is PxDarkAccent / PxLightAccent.
val PxBrand = Color(0xFF6008AC)
val PxBrand2 = Color(0xFF6650A4)
val PxBtnShadow = Color(0xFF2B0450)
val PxDangerFill = Color(0xFF8C1D18)
val PxDangerFillShadow = Color(0xFF4A0F0C)
val PxPlate = Color(0xFFF4F4F4)
val PxModalShadow = Color(0xFF000000)

val PxDarkBg = Color(0xFF121212)
val PxDarkBg2 = Color(0xFF171717)
val PxDarkSurface = Color(0xFF1E1E1E)
val PxDarkSurface2 = Color(0xFF232323)
val PxDarkAccent = Color(0xFFD0BCFF)
val PxDarkText = Color(0xFFE9E5EF)
val PxDarkTextMuted = Color(0xFFCCC2DC)
val PxDarkGray = Color(0xFF8B8B8B)
val PxDarkRule = Color(0xFF625B71)
val PxDarkDanger = Color(0xFFF2B8B5)
val PxDarkOk = Color(0xFF23D18B)
val PxDarkWarning = Color(0xFFE3B341)
val PxDarkScrim = Color(0xB8000000)

val PxLightBg = Color(0xFFF4F4F4)
val PxLightBg2 = Color(0xFFFDFCFF)
val PxLightSurface = Color(0xFFEFECF3)
val PxLightSurface2 = Color(0xFFDCD7E2)
val PxLightAccent = Color(0xFF6008AC)
val PxLightText = Color(0xFF1C1B1F)
val PxLightTextMuted = Color(0xFF49454F)
val PxLightGray = Color(0xFF666666)
val PxLightRule = Color(0xFFA099AB)
val PxLightDanger = Color(0xFF8C1D18)
val PxLightOk = Color(0xFF116A2E)
val PxLightWarning = Color(0xFF8A6100)
val PxLightScrim = Color(0x80000000)
