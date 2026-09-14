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
    const val SESSION_GIT_CHANGES = "session_git_changes/{sessionId}?commit={commit}"
    const val SESSION_GIT_DIFF =
        "session_git_diff/{sessionId}?path={path}&scope={scope}&origPath={origPath}"
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
    /** [commit] presents the commit sheet over the list on arrival (the terminal menu's Commit…). */
    fun sessionGitChanges(sessionId: String, commit: Boolean = false) =
        "session_git_changes/$sessionId?commit=$commit"

    // Query args, not path segments: a repo-relative path carries `/`, spaces
    // and `#`. Uri.encode leaves no raw `+`, so the navigation library's single
    // decode returns the path byte-for-byte.
    fun sessionGitDiff(sessionId: String, path: String, scope: String, origPath: String?): String {
        val base = "session_git_diff/$sessionId?path=${Uri.encode(path)}&scope=${Uri.encode(scope)}"
        return if (origPath == null) base else "$base&origPath=${Uri.encode(origPath)}"
    }
}
