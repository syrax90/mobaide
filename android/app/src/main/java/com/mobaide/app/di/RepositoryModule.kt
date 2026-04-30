package com.mobaide.app.di

import android.content.Context
import com.mobaide.app.data.auth.SessionManager
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.auth.UserIdentityStore
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.events.UserEventBus
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.network.AutoScanWsClient
import com.mobaide.app.data.network.CentralApi
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.WebSocketManager
import com.mobaide.app.data.auth.ConnectionStore
import com.mobaide.app.data.repository.AgentRepository
import com.mobaide.app.data.repository.AgentUpdateRepository
import com.mobaide.app.data.repository.AgentVersionMonitor
import com.mobaide.app.data.repository.AuthRepository
import com.mobaide.app.data.repository.AutoScanRepository
import com.mobaide.app.data.repository.GitRepository
import com.mobaide.app.data.repository.ProjectsRepository
import com.mobaide.app.data.repository.SessionRepository
import com.mobaide.app.data.repository.TemplatesRepository
import com.mobaide.app.data.repository.VersionGateRepository
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
        userIdentityStore: UserIdentityStore,
    ): WebSocketManager {
        return WebSocketManager(client, tokenStore, sessionManager, userIdentityStore)
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
    fun provideGitRepository(restApi: RestApi, eventBus: AgentEventBus): GitRepository {
        return GitRepository(restApi, eventBus)
    }

    @Provides
    @Singleton
    fun providePreferencesStore(@ApplicationContext context: Context): PreferencesStore {
        return PreferencesStore(context)
    }

    @Provides
    @Singleton
    fun provideAgentRepository(
        centralApi: CentralApi,
        userIdentityStore: UserIdentityStore,
    ): AgentRepository {
        return AgentRepository(centralApi, userIdentityStore)
    }

    @Provides
    @Singleton
    fun provideTemplatesRepository(
        centralApi: CentralApi,
        userIdentityStore: UserIdentityStore,
    ): TemplatesRepository {
        return TemplatesRepository(centralApi, userIdentityStore)
    }

    @Provides
    @Singleton
    fun provideUserEventBus(
        webSocketManager: WebSocketManager,
        sessionManager: SessionManager,
        userIdentityStore: UserIdentityStore,
        agentRepository: AgentRepository,
        templatesRepository: TemplatesRepository,
    ): UserEventBus {
        return UserEventBus(
            webSocketManager,
            sessionManager,
            userIdentityStore,
            agentRepository,
            templatesRepository,
        )
    }

    @Provides
    @Singleton
    fun provideVersionGateRepository(centralApi: CentralApi): VersionGateRepository {
        return VersionGateRepository(centralApi)
    }

    @Provides
    @Singleton
    fun provideAgentVersionMonitor(
        restApi: RestApi,
        tokenStore: TokenStore,
    ): AgentVersionMonitor {
        return AgentVersionMonitor(restApi, tokenStore)
    }

    @Provides
    @Singleton
    fun provideAgentUpdateRepository(restApi: RestApi): AgentUpdateRepository {
        return AgentUpdateRepository(restApi)
    }
}
