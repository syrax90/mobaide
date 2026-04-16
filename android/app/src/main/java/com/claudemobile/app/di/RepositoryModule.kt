package com.claudemobile.app.di

import android.content.Context
import com.claudemobile.app.data.auth.SessionManager
import com.claudemobile.app.data.auth.TokenStore
import com.claudemobile.app.data.auth.UserIdentityStore
import com.claudemobile.app.data.events.AgentEventBus
import com.claudemobile.app.data.local.PreferencesStore
import com.claudemobile.app.data.network.AutoScanWsClient
import com.claudemobile.app.data.network.CentralApi
import com.claudemobile.app.data.network.RestApi
import com.claudemobile.app.data.network.WebSocketManager
import com.claudemobile.app.data.auth.ConnectionStore
import com.claudemobile.app.data.repository.AuthRepository
import com.claudemobile.app.data.repository.AutoScanRepository
import com.claudemobile.app.data.repository.PairingRepository
import com.claudemobile.app.data.repository.ProjectsRepository
import com.claudemobile.app.data.repository.SessionRepository
import javax.inject.Named
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
    fun provideWebSocketManager(
        client: OkHttpClient,
        tokenStore: TokenStore,
        sessionManager: SessionManager,
    ): WebSocketManager {
        return WebSocketManager(client, tokenStore, sessionManager)
    }

    @Provides
    @Singleton
    fun provideAuthRepository(
        restApi: RestApi,
        centralApi: CentralApi,
        tokenStore: TokenStore,
        userIdentityStore: UserIdentityStore,
        sessionManager: SessionManager,
        connectionStore: ConnectionStore,
    ): AuthRepository {
        return AuthRepository(restApi, centralApi, tokenStore, userIdentityStore, sessionManager, connectionStore)
    }

    @Provides
    @Singleton
    fun provideAgentEventBus(
        webSocketManager: WebSocketManager,
        sessionManager: SessionManager,
    ): AgentEventBus {
        return AgentEventBus(webSocketManager, sessionManager)
    }

    @Provides
    @Singleton
    fun provideSessionRepository(
        restApi: RestApi,
        webSocketManager: WebSocketManager,
        eventBus: AgentEventBus,
    ): SessionRepository {
        return SessionRepository(restApi, webSocketManager, eventBus)
    }

    @Provides
    @Singleton
    fun provideAutoScanWsClient(webSocketManager: WebSocketManager): AutoScanWsClient {
        return AutoScanWsClient(webSocketManager)
    }

    @Provides
    @Singleton
    fun provideAutoScanRepository(
        restApi: RestApi,
        eventBus: AgentEventBus,
        autoScanWsClient: AutoScanWsClient,
    ): AutoScanRepository {
        return AutoScanRepository(restApi, eventBus, autoScanWsClient)
    }

    @Provides
    @Singleton
    fun provideProjectsRepository(restApi: RestApi, eventBus: AgentEventBus): ProjectsRepository {
        return ProjectsRepository(restApi, eventBus)
    }

    @Provides
    @Singleton
    fun providePreferencesStore(@ApplicationContext context: Context): PreferencesStore {
        return PreferencesStore(context)
    }

    @Provides
    @Singleton
    fun providePairingRepository(
        @Named("refresh") refreshClient: OkHttpClient,
        tokenStore: TokenStore,
        sessionManager: SessionManager,
        connectionStore: ConnectionStore,
    ): PairingRepository {
        return PairingRepository(refreshClient, tokenStore, sessionManager, connectionStore)
    }
}
