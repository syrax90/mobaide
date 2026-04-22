from __future__ import annotations

try:
    from claude_mobile.agent.logging import audit_event, get_logger, log_call
except Exception:  # pragma: no cover
    import logging
    def get_logger(name): return logging.getLogger(name)
    def log_call(*args, **kwargs):
        def deco(fn): return fn
        return deco
    def audit_event(*args, **kwargs): pass

from gql import Client, gql
from gql.transport.httpx import HTTPXAsyncTransport

from .models import IssueLabel, IssueState, LinearIssue
from .rate_limiter import LinearRateLimiter

logger = get_logger(__name__)

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

_QUERY_ISSUE_STATE = gql("""
query ($id: String!) {
  issue(id: $id) {
    id
    identifier
    state { id name }
  }
}
""")


class LinearApiClient:
    """Async GraphQL client for the Linear API."""

    def __init__(
        self,
        api_key: str,
        rate_limiter: LinearRateLimiter | None = None,
    ) -> None:
        self._api_key = api_key
        self._rate_limiter = rate_limiter
        transport = HTTPXAsyncTransport(
            url=_LINEAR_ENDPOINT,
            headers={"Authorization": api_key},
        )
        self._client = Client(transport=transport, fetch_schema_from_transport=False)

    async def _gate(self) -> None:
        if self._rate_limiter is not None:
            await self._rate_limiter.acquire(self._api_key)

    async def get_viewer(self) -> dict:
        """Return the authenticated user's id and name."""
        await self._gate()
        async with self._client as session:
            result = await session.execute(_QUERY_VIEWER)
        return result["viewer"]

    @log_call(name="linear.get_auto_scan_issues")
    async def get_auto_scan_issues(self) -> list[LinearIssue]:
        """Query issues assigned to the viewer with status Proposals + label AutoPipeline."""
        await self._gate()
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

    @log_call(name="linear.update_issue_state", log_args=("issue_id", "state_id"))
    async def update_issue_state(self, issue_id: str, state_id: str) -> bool:
        """Transition a Linear issue to a new workflow state."""
        async with self._client as session:
            result = await session.execute(
                _MUTATION_UPDATE_STATE,
                variable_values={"issueId": issue_id, "stateId": state_id},
            )
        success = result.get("issueUpdate", {}).get("success", False)
        if success:
            audit_event(
                "audit.linear.issue_state_changed",
                target=issue_id,
                outcome="ok",
                state_id=state_id,
            )
        return success

    @log_call(name="linear.get_issue_state", log_args=("issue_uuid",))
    async def get_issue_state(self, issue_uuid: str) -> IssueState | None:
        """Query the current workflow state of a single Linear issue.

        Returns the issue's current :class:`IssueState`, or ``None`` if
        the query fails or the issue cannot be found. Errors are logged
        as warnings — callers should treat ``None`` as "unknown" and
        typically retry on the next scan cycle.
        """
        await self._gate()
        try:
            async with self._client as session:
                result = await session.execute(
                    _QUERY_ISSUE_STATE,
                    variable_values={"id": issue_uuid},
                )
        except Exception as exc:
            logger.warning(
                "linear.get_issue_state.failed uuid=%s error=%s",
                issue_uuid, exc,
            )
            return None
        issue = (result or {}).get("issue")
        if not issue:
            logger.warning(
                "linear.get_issue_state.not_found uuid=%s", issue_uuid,
            )
            return None
        state = issue.get("state") or {}
        name = state.get("name")
        sid = state.get("id")
        if not isinstance(name, str) or not isinstance(sid, str):
            logger.warning(
                "linear.get_issue_state.malformed uuid=%s payload=%s",
                issue_uuid, issue,
            )
            return None
        return IssueState(id=sid, name=name)

    @log_call(name="linear.remove_issue_label", log_args=("issue_id", "label_id"))
    async def remove_issue_label(self, issue_id: str, label_id: str) -> bool:
        """Remove a label from a Linear issue."""
        async with self._client as session:
            result = await session.execute(
                _MUTATION_REMOVE_LABEL,
                variable_values={"issueId": issue_id, "labelId": label_id},
            )
        success = result.get("issueRemoveLabel", {}).get("success", False)
        if success:
            audit_event(
                "audit.linear.label_removed",
                target=issue_id,
                outcome="ok",
                label_id=label_id,
            )
        return success
