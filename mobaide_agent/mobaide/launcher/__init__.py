"""Supervisor shim that exec's into the currently-pinned release.

The launcher is a tiny binary registered with the platform's service manager
(systemd / launchd / Windows SCM). It reads ``<prefix>/current_version`` and
``os.execv``-es into the matching ``<prefix>/releases/<version>/venv``. The
staged-binary auto-updater stages new releases into sibling directories and
atomically rewrites the ``current_version`` file; the launcher then reboots
into the new release on the next supervisor restart.

This module is the only PyInstaller-bundled artifact on Windows; on
Linux/macOS it ships as plain Python source, invoked by the service manager
via the venv's ``python -m mobaide.launcher`` entrypoint.
"""
