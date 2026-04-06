package com.claudemobile.app.data.network.dto

import kotlinx.serialization.Serializable

@Serializable
data class WsInputMessage(
    val type: String = "input",
    val data: String,
)

@Serializable
data class WsResizeMessage(
    val type: String = "resize",
    val rows: Int,
    val cols: Int,
)

@Serializable
data class WsOutputMessage(
    val type: String,
    val data: String? = null,
    val status: String? = null,
)
