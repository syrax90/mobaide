package com.claudemobile.app.data.network.dto

import kotlinx.serialization.Serializable

/** Request body for `PATCH /api/projects/order`. `names` must be an exact
 *  permutation of existing project names — the server returns 400 otherwise. */
@Serializable
data class ProjectOrderRequest(val names: List<String>)
