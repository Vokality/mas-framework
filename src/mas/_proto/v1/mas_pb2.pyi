from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ClientEvent(_message.Message):
    __slots__ = ("hello", "ack", "nack", "heartbeat")
    HELLO_FIELD_NUMBER: _ClassVar[int]
    ACK_FIELD_NUMBER: _ClassVar[int]
    NACK_FIELD_NUMBER: _ClassVar[int]
    HEARTBEAT_FIELD_NUMBER: _ClassVar[int]
    hello: Hello
    ack: Ack
    nack: Nack
    heartbeat: Heartbeat
    def __init__(self, hello: _Optional[_Union[Hello, _Mapping]] = ..., ack: _Optional[_Union[Ack, _Mapping]] = ..., nack: _Optional[_Union[Nack, _Mapping]] = ..., heartbeat: _Optional[_Union[Heartbeat, _Mapping]] = ...) -> None: ...

class ServerEvent(_message.Message):
    __slots__ = ("welcome", "delivery", "error")
    WELCOME_FIELD_NUMBER: _ClassVar[int]
    DELIVERY_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    welcome: Welcome
    delivery: Delivery
    error: ServerError
    def __init__(self, welcome: _Optional[_Union[Welcome, _Mapping]] = ..., delivery: _Optional[_Union[Delivery, _Mapping]] = ..., error: _Optional[_Union[ServerError, _Mapping]] = ...) -> None: ...

class Hello(_message.Message):
    __slots__ = ("instance_id",)
    INSTANCE_ID_FIELD_NUMBER: _ClassVar[int]
    instance_id: str
    def __init__(self, instance_id: _Optional[str] = ...) -> None: ...

class Welcome(_message.Message):
    __slots__ = ("agent_id", "instance_id")
    AGENT_ID_FIELD_NUMBER: _ClassVar[int]
    INSTANCE_ID_FIELD_NUMBER: _ClassVar[int]
    agent_id: str
    instance_id: str
    def __init__(self, agent_id: _Optional[str] = ..., instance_id: _Optional[str] = ...) -> None: ...

class Delivery(_message.Message):
    __slots__ = ("delivery_id", "envelope_json")
    DELIVERY_ID_FIELD_NUMBER: _ClassVar[int]
    ENVELOPE_JSON_FIELD_NUMBER: _ClassVar[int]
    delivery_id: str
    envelope_json: str
    def __init__(self, delivery_id: _Optional[str] = ..., envelope_json: _Optional[str] = ...) -> None: ...

class Ack(_message.Message):
    __slots__ = ("delivery_id",)
    DELIVERY_ID_FIELD_NUMBER: _ClassVar[int]
    delivery_id: str
    def __init__(self, delivery_id: _Optional[str] = ...) -> None: ...

class Nack(_message.Message):
    __slots__ = ("delivery_id", "reason", "retryable")
    DELIVERY_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    RETRYABLE_FIELD_NUMBER: _ClassVar[int]
    delivery_id: str
    reason: str
    retryable: bool
    def __init__(self, delivery_id: _Optional[str] = ..., reason: _Optional[str] = ..., retryable: bool = ...) -> None: ...

