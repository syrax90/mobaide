package com.mobaide.app

import android.app.Application
import com.mobaide.app.notifications.AppForegroundTracker
import com.mobaide.app.notifications.NotificationChannels
import dagger.hilt.android.HiltAndroidApp
import javax.inject.Inject

@HiltAndroidApp
class MobaideApp : Application() {
    /**
     * Observed from here rather than from a screen: the streams it gates are
     * app-scoped, and the observer has to be attached before any of them opens.
     */
    @Inject lateinit var appForegroundTracker: AppForegroundTracker

    override fun onCreate() {
        super.onCreate()
        NotificationChannels.ensureCreated(this)
        appForegroundTracker.start()
    }
}
