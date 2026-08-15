"""The anonymous account-deletion pages left over from the end-user portal.

Mounted at ``/portal/*``. Everything that needed a session moved to the browser
client; what remains is the emailed cancel-deletion link and the public
deletion-info page Google Play Console points at.
"""

from .router import router

__all__ = ["router"]