class Heartbeat(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ServerError(_message.Message):
    __slots__ = ("code", "message")
    CODE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    code: str
    message: str
    def __init__(self, code: _Optional[str] = ..., message: _Optional[str] = ...) -> None: ...

class SendRequest(_message.Message):
    __slots__ = ("target_id", "message_type", "data_json", "instance_id")
    TARGET_ID_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_TYPE_FIELD_NUMBER: _ClassVar[int]
    DATA_JSON_FIELD_NUMBER: _ClassVar[int]
    INSTANCE_ID_FIELD_NUMBER: _ClassVar[int]
    target_id: str
    message_type: str
    data_json: str
    instance_id: str
    def __init__(self, target_id: _Optional[str] = ..., message_type: _Optional[str] = ..., data_json: _Optional[str] = ..., instance_id: _Optional[str] = ...) -> None: ...

class SendResponse(_message.Message):
    __slots__ = ("message_id",)
    MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    message_id: str
    def __init__(self, message_id: _Optional[str] = ...) -> None: ...

class RequestRequest(_message.Message):
    __slots__ = ("target_id", "message_type", "data_json", "timeout_ms", "instance_id")
    TARGET_ID_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_TYPE_FIELD_NUMBER: _ClassVar[int]
    DATA_JSON_FIELD_NUMBER: _ClassVar[int]
    TIMEOUT_MS_FIELD_NUMBER: _ClassVar[int]
    INSTANCE_ID_FIELD_NUMBER: _ClassVar[int]
    target_id: str
    message_type: str
    data_json: str
    timeout_ms: int
    instance_id: str
    def __init__(self, target_id: _Optional[str] = ..., message_type: _Optional[str] = ..., data_json: _Optional[str] = ..., timeout_ms: _Optional[int] = ..., instance_id: _Optional[str] = ...) -> None: ...

class RequestResponse(_message.Message):
    __slots__ = ("message_id", "correlation_id")
    MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    CORRELATION_ID_FIELD_NUMBER: _ClassVar[int]
    message_id: str
    correlation_id: str
    def __init__(self, message_id: _Optional[str] = ..., correlation_id: _Optional[str] = ...) -> None: ...

class ReplyRequest(_message.Message):
    __slots__ = ("correlation_id", "message_type", "data_json", "instance_id")
    CORRELATION_ID_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_TYPE_FIELD_NUMBER: _ClassVar[int]
    DATA_JSON_FIELD_NUMBER: _ClassVar[int]
    INSTANCE_ID_FIELD_NUMBER: _ClassVar[int]
    correlation_id: str
    message_type: str
    data_json: str
    instance_id: str
    def __init__(self, correlation_id: _Optional[str] = ..., message_type: _Optional[str] = ..., data_json: _Optional[str] = ..., instance_id: _Optional[str] = ...) -> None: ...

class ReplyResponse(_message.Message):
    __slots__ = ("message_id",)
    MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    message_id: str
    def __init__(self, message_id: _Optional[str] = ...) -> None: ...

class DiscoverRequest(_message.Message):
    __slots__ = ("capabilities",)
    CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    capabilities: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, capabilities: _Optional[_Iterable[str]] = ...) -> None: ...

class AgentRecord(_message.Message):
    __slots__ = ("agent_id", "capabilities", "metadata_json", "status")
    AGENT_ID_FIELD_NUMBER: _ClassVar[int]
    CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    METADATA_JSON_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    agent_id: str
    capabilities: _containers.RepeatedScalarFieldContainer[str]
    metadata_json: str
    status: str
    def __init__(self, agent_id: _Optional[str] = ..., capabilities: _Optional[_Iterable[str]] = ..., metadata_json: _Optional[str] = ..., status: _Optional[str] = ...) -> None: ...

class DiscoverResponse(_message.Message):
    __slots__ = ("agents",)
    AGENTS_FIELD_NUMBER: _ClassVar[int]
    agents: _containers.RepeatedCompositeFieldContainer[AgentRecord]
    def __init__(self, agents: _Optional[_Iterable[_Union[AgentRecord, _Mapping]]] = ...) -> None: ...

class GetStateRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class GetStateResponse(_message.Message):
    __slots__ = ("state",)
    class StateEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    STATE_FIELD_NUMBER: _ClassVar[int]
    state: _containers.ScalarMap[str, str]
    def __init__(self, state: _Optional[_Mapping[str, str]] = ...) -> None: ...

class UpdateStateRequest(_message.Message):
    __slots__ = ("updates",)
    class UpdatesEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    UPDATES_FIELD_NUMBER: _ClassVar[int]
    updates: _containers.ScalarMap[str, str]
    def __init__(self, updates: _Optional[_Mapping[str, str]] = ...) -> None: ...

class UpdateStateResponse(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ResetStateRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ResetStateResponse(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...
