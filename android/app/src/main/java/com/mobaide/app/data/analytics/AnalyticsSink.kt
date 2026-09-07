package com.mobaide.app.data.analytics

import android.content.Context
import android.os.Bundle
import com.google.firebase.FirebaseApp
import com.google.firebase.analytics.FirebaseAnalytics

interface AnalyticsSink {
    val isEnabled: Boolean
    fun logEvent(name: String, params: Map<String, String> = emptyMap())
}

class FirebaseAnalyticsSink(private val context: Context) : AnalyticsSink {

    // Computed per call, never cached: a build without google-services.json
    // has no default FirebaseApp, and every caller short-circuits on this
    // before doing any I/O or persisting anything.
    override val isEnabled: Boolean
        get() = FirebaseApp.getApps(context).isNotEmpty()

    private val analytics: FirebaseAnalytics by lazy { FirebaseAnalytics.getInstance(context) }

    override fun logEvent(name: String, params: Map<String, String>) {
        if (!isEnabled) return
        val bundle = Bundle().apply { params.forEach { (key, value) -> putString(key, value) } }
        analytics.logEvent(name, bundle)
    }
}

object NoOpAnalyticsSink : AnalyticsSink {
    override val isEnabled: Boolean = false
    override fun logEvent(name: String, params: Map<String, String>) = Unit
}
