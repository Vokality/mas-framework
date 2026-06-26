from __future__ import annotations

from typing import Literal

import pytest
from mas_agent import Agent, StateReloadError
from mas_proto.runtime.v1 import runtime_pb2 as mas_pb2
from mas_proto.runtime.v1 import runtime_pb2_grpc as mas_pb2_grpc
from pydantic import BaseModel


class NestedState(BaseModel):
    count: int = 0
    enabled: bool = False
    optional_note: str | None = None
    tags: list[str] = []
    preferences: dict[str, int] = {}
    mode: Literal["idle", "busy"] = "idle"


class _StateStub(mas_pb2_grpc.RuntimeServiceStub):
    def __init__(self, state: dict[str, str] | None = None) -> None:
        self.state = state or {}

    async def GetState(
        self,
        _request: mas_pb2.GetStateRequest,
        metadata: list[tuple[str, str]] | None = None,
    ) -> mas_pb2.GetStateResponse:
        del metadata
        return mas_pb2.GetStateResponse(state=self.state)

    async def UpdateState(
        self,
        request: mas_pb2.UpdateStateRequest,
        metadata: list[tuple[str, str]] | None = None,
    ) -> mas_pb2.UpdateStateResponse:
        del metadata
        self.state.update(dict(request.updates))
        return mas_pb2.UpdateStateResponse()


@pytest.mark.asyncio
async def test_state_round_trips_nested_json_values() -> None:
    stub = _StateStub()
    writer = Agent("worker", state_model=NestedState)
    writer._stub = stub
    writer._state = NestedState()

    await writer.update_state(
        {
            "count": 3,
            "enabled": True,
            "optional_note": None,
            "tags": ["a", "b"],
            "preferences": {"retries": 2},
            "mode": "busy",
        }
    )

    reader = Agent("worker", state_model=NestedState)
    reader._stub = stub
    await reader.refresh_state()

    assert reader.state == NestedState(
        count=3,
        enabled=True,
        optional_note=None,
        tags=["a", "b"],
        preferences={"retries": 2},
        mode="busy",
    )


@pytest.mark.asyncio
async def test_state_reload_failure_is_not_silently_reset() -> None:
    agent = Agent("worker", state_model=NestedState)
    agent._stub = _StateStub({"tags": "not-a-json-list"})

    with pytest.raises(StateReloadError):
        await agent.refresh_state()
