package com.mobaide.app.ui.util

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import androidx.browser.customtabs.CustomTabsIntent
import androidx.core.net.toUri

fun openUrlInCustomTab(context: Context, url: String) {
    val uri = url.toUri()
    try {
        CustomTabsIntent.Builder().build().launchUrl(context, uri)
    } catch (_: ActivityNotFoundException) {
        // Devices with no Custom Tabs provider (e.g. minimal Android TV
        // images) fall back to a plain VIEW intent. The outer catch keeps
        // tapping a privacy link from crashing the app even there.
        val fallback = Intent(Intent.ACTION_VIEW, uri).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        try {
            context.startActivity(fallback)
        } catch (_: ActivityNotFoundException) {
        }
    }
}
