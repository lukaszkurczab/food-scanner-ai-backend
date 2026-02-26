from app.schemas.version import VersionResponse


def build_version_response(version: str) -> VersionResponse:
    return VersionResponse(version=version)
