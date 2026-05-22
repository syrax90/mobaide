package com.mobaide.app.di

import android.content.Context
import com.mobaide.app.data.auth.ConnectionStore
import com.mobaide.app.data.auth.DeviceIdentityStore
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.auth.UserIdentityStore
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object AuthModule {

    @Provides
    @Singleton
    fun provideTokenStore(@ApplicationContext context: Context): TokenStore {
        return TokenStore(context)
    }

    @Provides
    @Singleton
    fun provideConnectionStore(@ApplicationContext context: Context): ConnectionStore {
        return ConnectionStore(context)
    }

    @Provides
    @Singleton
    fun provideUserIdentityStore(@ApplicationContext context: Context): UserIdentityStore {
        return UserIdentityStore(context)
    }

    @Provides
    @Singleton
    fun provideDeviceIdentityStore(@ApplicationContext context: Context): DeviceIdentityStore {
        return DeviceIdentityStore(context)
    }
}
