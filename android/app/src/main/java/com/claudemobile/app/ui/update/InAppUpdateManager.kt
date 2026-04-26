package com.claudemobile.app.ui.update

import android.app.Activity
import android.content.Context
import com.google.android.play.core.appupdate.AppUpdateManager
import com.google.android.play.core.appupdate.AppUpdateManagerFactory
import com.google.android.play.core.appupdate.AppUpdateOptions
import com.google.android.play.core.install.model.AppUpdateType
import com.google.android.play.core.install.model.UpdateAvailability
import com.google.android.play.core.ktx.requestAppUpdateInfo
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Wraps the Play Core in-app update API. Two flows:
 *
 * * **Flexible** ([checkForUpdate]) — non-blocking download/install for normal
 *   releases. Triggered every [Activity.onResume].
 * * **Immediate** ([requestImmediateUpdate]) — used only when the
 *   server-driven version gate ([com.claudemobile.app.data.repository.VersionGateRepository])
 *   declares the current build incompatible AND the Play Store reports an
 *   update is available. Server gate stays authoritative when Play has nothing.
 */
@Singleton
class InAppUpdateManager @Inject constructor(
    @ApplicationContext private val context: Context,
) {
    private val manager: AppUpdateManager = AppUpdateManagerFactory.create(context)
    private val checkInFlight = java.util.concurrent.atomic.AtomicBoolean(false)

    suspend fun checkForUpdate(activity: Activity) {
        if (!checkInFlight.compareAndSet(false, true)) return
        try {
            val info = try {
                manager.requestAppUpdateInfo()
            } catch (_: Throwable) {
                return
            }
            if (info.updateAvailability() == UpdateAvailability.UPDATE_AVAILABLE
                && info.isUpdateTypeAllowed(AppUpdateType.FLEXIBLE)
            ) {
                try {
                    manager.startUpdateFlowForResult(
                        info,
                        activity,
                        AppUpdateOptions.newBuilder(AppUpdateType.FLEXIBLE).build(),
                        REQUEST_CODE_FLEXIBLE,
                    )
                } catch (_: Throwable) {
                }
            }
        } finally {
            checkInFlight.set(false)
        }
    }

    suspend fun requestImmediateUpdate(activity: Activity) {
        val info = try {
            manager.requestAppUpdateInfo()
        } catch (_: Throwable) {
            return
        }
        if (info.updateAvailability() == UpdateAvailability.UPDATE_AVAILABLE
            && info.isUpdateTypeAllowed(AppUpdateType.IMMEDIATE)
        ) {
            try {
                manager.startUpdateFlowForResult(
                    info,
                    activity,
                    AppUpdateOptions.newBuilder(AppUpdateType.IMMEDIATE).build(),
                    REQUEST_CODE_IMMEDIATE,
                )
            } catch (_: Throwable) {
            }
        }
    }

    companion object {
        private const val REQUEST_CODE_FLEXIBLE = 0xCA42
        private const val REQUEST_CODE_IMMEDIATE = 0xCA43
    }
}
