"""Tests for LinearApiClient — mock the gql transport layer."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from mobaide.core.projects_config import AutoScanFilter
from mobaide.linear.client import LinearApiClient, _build_issue_filter
from mobaide.linear.models import IssueState, LinearIssue


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


def _filter() -> AutoScanFilter:
    return AutoScanFilter()


class TestGetViewer:
    async def test_returns_viewer_data(self, client, mock_session):
        mock_session.execute.return_value = {
            "viewer": {"id": "user-123", "name": "Test User"}
        }
        result = await client.get_viewer()

        assert result == {"id": "user-123", "name": "Test User"}


class TestBuildIssueFilter:
    def test_default_filter_shape(self):
        f: dict = _build_issue_filter(AutoScanFilter())
        assert f["assignee"] == {"isMe": {"eq": True}}
        assert f["state"] == {"name": {"in": ["Proposals"]}}
        assert f["labels"] == {"name": {"in": ["AutoPipeline"]}}

    def test_multi_value_filters_use_in_clause(self):
        f: dict = _build_issue_filter(
            AutoScanFilter(
                state_names=["Proposals", "Backlog"],
                label_names=["AutoPipeline", "AI-ready"],
            ),
        )
        assert f["state"]["name"]["in"] == ["Proposals", "Backlog"]
        assert f["labels"]["name"]["in"] == ["AutoPipeline", "AI-ready"]


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
        issues = await client.get_auto_scan_issues(_filter())

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
        issues = await client.get_auto_scan_issues(_filter())

        assert issues == []

    async def test_handles_missing_nested_fields(self, client, mock_session):
        mock_session.execute.return_value = {"viewer": {}}
        issues = await client.get_auto_scan_issues(_filter())

        assert issues == []

    async def test_passes_filter_as_graphql_variable(self, client, mock_session):
        mock_session.execute.return_value = {
            "viewer": {"assignedIssues": {"nodes": []}}
        }
        cfg = AutoScanFilter(
            state_names=["Backlog", "Proposals"],
            label_names=["AI-ready"],
        )
        await client.get_auto_scan_issues(cfg)

        # The gql client was called once with the parameterized document and
        # a `variable_values={"filter": ...}` kwarg containing the
        # canonical IssueFilter dict.
        assert mock_session.execute.await_count == 1
        kwargs = mock_session.execute.await_args.kwargs
        variables = kwargs.get("variable_values") or {}
        f = variables.get("filter") or {}
        assert f.get("state") == {"name": {"in": ["Backlog", "Proposals"]}}
        assert f.get("labels") == {"name": {"in": ["AI-ready"]}}
        assert f.get("assignee") == {"isMe": {"eq": True}}


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


class TestGetIssueState:
    async def test_returns_issue_state_when_found(self, client, mock_session):
        mock_session.execute.return_value = {
            "issue": {
                "id": "issue-uuid-1",
                "identifier": "CLA-8",
                "state": {"id": "state-done", "name": "Done"},
            }
        }
        state = await client.get_issue_state("issue-uuid-1")

        assert isinstance(state, IssueState)
        assert state.id == "state-done"
        assert state.name == "Done"

    async def test_returns_none_when_issue_missing(self, client, mock_session):
        mock_session.execute.return_value = {"issue": None}
        assert await client.get_issue_state("missing-uuid") is None

    async def test_returns_none_on_empty_payload(self, client, mock_session):
        mock_session.execute.return_value = {}
        assert await client.get_issue_state("whatever") is None

    async def test_returns_none_when_execute_raises(self, client, mock_session):
        mock_session.execute.side_effect = RuntimeError("network down")
        assert await client.get_issue_state("uuid") is None

    async def test_returns_none_on_malformed_state(self, client, mock_session):
        mock_session.execute.return_value = {
            "issue": {"id": "x", "identifier": "CLA-1", "state": {}}
        }
        assert await client.get_issue_state("uuid") is None


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


class TestGetWorkflowStates:
    async def test_dedupes_by_name_across_teams(self, client, mock_session):
        mock_session.execute.return_value = {
            "viewer": {
                "teamMemberships": {
                    "nodes": [
                        {
                            "team": {
                                "id": "team-a",
                                "states": {
                                    "nodes": [
                                        {"id": "s1", "name": "Proposals", "type": "backlog"},
                                        {"id": "s2", "name": "In Progress", "type": "started"},
                                    ],
                                },
                            },
                        },
                        {
                            "team": {
                                "id": "team-b",
                                "states": {
                                    "nodes": [
                                        # Duplicate name with the same type — second occurrence
                                        # is silently dropped.
                                        {"id": "s9", "name": "Proposals", "type": "backlog"},
                                        {"id": "s3", "name": "Done", "type": "completed"},
                                    ],
                                },
                            },
                        },
                    ],
                },
            },
        }
        states = await client.get_workflow_states()
        names = sorted(s["name"] for s in states)
        assert names == ["Done", "In Progress", "Proposals"]
        # The first occurrence's type wins — verify the canonical pairing
        # survives the dedupe step.
        by_name = {s["name"]: s["type"] for s in states}
        assert by_name["Proposals"] == "backlog"
        assert by_name["In Progress"] == "started"
        assert by_name["Done"] == "completed"

    async def test_returns_empty_when_no_teams(self, client, mock_session):
        mock_session.execute.return_value = {"viewer": {"teamMemberships": {"nodes": []}}}
        assert await client.get_workflow_states() == []


class TestGetLabels:
    async def test_merges_org_and_team_labels_dedupes_by_name(
        self, client, mock_session,
    ):
        mock_session.execute.return_value = {
            "organization": {
                "labels": {
                    "nodes": [
                        {"id": "L1", "name": "AutoPipeline", "color": "#ff0000"},
                    ],
                },
            },
            "viewer": {
                "teamMemberships": {
                    "nodes": [
                        {
                            "team": {
                                "id": "team-a",
                                "labels": {
                                    "nodes": [
                                        # Org label name re-appears here — org wins on color.
                                        {"id": "L9", "name": "AutoPipeline", "color": "#ignored"},
                                        {"id": "L2", "name": "AI-ready", "color": "#00ff00"},
                                    ],
                                },
                            },
                        },
                    ],
                },
            },
        }
        labels = await client.get_labels()
        by_name = {l["name"]: l for l in labels}
        assert set(by_name.keys()) == {"AutoPipeline", "AI-ready"}
        assert by_name["AutoPipeline"]["color"] == "#ff0000"
        assert by_name["AI-ready"]["color"] == "#00ff00"

    async def test_handles_missing_color(self, client, mock_session):
        mock_session.execute.return_value = {
            "organization": {"labels": {"nodes": [{"id": "L1", "name": "X"}]}},
            "viewer": {"teamMemberships": {"nodes": []}},
        }
        labels = await client.get_labels()
        assert labels == [{"name": "X", "color": ""}]
