package com.mobaide.app.di

import android.content.Context
import com.mobaide.app.data.analytics.AgentPairedConversion
import com.mobaide.app.data.analytics.AnalyticsSink
import com.mobaide.app.data.analytics.FirebaseAnalyticsSink
import com.mobaide.app.data.auth.UserIdentityStore
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.network.CentralApi
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object AnalyticsModule {

    @Provides
    @Singleton
    fun provideAnalyticsSink(@ApplicationContext context: Context): AnalyticsSink {
        return FirebaseAnalyticsSink(context)
    }

    @Provides
    @Singleton
    fun provideAgentPairedConversion(
        sink: AnalyticsSink,
        centralApi: CentralApi,
        userIdentityStore: UserIdentityStore,
        preferencesStore: PreferencesStore,
    ): AgentPairedConversion {
        return AgentPairedConversion(sink, centralApi, userIdentityStore, preferencesStore)
    }
}
