package com.claudemobile.app.di

import android.content.Context
import com.claudemobile.app.data.auth.TokenStore
import com.claudemobile.app.data.events.ServerEventBus
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
    fun provideServerEventBus(webSocketManager: WebSocketManager): ServerEventBus {
        return ServerEventBus(webSocketManager)
    }

    @Provides
    @Singleton
    fun provideSessionRepository(
        restApi: RestApi,
        webSocketManager: WebSocketManager,
        eventBus: ServerEventBus,
    ): SessionRepository {
        return SessionRepository(restApi, webSocketManager, eventBus)
    }

    @Provides
    @Singleton
    fun provideAutoScanRepository(restApi: RestApi, eventBus: ServerEventBus): AutoScanRepository {
        return AutoScanRepository(restApi, eventBus)
    }

    @Provides
    @Singleton
    fun provideProjectsRepository(restApi: RestApi, eventBus: ServerEventBus): ProjectsRepository {
        return ProjectsRepository(restApi, eventBus)
    }

    @Provides
    @Singleton
    fun providePreferencesStore(@ApplicationContext context: Context): PreferencesStore {
        return PreferencesStore(context)
    }
}
