package com.mobaide.app.data.network

import java.util.UUID

/**
 * The name of this app process as the controller of a Herdr pane. Control of
 * a pane belongs to one client: the agent puts the controller's id in the
 * pane's state frame, and every other client keeps the dictated grid and
 * sends no size.
 */
object PaneClient {
    val id: String = UUID.randomUUID().toString()
}
