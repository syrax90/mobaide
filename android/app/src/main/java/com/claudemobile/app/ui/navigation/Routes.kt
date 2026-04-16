package com.claudemobile.app.ui.navigation

import android.net.Uri

object Routes {
    const val LOGIN = "login"
    const val REGISTER = "register"
    const val SESSION_LIST = "session_list"
    const val SESSION_DETAIL = "session_detail/{sessionId}"
    const val CREATE_SESSION = "create_session"
    const val SETTINGS = "settings"
    const val AUTO_SCAN_DETAIL = "auto_scan_detail/{projectName}"
    const val QR_SCANNER = "qr_scanner"
    const val MANUAL_PAIRING = "manual_pairing"
    const val CONNECTIONS = "connections"

    fun sessionDetail(sessionId: String) = "session_detail/$sessionId"
    fun autoScanDetail(projectName: String) =
        "auto_scan_detail/${Uri.encode(projectName)}"
}
