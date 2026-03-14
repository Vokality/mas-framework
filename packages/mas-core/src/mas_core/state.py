"""Shared state typing.

The MAS server owns persistence; agents access state via gRPC.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any, TypeVar

from pydantic import BaseModel


StateType = TypeVar("StateType", bound=BaseModel | MutableMapping[str, Any])
