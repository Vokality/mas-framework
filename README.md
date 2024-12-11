# MAS AI - Experimental Multi-Agent System Framework

⚠️ **EXPERIMENTAL STATUS**: This project is in early development and APIs are subject to breaking changes. Not recommended for production use.

MAS AI is a Python framework for building multi-agent systems, focusing on reliable message passing and state management between agents.

## Core SDK Features

-   **Simple Agent Creation** - Declarative agent definition with capabilities
-   **Type-Safe State Management** - Local and global state with Pydantic models
-   **Message Routing** - Reliable agent-to-agent communication
-   **Agent Discovery** - Find agents by capabilities
-   **Lifecycle Management** - Controlled agent startup/shutdown

## Quick Start

1. Prerequisites:

```bash
# Required: Redis server for message transport
redis-server

# Install package
pip install mas-framework
```

2. Create an Agent:

```python
from mas.sdk.agent import Agent
from mas.sdk.decorators import agent
from mas.protocol import Message

@agent(
    agent_id="example_agent",
    capabilities=["math", "storage"],
    metadata={"version": "0.1.0"}
)
class ExampleAgent(Agent):
    async def on_message(self, message: Message) -> None:
        # Handle incoming messages
        print(f"Got message: {message.payload}")

        # Update agent's local state
        await self.update_local_state({
            "last_message": message.payload
        })
```

3. Run the Agent:

```python
import asyncio
from mas import mas_service

async def main():
    async with mas_service() as context:
        agent = await ExampleAgent.build(context)
        try:
            while True:
                await asyncio.sleep(1)
        finally:
            await agent.stop()

if __name__ == "__main__":
    asyncio.run(main())
```

## Key SDK Components

### State Management

Agents maintain both local and shared state:

```python
# Local state updates
await agent.update_local_state({
    "counter": 42,
    "status": "ready"
})

# Global shared state
await agent.update_global_state({
    "shared_resource": "value"
})
```

Custom state models with validation:

```python
from mas.sdk.state import BaseStateModel
from pydantic import Field

class AgentState(BaseStateModel):
    counter: int = Field(default=0)
    messages: List[str] = Field(default_factory=list)
```

### Message Handling

Pattern matching for message types:

```python
async def on_message(self, message: Message) -> None:
    match message.message_type:
        case MessageType.AGENT_MESSAGE:
            await self.handle_agent_message(message)
        case MessageType.DISCOVERY_RESPONSE:
            await self.handle_discovery(message)
```

### Agent Discovery

Find other agents by capabilities:

```python
# Find agents with specific capabilities
await agent.runtime.discover_agents(capabilities=["math"])
```

### Lifecycle Hooks

```python
class MyAgent(Agent):
    async def on_start(self) -> None:
        """Called when agent starts"""
        await self.update_local_state({"status": "starting"})

    async def on_stop(self) -> None:
        """Called when agent stops"""
        await self.cleanup_resources()
```

## Current Limitations

As this is experimental software, there are several limitations:

-   No authentication/authorization system yet
-   Limited error recovery mechanisms
-   Message delivery is not guaranteed
-   No persistent storage (in-memory only)
-   APIs may change without notice
-   Limited testing in distributed environments
-   No proper documentation yet

## Development Status

This project is under active development. Current focus areas:

-   Stabilizing core APIs
-   Improving error handling
-   Adding authentication
-   Adding persistent storage
-   Documentation
-   Testing infrastructure

## Contributing

This project is in experimental phase and we welcome feedback and contributions:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests: `pytest`
5. Submit a pull request

## License

MIT License - see LICENSE file for details
