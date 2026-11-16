"""Herdr support: the dashboard, alerts and pane views for the herdr servers
that run on this computer.

Herdr TUI (binary ``herdr``) and herdr-core (binary ``hc``) share no agents
and no API, so each has its own backend. Everything on the wire uses Mobaide's
own records, so an upstream change costs an agent release and no client
release. Mobaide never starts or stops a server.
"""
