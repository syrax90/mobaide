package com.mobaide.app.data.network.dto

import kotlinx.serialization.Serializable

/** Request body for `PATCH /api/projects/order`. `names` must be an exact
 *  permutation of existing workspace names — the server returns 400 otherwise. */
@Serializable
data class WorkspaceOrderRequest(val names: List<String>)
