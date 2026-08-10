package com.mobaide.app.ui.agents

/**
 * Sub-state for the reversed pairing direction: this device mints a short code
 * and the operator retypes it on the machine being paired.
 *
 * The QR direction needs a camera pointed at the agent's terminal. This one
 * needs nothing to travel *off* the agent host, which is the only thing that
 * works across an SSH jump host, a VM console, or any console whose clipboard
 * does not reach the phone.
 *
 * The flow ends at [Confirming]. Once the confirm lands, the shared tail —
 * waiting for the agent's relay tunnel to actually connect — is handed to
 * [AgentListViewModel.startWaitingForOnlineWatcher], which drives
 * [QrPairingState.WaitingForOnline]; the two directions differ only in how
 * they reach that point, so duplicating the watcher would be duplicating the
 * one piece of this flow with real failure modes.
 */
sealed interface DeviceCodePairingState {
    data object Idle : DeviceCodePairingState

    data object Minting : DeviceCodePairingState

    /**
     * A code is live and no machine has entered it yet.
     *
     * [command] is the whole line to run, which is what the operator copies;
     * [code] is displayed separately for the case where retyping eight
     * characters beats copying a command between devices.
     */
    data class WaitingForHost(
        val requestId: String,
        val code: String,
        val command: String,
    ) : DeviceCodePairingState

    /**
     * A machine entered the code and is waiting to be let in.
     *
     * [hostname] and [os] were reported by that machine over an endpoint that
     * authenticates nobody, so they are recognisable, not trustworthy. What
     * actually keeps a stranger's machine out of this account is the
     * confirmation the agent prints on its own terminal, naming this account
     * before it writes any credentials.
     *
     * [blockedReason] is non-null when the confirm is already destined to fail
     * — the agent id that machine reported is unknown to the relay, or belongs
     * to someone else — so the UI can say so instead of spending the code on a
     * tap that cannot succeed.
     *
     * [repairAgentName] is set only when approving would *move* an agent this
     * account already owns onto the redeeming host, rotating its credentials
     * and knocking the real machine offline. "Is this your machine?" is the
     * wrong question in that case.
     */
    data class Confirming(
        val requestId: String,
        val hostname: String,
        val os: String?,
        val repairAgentName: String?,
        val blockedReason: DeviceCodeBlockedReason?,
    ) : DeviceCodePairingState

    data class Claiming(val requestId: String) : DeviceCodePairingState

    data class Error(val message: String) : DeviceCodePairingState
}

/** The relay's pre-computed verdict on a confirm that would be refused. */
enum class DeviceCodeBlockedReason {
    UNKNOWN_AGENT,
    OWNED_BY_OTHER,
    ;

    /**
     * The canonical wording for this refusal, shared with the post-confirm
     * error path so one condition never ships two sentences.
     */
    val message: String
        get() = when (this) {
            UNKNOWN_AGENT ->
                "That machine still has credentials for an agent this relay no longer " +
                    "has. On the agent host run 'mobaide-agent re-pair --fresh', then " +
                    "enter a new code."
            OWNED_BY_OTHER ->
                "That machine is already paired to a different account. Its current " +
                    "owner has to remove it before it can be paired here."
        }

    companion object {
        /**
         * Unknown wire values map to `null` rather than throwing: a newer relay
         * may add a reason this build cannot render, and refusing to show the
         * confirm screen at all would be a worse failure than losing the
         * warning and letting the server reject the confirm.
         */
        fun fromWire(value: String?): DeviceCodeBlockedReason? = when (value) {
            "unknown_agent" -> UNKNOWN_AGENT
            "owned_by_other" -> OWNED_BY_OTHER
            else -> null
        }
    }
}
