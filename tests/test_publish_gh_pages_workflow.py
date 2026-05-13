"""Regression tests for ``.github/workflows/publish-gh-pages.yml``.

The workflow publishes ``deploy/install.sh``, ``deploy/install.ps1`` and
``deploy/windows_service.py`` from ``main`` to the ``gh-pages`` branch on
every push to ``main`` that touches those files. The triple is the
canonical content of ``get.mobaide.com``.

This test module pins the co-versioning contract that the
``Service module source not found at ...\\releases\\<v>\\venv\\...``
failure-class fix relies on. ``install.ps1`` downloads
``windows_service.py`` from the same gh-pages distribution channel it is
itself served from; both files MUST move together. If a future PR drops
``deploy/windows_service.py`` from any of the four references in the
workflow YAML (the ``paths:`` trigger, the ``git show`` copy, the
``git diff --quiet`` no-op gate, or the ``git add`` stage), the bug
class returns silently: a user running the new ``install.ps1`` would
hit a stale or 404 ``windows_service.py`` URL because the workflow
either failed to fire or failed to publish the updated file.

Mirror of the install.ps1 pins in ``tests/test_install_ps1.py``:
``test_install_service_downloads_service_module_to_svc_dir``,
``test_install_service_does_not_copy_from_release_wheel`` and
``test_install_ps1_defines_windows_service_url_with_env_override``.
Those pin the installer side; this module pins the publisher side.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml


_REPO_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOW_PATH = _REPO_ROOT / ".github" / "workflows" / "publish-gh-pages.yml"


@pytest.fixture(scope="module")
def workflow_text() -> str:
    assert _WORKFLOW_PATH.is_file(), f"missing workflow file: {_WORKFLOW_PATH}"
    return _WORKFLOW_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def workflow_doc(workflow_text: str) -> dict:
    return yaml.safe_load(workflow_text)


@pytest.fixture(scope="module")
def publish_step_run(workflow_doc: dict) -> str:
    """Return the ``run:`` body of the ``Sync install scripts ...`` step.

    The step is identified by name rather than index so reordering the
    job's steps does not silently void the assertions below.
    """
    jobs = workflow_doc.get("jobs", {})
    publish_job = jobs.get("publish")
    assert publish_job is not None, (
        "publish-gh-pages.yml must define a `publish` job. Found jobs: "
        + repr(list(jobs))
    )
    steps = publish_job.get("steps", [])
    for step in steps:
        if "Sync install scripts" in step.get("name", ""):
            run = step.get("run")
            assert isinstance(run, str), (
                "the `Sync install scripts ...` step must have a string "
                "`run:` body. Found: " + repr(run)
            )
            return run
    pytest.fail(
        "no step named `Sync install scripts ...` found in publish job. "
        "Steps: " + repr([s.get("name") for s in steps])
    )


def test_workflow_yaml_parses_cleanly(workflow_doc: dict) -> None:
    """The workflow file must be valid YAML.

    Cheap up-front sanity check — every assertion below depends on the
    document parsing. If a malformed colon or indentation slips in,
    surface that here rather than as a confusing ``KeyError`` deeper in
    the suite.
    """
    assert isinstance(workflow_doc, dict), (
        "publish-gh-pages.yml must parse as a YAML mapping at top level. "
        "Got: " + repr(type(workflow_doc))
    )
    # PyYAML parses the top-level `on:` key as the boolean `True` (YAML 1.1
    # legacy: "on" is a truthy alias). Accept either spelling so a future
    # PyYAML / ruamel.yaml swap that fixes the quirk does not flip this
    # assertion red.
    assert "on" in workflow_doc or True in workflow_doc, (
        "publish-gh-pages.yml must declare an `on:` trigger block. Keys: "
        + repr(list(workflow_doc))
    )
    assert "jobs" in workflow_doc, (
        "publish-gh-pages.yml must declare a `jobs:` block. Keys: "
        + repr(list(workflow_doc))
    )


def test_workflow_triggers_on_windows_service_py_change(workflow_doc: dict) -> None:
    """``deploy/windows_service.py`` must be in ``on.push.paths``.

    Without this entry, a PR that updates ``deploy/windows_service.py``
    without also touching ``install.sh`` / ``install.ps1`` would not
    fire the publish workflow at all. gh-pages would keep serving the
    previous revision while ``install.ps1`` (unchanged) downloads it,
    so the on-disk ``<prefix>\\svc\\mobaide_service.py`` would silently
    desync from the source tree. This is the exact failure mode the
    decoupling fix was written to eliminate; the trigger is half of
    the co-versioning contract.
    """
    on_block = workflow_doc.get("on") or workflow_doc.get(True)
    assert isinstance(on_block, dict), (
        "publish-gh-pages.yml's `on:` trigger must be a mapping. Got: "
        + repr(on_block)
    )
    push = on_block.get("push")
    assert isinstance(push, dict), (
        "publish-gh-pages.yml must define `on.push:` (the canonical "
        "trigger for gh-pages publishes). Got: " + repr(push)
    )
    paths = push.get("paths") or []
    assert "deploy/windows_service.py" in paths, (
        "publish-gh-pages.yml's `on.push.paths` must include "
        "`deploy/windows_service.py` so changes to the service module "
        "fire the publish workflow. Without this trigger, a "
        "windows_service.py edit ships to main but gh-pages keeps "
        "serving the stale revision, breaking the co-versioning "
        "contract that lets install.ps1 fetch it safely. "
        "Found paths: " + repr(paths)
    )


def test_workflow_copies_windows_service_py_into_gh_pages_root(
    publish_step_run: str,
) -> None:
    """The publish step must materialise ``windows_service.py``.

    The step copies the three publishable files out of ``main`` into the
    gh-pages worktree via ``git show origin/main:<path> > <basename>``.
    The ``windows_service.py`` line must be present, point at the
    ``deploy/`` source path, and write to the top-level filename
    ``windows_service.py`` (the file ``install.ps1`` will request as
    ``https://get.mobaide.com/windows_service.py``).
    """
    pattern = (
        r"git\s+show\s+origin/main:deploy/windows_service\.py\s*>\s*"
        r"windows_service\.py"
    )
    assert re.search(pattern, publish_step_run), (
        "publish-gh-pages.yml's publish step must include the line "
        "`git show origin/main:deploy/windows_service.py > "
        "windows_service.py` so the file appears at the gh-pages root. "
        "Without it, the trigger fires but no file is staged, the "
        "`git diff --quiet` short-circuits, and the workflow exits "
        "without publishing. Publish-step body:\n" + publish_step_run
    )


def test_workflow_diff_gate_includes_windows_service_py(
    publish_step_run: str,
) -> None:
    """The ``git diff --quiet`` no-op gate must list ``windows_service.py``.

    The publish step bails out early when none of the three publishable
    files changed (``git diff --quiet -- install.sh install.ps1
    windows_service.py``). If ``windows_service.py`` is missing from
    the gate, a workflow run triggered by a windows_service.py-only
    change would short-circuit on the install.sh/install.ps1 comparison
    (which would be a no-op) and exit without publishing. The trigger
    fires, but the publish never happens — a textbook missing-tracked-
    files bug.
    """
    assert re.search(
        r"git\s+diff\s+--quiet[^\r\n]*\bwindows_service\.py\b",
        publish_step_run,
    ), (
        "publish-gh-pages.yml's `git diff --quiet -- ...` no-op gate "
        "must include `windows_service.py` so the gate detects "
        "windows_service.py-only changes. Otherwise the workflow "
        "silently exits without publishing when only the service "
        "module is updated. Publish-step body:\n" + publish_step_run
    )


def test_workflow_git_add_includes_windows_service_py(
    publish_step_run: str,
) -> None:
    """The ``git add`` line must stage ``windows_service.py`` for commit.

    The publish step ends with ``git add install.sh install.ps1
    windows_service.py`` followed by ``git commit`` + ``git push``. If
    ``windows_service.py`` is omitted from the ``git add`` argument
    list (but the ``git show`` line is still there), the file is
    written to the worktree but never committed, so the gh-pages push
    publishes a tree that still serves the previous revision.
    """
    assert re.search(
        r"git\s+add[^\r\n]*\bwindows_service\.py\b",
        publish_step_run,
    ), (
        "publish-gh-pages.yml's `git add` line must include "
        "`windows_service.py` so the file is committed and pushed. "
        "Without it, `git show` writes the file but the subsequent "
        "commit excludes it; gh-pages publishes an unchanged tree. "
        "Publish-step body:\n" + publish_step_run
    )


def test_deploy_windows_service_py_exists_in_tree() -> None:
    """``deploy/windows_service.py`` must exist in the source tree.

    The four workflow assertions above pin the *references* in the
    workflow YAML; this one pins the referent. A workflow that
    references a non-existent path would still parse fine but every
    run would fail with ``fatal: path 'deploy/windows_service.py'
    does not exist`` at the ``git show`` step.
    """
    deploy_module = _REPO_ROOT / "deploy" / "windows_service.py"
    assert deploy_module.is_file(), (
        "`deploy/windows_service.py` must exist in the repo (it is the "
        "source the publish-gh-pages workflow copies to the gh-pages "
        "branch). The file was moved here from "
        "`mobaide_agent/mobaide/windows_service.py` to decouple it "
        "from the agent wheel. If this assertion fails, the move was "
        "reverted without updating the workflow. Missing: " + str(deploy_module)
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
