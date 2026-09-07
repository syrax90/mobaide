package com.mobaide.app.ui.theme

import androidx.compose.material3.Typography
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import com.mobaide.app.R
import java.util.Locale

// Departure Mono v1.500 (OFL — see android/app/DepartureMono-OFL.txt), the
// same face and version the web client ships. It has a single 400 weight:
// never pair ChromeFontFamily with FontWeight.Bold (Android would fake-bold
// and break the pixel grid). Bold names use FontFamily.Default at W600.
val ChromeFontFamily = FontFamily(Font(R.font.departure_mono_regular, FontWeight.Normal))

val TerminalFontFamily = ChromeFontFamily

/** The web system's uppercase "chrome" treatment; Compose has no textAllCaps. */
fun String.chromeCaps(): String = uppercase(Locale.ROOT)

// The 11px-grid chrome caption style: nav, section labels, chips, badges,
// status pills (11sp, 0.08em tracking, uppercased at the call site).
private val ChromeCaption = TextStyle(
    fontFamily = ChromeFontFamily,
    fontWeight = FontWeight.Normal,
    fontSize = 11.sp,
    lineHeight = 16.sp,
    letterSpacing = 0.08.em,
)

val Typography = Typography(
    displayLarge = TextStyle(
        fontFamily = ChromeFontFamily,
        fontWeight = FontWeight.Normal,
        fontSize = 44.sp,
        lineHeight = 52.sp,
        letterSpacing = 0.sp,
    ),
    displayMedium = TextStyle(
        fontFamily = ChromeFontFamily,
        fontWeight = FontWeight.Normal,
        fontSize = 33.sp,
        lineHeight = 44.sp,
        letterSpacing = 0.sp,
    ),
    displaySmall = TextStyle(
        fontFamily = ChromeFontFamily,
        fontWeight = FontWeight.Normal,
        fontSize = 33.sp,
        lineHeight = 44.sp,
        letterSpacing = 0.sp,
    ),
    headlineLarge = TextStyle(
        fontFamily = ChromeFontFamily,
        fontWeight = FontWeight.Normal,
        fontSize = 33.sp,
        lineHeight = 40.sp,
        letterSpacing = 0.sp,
    ),
    headlineMedium = TextStyle(
        fontFamily = ChromeFontFamily,
        fontWeight = FontWeight.Normal,
        fontSize = 28.sp,
        lineHeight = 36.sp,
        letterSpacing = 0.sp,
    ),
    headlineSmall = TextStyle(
        fontFamily = ChromeFontFamily,
        fontWeight = FontWeight.Normal,
        fontSize = 24.sp,
        lineHeight = 32.sp,
        letterSpacing = 0.sp,
    ),
    titleLarge = TextStyle(
        fontFamily = ChromeFontFamily,
        fontWeight = FontWeight.Normal,
        fontSize = 22.sp,
        lineHeight = 28.sp,
        letterSpacing = 0.sp,
    ),
    titleMedium = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.SemiBold,
        fontSize = 16.sp,
        lineHeight = 24.sp,
        letterSpacing = 0.15.sp,
    ),
    titleSmall = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.SemiBold,
        fontSize = 14.sp,
        lineHeight = 20.sp,
        letterSpacing = 0.1.sp,
    ),
    bodyLarge = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Normal,
        fontSize = 16.sp,
        lineHeight = 24.sp,
        letterSpacing = 0.5.sp,
    ),
    bodyMedium = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Normal,
        fontSize = 14.sp,
        lineHeight = 20.sp,
        letterSpacing = 0.25.sp,
    ),
    bodySmall = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Normal,
        fontSize = 12.sp,
        lineHeight = 16.sp,
        letterSpacing = 0.4.sp,
    ),
    labelLarge = ChromeCaption,
    labelMedium = ChromeCaption,
    labelSmall = ChromeCaption,
)
