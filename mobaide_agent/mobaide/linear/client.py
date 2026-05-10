from __future__ import annotations

try:
    from mobaide.agent.logging import audit_event, get_logger, log_call
except Exception:  # pragma: no cover
    import logging
    def get_logger(name): return logging.getLogger(name)
    def log_call(*args, **kwargs):
        def deco(fn): return fn
        return deco
    def audit_event(*args, **kwargs): pass

from gql import Client, gql
from gql.transport.httpx import HTTPXAsyncTransport

from mobaide.core.projects_config import AutoScanFilter

from .models import IssueLabel, IssueState, LinearIssue
from .rate_limiter import LinearRateLimiter

logger = get_logger(__name__)

_LINEAR_ENDPOINT = "https://api.linear.app/graphql"

# Page sizes for the discovery queries. Linear's complexity calculator
# multiplies per-connection page sizes (documented per-query cap: 10,000
# complexity points). Both nested queries below have a single nested level,
# so the budget is `_TEAMS_PAGE * _LABELS_PAGE` (or _STATES_PAGE).
# Keeping all three at 50 yields 50*50 = 2,500 — comfortably below the cap
# for any reasonably-sized workspace. Workspaces with more than 50 teams
# would page off the end here; for the auto-scan use case (one user's
# assigned issues) that surface is acceptable.
_TEAMS_PAGE = 50
_STATES_PAGE = 50
_LABELS_PAGE = 50

_QUERY_VIEWER = gql("""
query {
  viewer { id name }
}
""")

# Parameterized issue query — the filter is supplied as an `IssueFilter!`
# variable so we can configure trigger states/labels per project without
# splicing strings into the document. The `assignee.isMe` clause stays
# baked in — only labels and states are user-configurable in this iteration.
_QUERY_AUTO_SCAN_ISSUES = gql("""
query ($filter: IssueFilter!) {
  viewer {
    assignedIssues(filter: $filter) {
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

_QUERY_WORKFLOW_STATES = gql("""
query ($teamsFirst: Int!, $statesFirst: Int!) {
  viewer {
    teamMemberships(first: $teamsFirst) {
      nodes {
        team {
          id
          states(first: $statesFirst) {
            nodes { id name type }
          }
        }
      }
    }
  }
}
""")

_QUERY_LABELS = gql("""
query ($teamsFirst: Int!, $labelsFirst: Int!) {
  viewer {
    teamMemberships(first: $teamsFirst) {
      nodes {
        team {
          id
          labels(first: $labelsFirst) {
            nodes { id name color }
          }
        }
      }
    }
  }
  organization {
    labels(first: $labelsFirst) {
      nodes { id name color }
    }
  }
}
""")


def _build_issue_filter(filter_cfg: AutoScanFilter) -> dict[str, object]:
    """Build an ``IssueFilter`` variable dict from the configured filter.

    Linear's filter syntax wraps each field in a comparator object; for
    multi-value matches we use ``{ name: { in: [...] } }``. The
    ``assignee.isMe`` clause is added unconditionally — only the trigger
    states/labels are user-configurable in this iteration.
    """
    return {
        "assignee": {"isMe": {"eq": True}},
        "state": {"name": {"in": list(filter_cfg.state_names)}},
        "labels": {"name": {"in": list(filter_cfg.label_names)}},
    }


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
    async def get_auto_scan_issues(
        self, filter_cfg: AutoScanFilter,
    ) -> list[LinearIssue]:
        """Query issues assigned to the viewer matching the configured filter.

        ``filter_cfg`` supplies the trigger states and labels that an issue
        must match to enter the auto-scan pipeline. The ``assignee.isMe``
        condition is always applied.
        """
        await self._gate()
        variables = {"filter": _build_issue_filter(filter_cfg)}
        async with self._client as session:
            result = await session.execute(
                _QUERY_AUTO_SCAN_ISSUES, variable_values=variables,
            )

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

    @log_call(name="linear.get_workflow_states")
    async def get_workflow_states(self) -> list[dict[str, str]]:
        """Return all workflow states visible to the viewer, deduped by name.

        States are team-scoped in Linear, but the auto-scan filter matches
        on ``state.name``, so callers select by name across teams. This
        method returns one entry per unique name; the ``type`` of the first
        occurrence wins (Linear's state types are consistent across teams
        for canonically-named states like ``Proposals`` / ``In Progress``).
        """
        await self._gate()
        async with self._client as session:
            result = await session.execute(
                _QUERY_WORKFLOW_STATES,
                variable_values={
                    "teamsFirst": _TEAMS_PAGE,
                    "statesFirst": _STATES_PAGE,
                },
            )

        seen: dict[str, dict[str, str]] = {}
        memberships = (
            (result or {})
            .get("viewer", {})
            .get("teamMemberships", {})
            .get("nodes", [])
        )
        for membership in memberships:
            team = (membership or {}).get("team") or {}
            for s in team.get("states", {}).get("nodes", []):
                name = s.get("name")
                if not isinstance(name, str) or not name:
                    continue
                if name in seen:
                    continue
                stype = s.get("type")
                seen[name] = {
                    "name": name,
                    "type": stype if isinstance(stype, str) else "",
                }
        return list(seen.values())

    @log_call(name="linear.get_labels")
    async def get_labels(self) -> list[dict[str, str]]:
        """Return all labels visible to the viewer, deduped by name.

        Merges workspace-level labels (``organization.labels``) with
        team-scoped ones (``viewer.teamMemberships[].team.labels``).
        Workspace labels win on color when a name appears in both, since
        team labels in Linear are commonly inherited / aliased from the
        organization layer.
        """
        await self._gate()
        async with self._client as session:
            result = await session.execute(
                _QUERY_LABELS,
                variable_values={
                    "teamsFirst": _TEAMS_PAGE,
                    "labelsFirst": _LABELS_PAGE,
                },
            )

        seen: dict[str, dict[str, str]] = {}
        org_labels = (
            (result or {}).get("organization", {}).get("labels", {}).get("nodes", [])
        )
        for lab in org_labels:
            name = lab.get("name")
            if not isinstance(name, str) or not name:
                continue
            color = lab.get("color")
            seen[name] = {
                "name": name,
                "color": color if isinstance(color, str) else "",
            }
        memberships = (
            (result or {})
            .get("viewer", {})
            .get("teamMemberships", {})
            .get("nodes", [])
        )
        for membership in memberships:
            team = (membership or {}).get("team") or {}
            for lab in team.get("labels", {}).get("nodes", []):
                name = lab.get("name")
                if not isinstance(name, str) or not name:
                    continue
                if name in seen:
                    continue
                color = lab.get("color")
                seen[name] = {
                    "name": name,
                    "color": color if isinstance(color, str) else "",
                }
        return list(seen.values())
