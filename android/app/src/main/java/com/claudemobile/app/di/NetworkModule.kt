package com.claudemobile.app.di

import com.claudemobile.app.BuildConfig
import com.claudemobile.app.data.auth.AuthInterceptor
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
     * Bare OkHttpClient used exclusively by
     * [com.claudemobile.app.data.auth.SessionManager] to issue
     * `/api/auth/refresh` calls AND by [CentralApi] for user-auth
     * endpoints. Must NOT include [AuthInterceptor]: routing refresh
     * traffic back through the interceptor that depends on SessionManager
     * would create a Hilt cycle and a runtime re-entrance loop.
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

    @Provides
    @Singleton
    fun provideCentralApi(@Named("refresh") client: OkHttpClient): CentralApi {
        return CentralApi(client, BuildConfig.CENTRAL_SERVER_URL)
    }
}
