package com.mobaide.app.data.repository

import com.mobaide.app.data.network.ApiException
import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive
import java.io.IOException

/**
 * Failures of the session-scoped git Changes routes, typed so the screens can
 * tell "update your agent" from "session ended" from "git itself refused".
 *
 * A coded `detail` object is a git answer whatever the status — the job routes'
 * `404 job_not_found` included, which must not read as an old agent. The two
 * string-detail 404s differ only in FastAPI's text: the agent answers a session
 * lookup miss with `Session '<id>' not found`, whereas an agent that predates
 * the routes answers a relay `Unknown proxy route` or FastAPI's plain
 * `Not Found` — so the string is the discriminator.
 */
sealed class SessionGitException(message: String, cause: Throwable? = null) :
    Exception(message, cause) {

    class AgentOutdated(cause: ApiException) :
        SessionGitException("The agent does not serve the git Changes API", cause)

    class SessionGone(cause: ApiException) :
        SessionGitException("Session not found", cause)

    /** `{"detail": {"code": …, "message"?: …}}` from 400 / 409 / 500 / 503 / 504. */
    class GitError(val code: String, val detailMessage: String?, cause: ApiException) :
        SessionGitException("git: $code", cause)

    class Network(cause: Throwable) :
        SessionGitException(cause.message ?: "Network error", cause)

    companion object {
        const val CODE_GIT_UNAVAILABLE = "git_unavailable"
        const val CODE_GIT_TIMEOUT = "git_timeout"
        const val CODE_GIT_ERROR = "git_error"
        const val CODE_BAD_PATH = "bad_path"
        const val CODE_BAD_SCOPE = "bad_scope"
        const val CODE_EMPTY_MESSAGE = "empty_message"
        const val CODE_IDENTITY_REQUIRED = "identity_required"
        const val CODE_UNMERGED = "unmerged"
        const val CODE_NOTHING_TO_COMMIT = "nothing_to_commit"
        const val CODE_NO_HEAD = "no_head"
        const val CODE_HOOK_FAILED = "hook_failed"
        const val CODE_INDEX_LOCKED = "index_locked"
        const val CODE_PATHSPEC_MISMATCH = "pathspec_mismatch"
        const val CODE_GIT_TOO_OLD = "git_too_old"
        /** Another fetch / pull / push owns the repository; [GitError.detailMessage] is its job id. */
        const val CODE_JOB_RUNNING = "job_running"
        const val CODE_JOB_NOT_FOUND = "job_not_found"
        /** The rebase refusals; [GitError.detailMessage] is the ref, the state or the path. */
        const val CODE_UNKNOWN_REF = "unknown_ref"
        const val CODE_NO_REBASE = "no_rebase"
        const val CODE_CONFLICT_MARKERS = "conflict_markers"
        /** The conflict routes' refusals; [GitError.detailMessage] is the path. */
        const val CODE_NOT_CONFLICTED = "not_conflicted"
        const val CODE_FILE_CHANGED = "file_changed"
        const val CODE_FILE_BUSY = "file_busy"
        const val CODE_NOT_RESTORABLE = "not_restorable"
        const val CODE_UNSUPPORTED = "unsupported"
        const val CODE_TOO_LARGE = "too_large"
        const val CODE_BAD_CONTENT = "bad_content"
    }
}

internal val lenientDetailJson = Json { ignoreUnknownKeys = true; isLenient = true }

private val sessionNotFound = Regex("^Session '.*' not found$")

/** FastAPI's `detail` is a string for 404s and an object for the git errors. */
internal fun extractDetail(body: String): Any? {
    if (body.isBlank()) return null
    val root = runCatching { lenientDetailJson.parseToJsonElement(body) }.getOrNull() as? JsonObject
        ?: return null
    return when (val detail = root["detail"]) {
        is JsonPrimitive -> detail.contentOrNull
        is JsonObject -> detail
        else -> null
    }
}

internal fun mapSessionGitError(t: Throwable): Throwable = when (t) {
    is SessionGitException -> t
    is ApiException -> {
        val detail = extractDetail(t.errorBody)
        val code = (detail as? JsonObject)?.get("code")?.jsonPrimitive?.contentOrNull
        when {
            code != null -> SessionGitException.GitError(
                code = code,
                detailMessage = (detail as JsonObject)["message"]?.jsonPrimitive?.contentOrNull,
                cause = t,
            )
            t.code == 404 && detail is String && sessionNotFound.matches(detail) ->
                SessionGitException.SessionGone(t)
            t.code == 404 -> SessionGitException.AgentOutdated(t)
            else -> t
        }
    }
    is IOException, is SerializationException -> SessionGitException.Network(t)
    else -> t
}
