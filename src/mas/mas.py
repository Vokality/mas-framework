import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Optional, Type

from mas.logger import get_logger
from mas.persistence.interfaces import IPersistenceProvider
from mas.persistence.memory import InMemoryProvider
from mas.protocol import AgentStatus, Message, MessageType
from mas.transport.redis import RedisTransport
from mas.transport.service import TransportService

from .discovery.service import DiscoveryService

logger = get_logger()


class MAS:
    """Multi-Agent System (MAS) service."""

    def __init__(
        self,
        transport: TransportService,
        persistence: IPersistenceProvider,
    ) -> None:
        self._loop = asyncio.get_running_loop()
        self._tasks: List[asyncio.Task] = []
        self._channel: str = "core"
        self._running: bool = False
        self._transport: TransportService = transport
        self._discovery: DiscoveryService = DiscoveryService(persistence)
        self._persistence: IPersistenceProvider = persistence
        self._message_handler: Optional[asyncio.Task] = None
        self._handlers: Dict[MessageType, Any] = {
            MessageType.STATUS_UPDATE: self._handle_status_update,
            MessageType.DISCOVERY_REQUEST: self._handle_discovery,
            MessageType.REGISTRATION_REQUEST: self._handle_registration,
            MessageType.HEALTH_CHECK_RESPONSE: self._handle_health_check,
        }

    async def start(self) -> None:
        """Start MAS."""
        if self._running:
            return

        logger.info("Starting MAS...")
        try:
            # Initialize services in order
            await self._persistence.initialize()
            await self._transport.start()
            await self._discovery.initialize()
            self._running = True
            self._message_handler = self._loop.create_task(
                self._message_stream(),
                name="core_message_handler",
            )
            self._tasks.append(
                self._loop.create_task(
                    self._perform_health_checks(),
                    name="core_perform_health_checks",
                )
            )
            await asyncio.sleep(0)
            logger.info("MAS started successfully")

        except Exception as e:
            logger.error(f"Failed to start MAS: {e}")
            await self.stop()  # Clean up any partially initialized services
            raise

    async def stop(self) -> None:
        """Stop MAS in a controlled manner."""
        if not self._running:
            return

        logger.info("Stopping MAS...")
        self._running = False

        try:
            # Cancel message handler
            if self._message_handler:
                logger.info("Stopping message handler...")
                self._message_handler.cancel()
                try:
                    await self._message_handler
                except asyncio.CancelledError:
                    pass

            for task in self._tasks:
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)

            # Cleanup services in reverse order
            await self._discovery.cleanup()
            await self._persistence.cleanup()
            await self._transport.stop()

            logger.info("MAS stopped successfully")

        except Exception as e:
            logger.error(f"Error during MAS shutdown: {e}")
            raise

    async def _message_stream(self) -> None:
        """Handle incoming core messages."""
        if not self._running:
            logger.error("Attempted to start message stream but MAS is not running.")
            return
        try:
            # Subscribe to the core channel for all core-targeted messages
            async with self._transport.message_stream(
                channel=self._channel,
                subscriber_id=self._channel,
            ) as stream:
                logger.info("Received message stream...")
                async for message in stream:  # type: ignore
                    if message.target_id != self._channel:
                        continue
                    try:
                        await self._process_message(message)
                    except Exception as e:
                        logger.error(f"Error processing message: {e}")
                        await self._send_error_response(message, str(e))
        except Exception as e:
            if self._running:
                logger.error(f"MAS message handler failed: {e}")

    async def _process_message(self, message: Message) -> None:
        """Process an incoming message."""
        if handler := self._handlers.get(message.message_type):
            try:
                await handler(message)
            except Exception as e:
                logger.error(
                    f"Handler failed for message type {message.message_type}: {e}"
                )
                await self._send_error_response(message, str(e))
        else:
            logger.warning(f"No handler for message type: {message.message_type}")

    async def _send_error_response(self, original_message: Message, error: str) -> None:
        """Send error response for a failed message."""
        try:
            await self._transport.send_message(
                Message(
                    sender_id=self._channel,
                    target_id=original_message.sender_id,
                    message_type=MessageType.ERROR,
                    payload={
                        "error": error,
                        "original_message_type": original_message.message_type,
                    },
                )
            )
        except Exception as e:
            logger.error(f"Failed to send error response: {e}")

    async def _handle_registration(self, message: Message) -> None:
        """Handle agent registration."""
        try:
            token = await self._discovery.register_agent(
                agent_id=message.sender_id,
                capabilities=set(message.payload["capabilities"]),
            )
            await self._send_success_response(
                message,
                MessageType.REGISTRATION_RESPONSE,
                {"token": token},
            )
        except Exception as e:
            await self._send_error_response(message, str(e))

    async def _handle_discovery(self, message: Message) -> None:
        """Handle agent discovery request."""
        try:
            capabilities = set(message.payload.get("capabilities", []))
            agents = await self._discovery.find_agents(capabilities)

            await self._send_success_response(
                message,
                MessageType.DISCOVERY_RESPONSE,
                {"agents": agents},
            )
        except Exception as e:
            logger.error(f"Failed to send discovery response: {e}", exc_info=True)
            await self._send_error_response(message, str(e))

    async def _handle_status_update(self, message: Message) -> None:
        """Handle agent status update."""
        try:
            status = message.payload["status"]
            await self._discovery.update_status(
                status=status,
                agent_id=message.sender_id,
            )
            if status == AgentStatus.INACTIVE.value:
                await self._discovery.deregister_agent(agent_id=message.sender_id)
        except Exception as e:
            await self._send_error_response(message, str(e))

    async def _handle_health_check(self, message: Message) -> None:
        """Handle agent health check."""
        try:
            status = message.payload["status"]
            await self._discovery.update_status(
                status=status,
                agent_id=message.sender_id,
            )
        except Exception as e:
            logger.error(f"Failed to process health check response: {e}", exc_info=True)

    async def _send_success_response(
        self,
        original_message: Message,
        response_type: MessageType,
        payload: Dict[str, Any],
    ) -> None:
        """Send a success response."""
        payload["status"] = "success"
        await self._transport.send_message(
            Message(
                payload=payload,
                sender_id=self._channel,
                target_id=original_message.sender_id,
                message_type=response_type,
            )
        )

    async def _perform_health_checks(self):
        while self._running:
            agents = await self._discovery.find_agents()
            for agent in agents:
                await self._transport.send_message(
                    Message(
                        sender_id=self._channel,
                        target_id=agent.id,
                        message_type=MessageType.HEALTH_CHECK,
                        payload={},
                    )
                )
            await asyncio.sleep(10)


@dataclass(frozen=True)
class MASContext:
    mas: MAS
    transport: TransportService
    persistence: IPersistenceProvider


@asynccontextmanager
async def mas_service(
    provider: Type[IPersistenceProvider] = InMemoryProvider,
) -> AsyncIterator[MASContext]:
    """Run the MAS service until the shutdown signal is received or context exits."""

    # Create all our services
    transport = TransportService(transport=RedisTransport())
    storage = provider()
    mas = MAS(transport, storage)

    # Set up signal handling
    try:
        # Start the service
        await mas.start()

        # Give control back to the caller until they're done
        # or until a shutdown signal is received
        yield MASContext(mas, transport, storage)
    finally:
        # Always stop the service, whether we got a signal
        # or the caller finished naturally
        await mas.stop()
