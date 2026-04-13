package com.claudemobile.app.data.repository

import com.claudemobile.app.data.events.AgentEvent
import com.claudemobile.app.data.events.AgentEventBus
import com.claudemobile.app.data.network.RestApi
import com.claudemobile.app.data.network.dto.ProjectInfo
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

class ProjectsRepository(
    private val restApi: RestApi,
    private val eventBus: AgentEventBus,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val _projects = MutableStateFlow<List<ProjectInfo>>(emptyList())

    init {
        scope.launch {
            eventBus.stream<AgentEvent.ProjectsChanged>().collect { ev ->
                _projects.value = ev.projects
            }
        }
    }

    /** One-shot fetch (still used as a fallback seed if the bus snapshot hasn't arrived). */
    suspend fun listProjects(): List<ProjectInfo> = restApi.getProjects()

    fun observeProjects(): StateFlow<List<ProjectInfo>> = _projects.asStateFlow()
}
