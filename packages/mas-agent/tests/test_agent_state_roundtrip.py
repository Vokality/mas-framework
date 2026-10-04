from __future__ import annotations

from typing import Literal

import pytest
from mas_agent import Agent, StateReloadError
from mas_proto.runtime.v1 import runtime_pb2 as mas_pb2
from mas_proto.runtime.v1 import runtime_pb2_grpc as mas_pb2_grpc
from pydantic import BaseModel, Field, ValidationError


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

    async def ResetState(
        self,
        request: mas_pb2.ResetStateRequest,
        metadata: list[tuple[str, str]] | None = None,
    ) -> mas_pb2.ResetStateResponse:
        del request, metadata
        self.state.clear()
        return mas_pb2.ResetStateResponse()


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


@pytest.mark.asyncio
async def test_independent_instances_do_not_overwrite_unmodified_state_fields() -> None:
    stub = _StateStub()
    first = Agent("worker", state_model=NestedState)
    second = Agent("worker", state_model=NestedState)
    for agent in (first, second):
        agent._stub = stub
        agent._state = NestedState()
    await first.update_state({"count": 3})
    await second.update_state({"enabled": True})
    await first.refresh_state()
    assert first.state.count == 3
    assert first.state.enabled


@pytest.mark.asyncio
async def test_state_field_alias_does_not_break_roundtrip() -> None:
    class AliasedState(BaseModel):
        count: int = Field(default=0, alias="countValue")

    stub = _StateStub()
    agent = Agent("worker", state_model=AliasedState)
    agent._stub = stub
    agent._state = AliasedState()
    await agent.update_state({"count": 3})
    await agent.refresh_state()
    assert agent.state.count == 3


@pytest.mark.asyncio
async def test_invalid_defaults_do_not_erase_persisted_state() -> None:
    class RequiredState(BaseModel):
        count: int

    stub = _StateStub({"count": "3"})
    agent = Agent("worker", state_model=RequiredState)
    agent._stub = stub
    agent._state = RequiredState(count=3)
    with pytest.raises(ValidationError):
        await agent.reset_state()
    assert stub.state == {"count": "3"}
    assert agent.state.count == 3
