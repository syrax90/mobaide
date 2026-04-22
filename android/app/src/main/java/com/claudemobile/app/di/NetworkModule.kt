package com.claudemobile.app.di

import com.claudemobile.app.BuildConfig
import com.claudemobile.app.data.auth.AuthInterceptor
import com.claudemobile.app.data.auth.CentralAuthInterceptor
import com.claudemobile.app.data.network.CentralApi
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import okhttp3.OkHttpClient
import java.util.concurrent.TimeUnit
import javax.inject.Named
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object NetworkModule {

    /**
     * Provide the bare OkHttpClient used by
     * [com.claudemobile.app.data.auth.SessionManager] to issue
     * `/api/auth/refresh` calls. Must NOT include [AuthInterceptor]:
     * routing refresh traffic back through the interceptor that depends
     * on SessionManager would create a Hilt cycle and a runtime
     * re-entrance loop.
     */
    @Provides
    @Singleton
    @Named("refresh")
    fun provideRefreshOkHttpClient(): OkHttpClient {
        return OkHttpClient.Builder()
            .connectTimeout(30, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)
            .build()
    }

    @Provides
    @Singleton
    fun provideOkHttpClient(authInterceptor: AuthInterceptor): OkHttpClient {
        return OkHttpClient.Builder()
            .addInterceptor(authInterceptor)
            .connectTimeout(30, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)
            .pingInterval(30, TimeUnit.SECONDS)
            .build()
    }

    /**
     * Provide the OkHttpClient for [CentralApi] user-scoped central-server
     * REST endpoints (user auth + agent CRUD + mobile-token issuance).
     * Carries [CentralAuthInterceptor] so that a 401 on any authenticated
     * endpoint atomically invalidates the local session — the dashboard
     * JWT has no refresh token, so a server rejection is terminal and the
     * user must re-login.
     */
    @Provides
    @Singleton
    @Named("central")
    fun provideCentralOkHttpClient(
        centralAuthInterceptor: CentralAuthInterceptor,
    ): OkHttpClient {
        return OkHttpClient.Builder()
            .addInterceptor(centralAuthInterceptor)
            .connectTimeout(30, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)
            .build()
    }

    @Provides
    @Singleton
    fun provideCentralApi(@Named("central") client: OkHttpClient): CentralApi {
        return CentralApi(client, BuildConfig.CENTRAL_SERVER_URL)
    }
}
