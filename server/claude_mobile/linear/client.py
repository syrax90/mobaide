from __future__ import annotations

import logging

from gql import Client, gql
from gql.transport.httpx import HTTPXAsyncTransport

from .models import IssueLabel, IssueState, LinearIssue

logger = logging.getLogger(__name__)

_LINEAR_ENDPOINT = "https://api.linear.app/graphql"

_QUERY_VIEWER = gql("""
query {
  viewer { id name }
}
""")

_QUERY_AUTO_SCAN_ISSUES = gql("""
query {
  viewer {
    assignedIssues(
      filter: {
        state: { name: { eq: "Proposals" } }
        labels: { name: { eq: "AutoPipeline" } }
      }
    ) {
      nodes {
        id
        identifier
        title
        description
        state { id name }
        labels { nodes { id name } }
        team { id states { nodes { id name } } }
      }
    }
  }
}
""")

_MUTATION_UPDATE_STATE = gql("""
mutation ($issueId: String!, $stateId: String!) {
  issueUpdate(id: $issueId, input: { stateId: $stateId }) {
    success
    issue { id identifier state { name } }
  }
}
""")

_MUTATION_REMOVE_LABEL = gql("""
mutation ($issueId: String!, $labelId: String!) {
  issueRemoveLabel(id: $issueId, labelId: $labelId) {
    success
  }
}
""")


class LinearApiClient:
    """Async GraphQL client for the Linear API.

    Mirrors the PS1 script's Invoke-LinearGraphQL, Get-LinearAutoScanIssues,
    Update-LinearIssueState, and Remove-LinearIssueLabel functions.
    """

    def __init__(self, api_key: str) -> None:
        transport = HTTPXAsyncTransport(
            url=_LINEAR_ENDPOINT,
            headers={"Authorization": api_key},
        )
        self._client = Client(transport=transport, fetch_schema_from_transport=False)

    async def get_viewer(self) -> dict:
        """Return the authenticated user's id and name."""
        async with self._client as session:
            result = await session.execute(_QUERY_VIEWER)
        return result["viewer"]

    async def get_auto_scan_issues(self) -> list[LinearIssue]:
        """Query issues assigned to the viewer with status Proposals + label AutoPipeline."""
        async with self._client as session:
            result = await session.execute(_QUERY_AUTO_SCAN_ISSUES)

        nodes = result.get("viewer", {}).get("assignedIssues", {}).get("nodes", [])
        issues: list[LinearIssue] = []
        for node in nodes:
            team_states = [
                IssueState(id=s["id"], name=s["name"])
                for s in node.get("team", {}).get("states", {}).get("nodes", [])
            ]
            labels = [
                IssueLabel(id=l["id"], name=l["name"])
                for l in node.get("labels", {}).get("nodes", [])
            ]
            issues.append(
                LinearIssue(
                    id=node["id"],
                    identifier=node["identifier"],
                    title=node["title"],
                    description=node.get("description"),
                    state=IssueState(
                        id=node["state"]["id"], name=node["state"]["name"]
                    ),
                    labels=labels,
                    team_states=team_states,
                )
            )

        logger.debug("Fetched %d issue(s) from Linear", len(issues))
        return issues

    async def update_issue_state(self, issue_id: str, state_id: str) -> bool:
        """Transition a Linear issue to a new workflow state."""
        async with self._client as session:
            result = await session.execute(
                _MUTATION_UPDATE_STATE,
                variable_values={"issueId": issue_id, "stateId": state_id},
            )
        return result.get("issueUpdate", {}).get("success", False)

    async def remove_issue_label(self, issue_id: str, label_id: str) -> bool:
        """Remove a label from a Linear issue."""
        async with self._client as session:
            result = await session.execute(
                _MUTATION_REMOVE_LABEL,
                variable_values={"issueId": issue_id, "labelId": label_id},
            )
        return result.get("issueRemoveLabel", {}).get("success", False)
