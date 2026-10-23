package com.mobaide.app.data.repository

import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.fs.DirListingCache
import com.mobaide.app.data.fs.ListingHit
import com.mobaide.app.data.fs.ListingRequest
import com.mobaide.app.data.fs.PathCompletion
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.dto.FsDirsResponse
import com.mobaide.app.data.network.dto.fsCapability
import kotlinx.coroutines.CancellationException

/** The agent's `capabilities.fs`: [dirs] gates suggestions, [windowsPaths] words the hints. */
data class FsSupport(val dirs: Boolean, val windowsPaths: Boolean) {
    companion object {
        val None = FsSupport(dirs = false, windowsPaths = false)
    }
}

/**
 * Folder listings behind the host-path fields, cached per agent so the project
 * form and the session wizard share what either has already fetched.
 */
class PathSuggestionRepository(
    private val restApi: RestApi,
    private val tokenStore: TokenStore,
    private val now: () -> Long = System::currentTimeMillis,
) {
    private data class Support(val support: FsSupport, val probedAt: Long)

    private val lock = Any()
    private val caches = HashMap<String, DirListingCache>()
    private val support = HashMap<String, Support>()

    private fun agentKey(): String = tokenStore.getAgentId() ?: tokenStore.getAgentUrl() ?: ""

    private fun cache(): DirListingCache = synchronized(lock) {
        caches.getOrPut(agentKey()) { DirListingCache(now) }
    }

    /** `capabilities.fs`, probed at most every five minutes per agent; [FsSupport.None] on failure. */
    suspend fun support(): FsSupport {
        val key = agentKey()
        synchronized(lock) {
            support[key]?.takeIf { now() - it.probedAt < SUPPORT_TTL_MS }?.let { return it.support }
        }
        val probed = try {
            val info = restApi.getAgentVersion()
            FsSupport(dirs = info.fsCapability("dirs"), windowsPaths = info.fsCapability("windows_paths"))
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            // Suggestions are a convenience: any failed probe just shows none.
            return FsSupport.None
        }
        synchronized(lock) { support[key] = Support(probed, now()) }
        return probed
    }

    /** `capabilities.fs.dirs`, with [support]'s caching. */
    suspend fun isSupported(): Boolean = support().dirs

    fun lookup(query: PathCompletion.Query): ListingHit? = synchronized(lock) {
        cache().lookup(query.dir, query.fragment)
    }

    fun nextRequest(query: PathCompletion.Query): ListingRequest? = synchronized(lock) {
        cache().nextRequest(query)
    }

    /**
     * Fetch and cache one listing; false when nothing new was cached. A
     * relative, missing or unreadable directory is remembered as empty so
     * further keystrokes under it stay local; a timed-out scan (503) or a network failure
     * is not, and is retried on the next directory change.
     */
    suspend fun fetch(request: ListingRequest): Boolean {
        val cache = cache()
        val listing = try {
            restApi.listDirs(request.dir, request.q)
        } catch (e: ApiException) {
            if (e.code !in DEFINITIVE_FAILURES) return false
            FsDirsResponse(path = request.dir, dirs = emptyList(), truncated = false)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            return false
        }
        synchronized(lock) { cache.store(request, listing) }
        return true
    }

    private companion object {
        const val SUPPORT_TTL_MS = 5 * 60_000L
        val DEFINITIVE_FAILURES = setOf(400, 403, 404)
    }
}
