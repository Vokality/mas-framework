from __future__ import annotations

import pytest
from mas_agent import Agent
from mas_server import AgentDefinition
from mas_server.errors import PermissionDeniedError

pytestmark = pytest.mark.asyncio


async def test_reply_sender_must_match_request_target(
    mas_server_factory,
    test_tls,
) -> None:
    server = await mas_server_factory(
        {
            "requester": AgentDefinition(
                agent_id="requester", capabilities=[], metadata={}
            ),
            "responder": AgentDefinition(
                agent_id="responder", capabilities=[], metadata={}
            ),
            "attacker": AgentDefinition(
                agent_id="attacker", capabilities=[], metadata={}
            ),
        }
    )
    await server.authz.set_permissions("requester", allowed_targets=["responder"])
    await server.authz.set_permissions("responder", allowed_targets=["requester"])
    # Give the attacker ordinary send permission to prove the denial is the
    # correlation binding, not ACL policy.
    await server.authz.set_permissions("attacker", allowed_targets=["requester"])

    requester = Agent(
        "requester",
        server_addr=server.bound_addr,
        tls=test_tls.client("requester"),
    )
    responder = Agent(
        "responder",
        server_addr=server.bound_addr,
        tls=test_tls.client("responder"),
    )
    attacker = Agent(
        "attacker",
        server_addr=server.bound_addr,
        tls=test_tls.client("attacker"),
    )

    await requester.start()
    await responder.start()
    await attacker.start()
    try:
        _message_id, correlation_id = await server.request_message(
            sender_id="requester",
            sender_instance_id=requester.instance_id,
            target_id="responder",
            message_type="question",
            data_json="{}",
            timeout_ms=5000,
        )

        with pytest.raises(PermissionDeniedError):
            await server.reply_message(
                sender_id="attacker",
                sender_instance_id=attacker.instance_id,
                correlation_id=correlation_id,
                message_type="answer",
                data_json="{}",
            )

        reply_id = await server.reply_message(
            sender_id="responder",
            sender_instance_id=responder.instance_id,
            correlation_id=correlation_id,
            message_type="answer",
            data_json="{}",
        )
        assert reply_id
    finally:
        await attacker.stop()
        await responder.stop()
        await requester.stop()
