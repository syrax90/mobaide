package com.mobaide.app.ui.review

import android.app.Activity
import android.content.Context
import com.google.android.play.core.ktx.launchReview
import com.google.android.play.core.ktx.requestReview
import com.google.android.play.core.review.ReviewManager
import com.google.android.play.core.review.ReviewManagerFactory
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.CancellationException
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Wraps the Play In-App Review API. Play alone decides whether the card shows
 * (quota, an existing review, an install not from Play) and never says, so
 * every outcome, a failure included, just returns: Play's guidance is to
 * carry on without telling the user.
 */
@Singleton
class InAppReviewManager @Inject constructor(
    @ApplicationContext context: Context,
) {
    private val manager: ReviewManager = ReviewManagerFactory.create(context)

    /**
     * [stillWanted] is read again after the Play round trip; [onLaunch] runs
     * only when the card is really handed to Play.
     */
    suspend fun launchReview(
        activity: Activity,
        stillWanted: () -> Boolean,
        onLaunch: suspend () -> Unit,
    ) {
        try {
            val info = manager.requestReview()
            if (!stillWanted()) return
            onLaunch()
            manager.launchReview(activity, info)
        } catch (e: CancellationException) {
            throw e
        } catch (_: Throwable) {
        }
    }
}
