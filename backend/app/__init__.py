"""AI Attendance Marker backend.

The API loads configuration from the environment, connects to MySQL through
SQLAlchemy when a request needs a session, and exposes health and
authentication routes. Face recognition is a later phase.
"""
