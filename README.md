# MAS AI - Experimental Multi-Agent System Framework

⚠️ **EXPERIMENTAL STATUS**: This project is in early development and APIs are subject to breaking changes. Not recommended for production use.

MAS AI is a Python framework for building multi-agent systems, focusing on reliable message passing and state management between agents.

## Core SDK Features

-   **Simple Agent Creation** - Declarative agent definition with capabilities
-   **Type-Safe State Management** - Immutable state management with Pydantic models
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
from mas.sdk.state import AgentState
from pydantic import Field

# Optional: Define custom state model
class MyState(AgentState):
    counter: int = Field(default=0)
    name: str = Field(default="")

@agent(
    agent_id="example_agent",
    capabilities=["math", "storage"],
    metadata={"version": "0.1.0"},
    state_model=MyState  # Optional custom state model
)
class ExampleAgent(Agent):
    async def on_message(self, message: Message) -> None:
        # Handle incoming messages
        print(f"Got message: {message.payload}")

        # Update agent's state
        await self.update_state({
            "counter": 42,
            "name": "example"
        })

        # Access current state
        current_state = self.state
        print(f"Counter: {current_state.data['counter']}")
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

Agents maintain immutable state with type-safe updates:

```python
# Define custom state model
class MyState(AgentState):
    counter: int = Field(default=0)
    status: str = Field(default="idle")

# Update state
await agent.update_state({
    "counter": 42,
    "status": "ready"
})

# Access state
current_state = agent.state
print(f"Counter: {current_state.data['counter']}")

# Reset state to initial values
await agent.reset_state()

# Subscribe to state changes
async def on_state_change(new_state: MyState) -> None:
    print(f"State changed: {new_state.model_dump()}")

agent.subscribe_to_state(on_state_change)
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
        await self.update_state({"status": "starting"})

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
