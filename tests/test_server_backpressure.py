from __future__ import annotations

import asyncio

from mas._proto.v1 import mas_pb2
from mas.server import MASServer, _InflightDelivery


def _event(delivery_id: str) -> mas_pb2.ServerEvent:
    return mas_pb2.ServerEvent(
        delivery=mas_pb2.Delivery(delivery_id=delivery_id, envelope_json="{}")
    )


def test_drop_oldest_outbound_removes_inflight() -> None:
    outbound: asyncio.Queue[mas_pb2.ServerEvent] = asyncio.Queue(maxsize=2)
    inflight: dict[str, _InflightDelivery] = {
        "d1": _InflightDelivery(
            stream_name="agent.stream:worker",
            group="agents",
            entry_id="1-0",
            envelope_json="{}",
            received_at=0.0,
        ),
        "d2": _InflightDelivery(
            stream_name="agent.stream:worker",
            group="agents",
            entry_id="2-0",
            envelope_json="{}",
            received_at=0.0,
        ),
    }

    outbound.put_nowait(_event("d1"))
    outbound.put_nowait(_event("d2"))

    dropped = MASServer._drop_oldest_outbound(outbound, inflight)

    assert dropped == 1
    assert "d1" not in inflight
    assert outbound.qsize() == 1
