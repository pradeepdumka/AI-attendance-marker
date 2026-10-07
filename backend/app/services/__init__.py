"""Application services.

Business logic that should stay out of routers and ORM models belongs here.
Registration and login checks live in `app.services.auth`. Student roster
changes live in `app.services.students`. Teacher profile changes live in
`app.services.teachers`. Class, subject, and enrollment changes live in
`app.services.academics`. Face sample storage lives in
`app.services.face_enrollment`. Matching a frame to those samples lives
in `app.services.face_recognition`. Saving the match as attendance,
opening a teacher's session, and building teacher and student reports
live in `app.services.attendance`.
"""
