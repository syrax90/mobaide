package com.mobaide.app.ui.templates

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobaide.app.data.network.dto.TemplateDto
import com.mobaide.app.data.repository.TemplatesRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

sealed interface EditorState {
    object Hidden : EditorState
    data class Open(val target: TemplateDto?) : EditorState
}

data class TemplatesUiState(
    val templates: List<TemplateDto> = emptyList(),
    val isLoading: Boolean = false,
    val error: String? = null,
    val editorState: EditorState = EditorState.Hidden,
    val deleteTarget: TemplateDto? = null,
)

@HiltViewModel
class TemplatesViewModel @Inject constructor(
    private val templatesRepository: TemplatesRepository,
) : ViewModel() {

    private val _uiState = MutableStateFlow(TemplatesUiState())
    val uiState: StateFlow<TemplatesUiState> = _uiState.asStateFlow()

    init {
        viewModelScope.launch {
            templatesRepository.observeTemplates().collect { templates ->
                _uiState.update { it.copy(templates = templates) }
            }
        }
        refresh()
    }

    fun refresh() {
        viewModelScope.launch {
            _uiState.update { it.copy(isLoading = true, error = null) }
            try {
                templatesRepository.refreshTemplates()
            } catch (t: Throwable) {
                _uiState.update {
                    it.copy(error = t.message ?: "Failed to load templates")
                }
            } finally {
                _uiState.update { it.copy(isLoading = false) }
            }
        }
    }

    fun clearError() {
        _uiState.update { it.copy(error = null) }
    }

    fun openCreateEditor() {
        _uiState.update { it.copy(editorState = EditorState.Open(target = null)) }
    }

    fun openEditEditor(template: TemplateDto) {
        _uiState.update { it.copy(editorState = EditorState.Open(target = template)) }
    }

    fun dismissEditor() {
        _uiState.update { it.copy(editorState = EditorState.Hidden) }
    }

    fun saveFromEditor(name: String, body: String) {
        val state = _uiState.value.editorState as? EditorState.Open ?: return
        viewModelScope.launch {
            try {
                if (state.target == null) {
                    templatesRepository.createTemplate(name, body)
                } else {
                    templatesRepository.updateTemplate(
                        state.target.template_id,
                        name = name,
                        body = body,
                    )
                }
                _uiState.update { it.copy(editorState = EditorState.Hidden) }
            } catch (t: Throwable) {
                _uiState.update {
                    it.copy(error = t.message ?: "Failed to save template")
                }
            }
        }
    }

    fun toggleFavorite(template: TemplateDto) {
        viewModelScope.launch {
            try {
                templatesRepository.updateTemplate(
                    template.template_id,
                    isFavorite = !template.is_favorite,
                )
            } catch (t: Throwable) {
                _uiState.update { it.copy(error = "Failed to update template") }
            }
        }
    }

    fun showDeleteConfirm(template: TemplateDto) {
        _uiState.update { it.copy(deleteTarget = template) }
    }

    fun dismissDeleteConfirm() {
        _uiState.update { it.copy(deleteTarget = null) }
    }

    fun confirmDelete(template: TemplateDto) {
        _uiState.update { it.copy(deleteTarget = null) }
        viewModelScope.launch {
            try {
                templatesRepository.deleteTemplate(template.template_id)
            } catch (t: Throwable) {
                _uiState.update { it.copy(error = "Failed to delete template") }
            }
        }
    }
}
