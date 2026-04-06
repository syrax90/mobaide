package com.claudemobile.app.ui.navigation

object Routes {
    const val LOGIN = "login"
    const val SESSION_LIST = "session_list"
    const val SESSION_DETAIL = "session_detail/{sessionId}"
    const val SETTINGS = "settings"

    fun sessionDetail(sessionId: String) = "session_detail/$sessionId"
}
