package com.mobaide.app.ui.navigation

import android.net.Uri

object Routes {
    const val LOGIN = "login"
    const val REGISTER = "register"
    const val VERIFY_EMAIL = "verify_email/{email}"
    const val AGENT_LIST = "agent_list"
    const val SESSION_LIST = "session_list"
    const val SESSION_DETAIL = "session_detail/{sessionId}"
    const val CREATE_SESSION = "create_session"
    const val SETTINGS = "settings"
    const val SUPPORT = "support"
    const val AUTO_SCAN_DETAIL = "auto_scan_detail/{projectName}"
    const val CONNECTIONS = "connections"
    const val PROJECTS = "projects"
    const val GIT_MANAGEMENT = "git/{projectName}"
    const val INCOMPATIBLE_SERVER = "incompatible_server"
    const val TEMPLATES = "templates"
    const val DEVICES = "devices"

    const val FORGOT_PASSWORD = "forgot_password"
    const val RESET_PASSWORD = "reset_password/{email}"

    fun sessionDetail(sessionId: String) = "session_detail/$sessionId"
    fun autoScanDetail(projectName: String) =
        "auto_scan_detail/${Uri.encode(projectName)}"
    fun verifyEmail(email: String) = "verify_email/${Uri.encode(email)}"
    fun resetPassword(email: String) = "reset_password/${Uri.encode(email)}"
    fun gitManagement(projectName: String) = "git/${Uri.encode(projectName)}"
}
