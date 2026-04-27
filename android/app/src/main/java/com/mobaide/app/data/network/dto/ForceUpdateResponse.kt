package com.mobaide.app.data.network.dto

import kotlinx.serialization.Serializable

/**
 * Response from ``POST /api/agent/update/force_now`` — the verbatim
 * outcome string from ``StagedBinaryUpdater.consider_manifest``:
 * ``"staged"``, ``"deferred"``, ``"no_change"``, or
 * ``"signature_failed"``.
 */
@Serializable
data class ForceUpdateResponse(val outcome: String)
