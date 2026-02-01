"""Shared dependencies for API endpoints."""

from typing import Annotated

from fastapi import Depends

from fastapi_app.core.config import Settings, get_settings

# Common dependencies
SettingsDep = Annotated[Settings, Depends(get_settings)]
