package com.claudemobile.app.ui.theme

import androidx.compose.ui.graphics.Color

// Terminal colors
val TerminalBackground = Color(0xFF1E1E1E)
val TerminalForeground = Color(0xFFD4D4D4)

// ANSI standard colors
val AnsiBlack = Color(0xFF000000)
val AnsiRed = Color(0xFFCD3131)
val AnsiGreen = Color(0xFF0DBC79)
val AnsiYellow = Color(0xFFE5E510)
val AnsiBlue = Color(0xFF2472C8)
val AnsiMagenta = Color(0xFFBC3FBC)
val AnsiCyan = Color(0xFF11A8CD)
val AnsiWhite = Color(0xFFE5E5E5)

// ANSI bright colors
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

// Material theme colors
val Purple80 = Color(0xFFD0BCFF)
val PurpleGrey80 = Color(0xFFCCC2DC)
val Pink80 = Color(0xFFEFB8C8)
val Purple40 = Color(0xFF6650a4)
val PurpleGrey40 = Color(0xFF625b71)
val Pink40 = Color(0xFF7D5260)

// Session state colors
val StateRunning = Color(0xFF4CAF50)
val StateStopped = Color(0xFF9E9E9E)
val StateCrashed = Color(0xFFF44336)
val StateStarting = Color(0xFFFFC107)
val StateStopping = Color(0xFFFF9800)
