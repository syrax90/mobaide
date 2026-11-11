"""Unit tests for the Pydantic wire models defined in
``mobaide.agent.projects.models``.

The REST integration tests in ``test_rest_projects.py`` exercise these
models indirectly through HTTP. These tests pin the dataclass <-> wire
model translation directly so a refactor of either side surfaces here
before it cascades into a wire-format regression.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from mobaide.agent.projects.models import AutoScanFilterModel


class TestAutoScanFilterModelRoundTrip:
    """The wire model is the only translation layer between HTTP JSON and
    the immutable :class:`AutoScanFilter` dataclass that the scanner reads.
    Both directions must preserve every field byte-for-byte; otherwise an
    edit cycle (GET -> form -> PUT) would silently mutate fields."""

    def test_to_dataclass_decouples_lists(self):
        # The dataclass is frozen, but its list fields are mutable
        # objects. ``to_dataclass`` must hand out fresh copies so a later
        # mutation of the wire model's list does not silently rewrite the
        # filter the scanner already snapshotted.
        m = AutoScanFilterModel(
            state_names=["Proposals"],
            label_names=["AutoPipeline"],
            active_state="In Progress",
            done_state="Done",
            removal_label="AutoPipeline",
        )

        f = m.to_dataclass()
        m.state_names.append("Backlog")

        assert f.state_names == ["Proposals"]


class TestAutoScanFilterModelValidation:
    """The wire model validates list/string min-length at the API boundary
    so a malformed body is rejected with 422 before any patch is applied
    to the store. These are pinned at unit level (no HTTP) so a future
    move of the validators stays detectable here."""

    def test_empty_state_names_rejected(self):
        with pytest.raises(ValidationError):
            AutoScanFilterModel(
                state_names=[],
                label_names=["AutoPipeline"],
                active_state="In Progress",
                done_state="Done",
                removal_label="AutoPipeline",
            )

    @pytest.mark.parametrize(
        "field",
        ["active_state", "done_state", "removal_label"],
    )
    def test_empty_string_singleton_rejected(self, field):
        kwargs = {
            "state_names": ["Proposals"],
            "label_names": ["AutoPipeline"],
            "active_state": "In Progress",
            "done_state": "Done",
            "removal_label": "AutoPipeline",
        }
        kwargs[field] = ""
        with pytest.raises(ValidationError):
            AutoScanFilterModel(**kwargs)


class TestAutoScanFilterModelElementValidators:
    """``min_length=1`` only validates list LENGTH, not element content. The
    explicit ``_reject_empty_elements`` validator on ``state_names`` and
    ``label_names`` is what guards against ``[""]`` flowing onto disk and
    producing a permanently-no-match scanner state once the empty string
    reaches Linear's filter dict."""

    @pytest.mark.parametrize(
        "field", ["state_names", "label_names"],
    )
    def test_empty_string_element_in_list_rejected(self, field):
        kwargs = {
            "state_names": ["Proposals"],
            "label_names": ["AutoPipeline"],
            "active_state": "In Progress",
            "done_state": "Done",
            "removal_label": "AutoPipeline",
        }
        kwargs[field] = [""]
        with pytest.raises(ValidationError):
            AutoScanFilterModel(**kwargs)

    @pytest.mark.parametrize(
        "field", ["active_state", "done_state", "removal_label"],
    )
    def test_whitespace_only_singleton_rejected(self, field):
        kwargs = {
            "state_names": ["Proposals"],
            "label_names": ["AutoPipeline"],
            "active_state": "In Progress",
            "done_state": "Done",
            "removal_label": "AutoPipeline",
        }
        kwargs[field] = "   "
        with pytest.raises(ValidationError):
            AutoScanFilterModel(**kwargs)
