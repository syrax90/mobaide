package com.claudemobile.app.data.repository

import com.claudemobile.app.data.network.RestApi
import com.claudemobile.app.data.network.dto.ProjectInfo

class ProjectsRepository(private val restApi: RestApi) {
    suspend fun listProjects(): List<ProjectInfo> = restApi.getProjects()
}
