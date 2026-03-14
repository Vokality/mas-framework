"""gRPC-mapped server errors."""

from __future__ import annotations

import grpc


class RpcError(Exception):
    """Base exception that maps to gRPC status codes."""

    def __init__(self, status: grpc.StatusCode, message: str):
        """Initialize error with gRPC status and message."""
        super().__init__(message)
        self.status = status
        self.message = message


class UnauthenticatedError(RpcError):
    """Unauthenticated gRPC error."""

    def __init__(self, message: str):
        """Initialize unauthenticated error."""
        super().__init__(grpc.StatusCode.UNAUTHENTICATED, message)


class PermissionDeniedError(RpcError):
    """Permission denied gRPC error."""

    def __init__(self, message: str):
        """Initialize permission denied error."""
        super().__init__(grpc.StatusCode.PERMISSION_DENIED, message)


class ResourceExhaustedError(RpcError):
    """Resource exhausted gRPC error."""

    def __init__(self, message: str):
        """Initialize resource exhausted error."""
        super().__init__(grpc.StatusCode.RESOURCE_EXHAUSTED, message)


class InvalidArgumentError(RpcError):
    """Invalid argument gRPC error."""

    def __init__(self, message: str):
        """Initialize invalid argument error."""
        super().__init__(grpc.StatusCode.INVALID_ARGUMENT, message)


class FailedPreconditionError(RpcError):
    """Failed precondition gRPC error."""

    def __init__(self, message: str):
        """Initialize failed precondition error."""
        super().__init__(grpc.StatusCode.FAILED_PRECONDITION, message)
