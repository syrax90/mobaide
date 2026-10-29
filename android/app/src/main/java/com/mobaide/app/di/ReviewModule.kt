package com.mobaide.app.di

import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.review.ReviewPromptCoordinator
import com.mobaide.app.notifications.ForegroundSessionTracker
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object ReviewModule {

    @Provides
    @Singleton
    fun provideReviewPromptCoordinator(
        preferencesStore: PreferencesStore,
        foregroundSessionTracker: ForegroundSessionTracker,
    ): ReviewPromptCoordinator {
        return ReviewPromptCoordinator(preferencesStore, foregroundSessionTracker)
    }
}
