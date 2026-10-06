"""HTTP routers.

Each module in this package defines one `APIRouter` for a single area of
the API. `app.main` mounts those routers. Keep handlers thin; password
checks and other business rules belong in `app.services`.
"""
