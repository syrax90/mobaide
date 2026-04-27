package com.mobaide.app.di

import javax.inject.Qualifier

/** Marks the OkHttpClient / API instance targeting the central server (user auth, agent CRUD). */
@Qualifier
@Retention(AnnotationRetention.BINARY)
annotation class CentralApi

/** Marks the OkHttpClient / API instance targeting a per-agent URL (sessions, WebSocket). */
@Qualifier
@Retention(AnnotationRetention.BINARY)
annotation class AgentApi
