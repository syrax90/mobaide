"""End-user web portal — sibling of the admin dashboard.

Mounted at ``/portal/*``. Cookie name (``portal_token``) is deliberately
distinct from the admin dashboard's ``dashboard_token`` so a non-admin can
never gain a session in the admin namespace.
"""

from .router import router

__all__ = ["router"]
