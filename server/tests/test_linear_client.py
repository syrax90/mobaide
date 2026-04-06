"""Tests for LinearApiClient — mock the gql transport layer."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from claude_mobile.linear.client import LinearApiClient
from claude_mobile.linear.models import LinearIssue


@pytest.fixture
def mock_session():
    """Create a mock gql session that can execute queries."""
    session = AsyncMock()
    return session


@pytest.fixture
def client(mock_session):
    c = LinearApiClient(api_key="lin_api_test_key")
    # Replace the gql Client with an async context manager that yields mock_session
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=mock_session)
    cm.__aexit__ = AsyncMock(return_value=False)
    c._client = cm
    return c


class TestGetViewer:
    async def test_returns_viewer_data(self, client, mock_session):
        mock_session.execute.return_value = {
            "viewer": {"id": "user-123", "name": "Test User"}
        }
        result = await client.get_viewer()

        assert result == {"id": "user-123", "name": "Test User"}


class TestGetAutoScanIssues:
    async def test_returns_parsed_issues(self, client, mock_session):
        mock_session.execute.return_value = {
            "viewer": {
                "assignedIssues": {
                    "nodes": [
                        {
                            "id": "issue-uuid-1",
                            "identifier": "CLA-8",
                            "title": "Test issue",
                            "description": "Some description",
                            "state": {"id": "state-1", "name": "Proposals"},
                            "labels": {
                                "nodes": [
                                    {"id": "label-1", "name": "AutoPipeline"}
                                ]
                            },
                            "team": {
                                "id": "team-1",
                                "states": {
                                    "nodes": [
                                        {"id": "state-1", "name": "Proposals"},
                                        {"id": "state-2", "name": "In Progress"},
                                    ]
                                },
                            },
                        }
                    ]
                }
            }
        }
        issues = await client.get_auto_scan_issues()

        assert len(issues) == 1
        issue = issues[0]
        assert isinstance(issue, LinearIssue)
        assert issue.id == "issue-uuid-1"
        assert issue.identifier == "CLA-8"
        assert issue.title == "Test issue"
        assert issue.state.name == "Proposals"
        assert len(issue.labels) == 1
        assert issue.labels[0].name == "AutoPipeline"
        assert len(issue.team_states) == 2

    async def test_returns_empty_list_when_no_issues(self, client, mock_session):
        mock_session.execute.return_value = {
            "viewer": {"assignedIssues": {"nodes": []}}
        }
        issues = await client.get_auto_scan_issues()

        assert issues == []

    async def test_handles_missing_nested_fields(self, client, mock_session):
        mock_session.execute.return_value = {"viewer": {}}
        issues = await client.get_auto_scan_issues()

        assert issues == []


class TestUpdateIssueState:
    async def test_returns_true_on_success(self, client, mock_session):
        mock_session.execute.return_value = {
            "issueUpdate": {
                "success": True,
                "issue": {
                    "id": "issue-1",
                    "identifier": "CLA-8",
                    "state": {"name": "In Progress"},
                },
            }
        }
        result = await client.update_issue_state("issue-1", "state-2")

        assert result is True

    async def test_returns_false_on_failure(self, client, mock_session):
        mock_session.execute.return_value = {
            "issueUpdate": {"success": False}
        }
        result = await client.update_issue_state("issue-1", "state-2")

        assert result is False


class TestRemoveIssueLabel:
    async def test_returns_true_on_success(self, client, mock_session):
        mock_session.execute.return_value = {
            "issueRemoveLabel": {"success": True}
        }
        result = await client.remove_issue_label("issue-1", "label-1")

        assert result is True

    async def test_returns_false_on_failure(self, client, mock_session):
        mock_session.execute.return_value = {
            "issueRemoveLabel": {"success": False}
        }
        result = await client.remove_issue_label("issue-1", "label-1")

        assert result is False
