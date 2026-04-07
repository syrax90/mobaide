package com.claudemobile.app.di

import android.content.Context
import com.claudemobile.app.data.auth.TokenStore
import com.claudemobile.app.data.local.PreferencesStore
import com.claudemobile.app.data.network.RestApi
import com.claudemobile.app.data.network.WebSocketManager
import com.claudemobile.app.data.repository.AuthRepository
import com.claudemobile.app.data.repository.AutoScanRepository
import com.claudemobile.app.data.repository.ProjectsRepository
import com.claudemobile.app.data.repository.SessionRepository
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import okhttp3.OkHttpClient
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object RepositoryModule {

    @Provides
    @Singleton
    fun provideRestApi(client: OkHttpClient, tokenStore: TokenStore): RestApi {
        return RestApi(client, tokenStore)
    }

    @Provides
    @Singleton
    fun provideWebSocketManager(client: OkHttpClient, tokenStore: TokenStore): WebSocketManager {
        return WebSocketManager(client, tokenStore)
    }

    @Provides
    @Singleton
    fun provideAuthRepository(restApi: RestApi, tokenStore: TokenStore): AuthRepository {
        return AuthRepository(restApi, tokenStore)
    }

    @Provides
    @Singleton
    fun provideSessionRepository(
        restApi: RestApi,
        webSocketManager: WebSocketManager,
    ): SessionRepository {
        return SessionRepository(restApi, webSocketManager)
    }

    @Provides
    @Singleton
    fun provideAutoScanRepository(restApi: RestApi): AutoScanRepository {
        return AutoScanRepository(restApi)
    }

    @Provides
    @Singleton
    fun provideProjectsRepository(restApi: RestApi): ProjectsRepository {
        return ProjectsRepository(restApi)
    }

    @Provides
    @Singleton
    fun providePreferencesStore(@ApplicationContext context: Context): PreferencesStore {
        return PreferencesStore(context)
    }
}
