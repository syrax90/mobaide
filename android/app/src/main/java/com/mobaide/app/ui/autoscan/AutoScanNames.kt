package com.mobaide.app.ui.autoscan

import com.mobaide.app.data.network.dto.AUTO_SCAN_SOURCE_GITHUB
import com.mobaide.app.data.network.dto.AUTO_SCAN_SOURCE_LINEAR
import com.mobaide.app.data.network.dto.AUTO_SCAN_UI_CHAT
import com.mobaide.app.data.network.dto.AutoScanInstanceDto

/** The display name of an Auto Scan agent kind; an unknown kind shows as sent. */
fun autoScanAgentName(cli: String): String = when (cli) {
    "claude" -> "Claude Code"
    "codex" -> "Codex"
    "antigravity" -> "Antigravity"
    "opencode" -> "OpenCode"
    "copilot" -> "GitHub Copilot"
    "cursor" -> "Cursor"
    "junie" -> "Junie"
    "pi" -> "Pi"
    else -> cli
}

/** The display name of an Auto Scan issue source. */
fun autoScanSourceName(source: String): String = when (source) {
    AUTO_SCAN_SOURCE_GITHUB -> "GitHub"
    AUTO_SCAN_SOURCE_LINEAR -> "Linear"
    else -> source
}

/**
 * "GitHub · Codex": where a running scan finds issues and which agent runs
 * them; "GitHub · Codex · Chat" when its sessions open in the chat view.
 */
val AutoScanInstanceDto.sourceAndAgent: String
    get() = listOfNotNull(
        autoScanSourceName(source),
        autoScanAgentName(cli),
        "Chat".takeIf { ui == AUTO_SCAN_UI_CHAT },
    ).joinToString(" · ")
