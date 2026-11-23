package com.mobaide.app.data.network

import kotlinx.coroutines.suspendCancellableCoroutine
import okhttp3.Call
import okhttp3.Callback
import okhttp3.Response
import java.io.IOException
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/**
 * Run the call so that cancelling the coroutine cancels it, an in-progress
 * body read included: [transform] consumes the response on OkHttp's thread
 * inside the callback, rather than handing it across a dispatcher hop where a
 * cancellation could strand it unclosed.
 */
internal suspend fun <T> Call.await(transform: (Response) -> T): T =
    suspendCancellableCoroutine { cont ->
        cont.invokeOnCancellation { cancel() }
        enqueue(object : Callback {
            override fun onResponse(call: Call, response: Response) {
                runCatching { response.use(transform) }.fold(
                    onSuccess = { cont.resume(it) },
                    onFailure = { if (!cont.isCancelled) cont.resumeWithException(it) },
                )
            }

            override fun onFailure(call: Call, e: IOException) {
                if (!cont.isCancelled) cont.resumeWithException(e)
            }
        })
    }
