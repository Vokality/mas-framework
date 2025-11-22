# How to Run the Typing Game

## Quick Start

### 1. Start Redis

```bash
# macOS
brew services start redis

# Or Docker
docker run -d -p 6379:6379 redis:latest

# Verify it's running
redis-cli ping
# Should return: PONG
```

### 2. Run the Game System

```bash
cd examples/typing_game
./run.sh
```

This starts:
- ✅ MAS Service (agent registry)
- ✅ Gateway Service (security & routing)
- ✅ Game Agent (typing game logic)
- ✅ Reward Agent (points, stars, gems)
- ✅ Achievement Agent (milestones)
- ✅ Parent Agent (monitoring & encouragement)

### 3. Interact with the Game

The easiest way to interact is through the built-in Textual TUI:

```bash
cd examples/typing_game
uv run python simple_typing.py
```

This command:
1. Checks if the game system is already running
2. Starts MAS Service, Gateway, and all agents if needed
3. Launches the Textual UI with highlighted words, live stats, and connection status

Press `q` inside the UI to exit. If you started the services with this command, they will shut down automatically.

#### Option B: Use Python Script to Send Messages

Create a simple test script:

```python
# test_game.py
import asyncio
from mas import Agent

async def test():
    agent = Agent("test_client", capabilities=["typing_game"])
    await agent.start()
    
    # Send a letter
    await agent.send("child_alex", "letter.typed", {
        "letter": "c",
        "timestamp": time.time()
    })
    
    await agent.stop()

asyncio.run(test())
```

#### Option C: Build a Web/Mobile Frontend

The agents communicate via Redis, so you can build any frontend that:
1. Connects to Redis
2. Sends messages to agent streams
3. Receives messages from agent streams

## Testing the System

### Send a Letter

```python
import asyncio
import time
from mas import Agent

async def send_letter():
    client = Agent("test_client", redis_url="redis://localhost:6379", use_gateway=True)
    await client.start()
    
    # Send letter 'c'
    await client.send("child_alex", "letter.typed", {
        "letter": "c",
        "timestamp": time.time()
    })
    
    await asyncio.sleep(1)
    await client.stop()

asyncio.run(send_letter())
```

### Check Rewards

```python
# Query reward state via Redis
import redis
r = redis.Redis(decode_responses=True)
state = r.hgetall("agent.state:child_alex_rewards")
print(f"Total points: {state.get('total_points', 0)}")
print(f"Stars earned: {state.get('stars_earned', 0)}")
```

## Architecture Overview

```
┌─────────────────┐
│   UI/Frontend   │  (You build this)
│  (Textual/Web)  │
└────────┬────────┘
         │ Messages
         ↓
┌─────────────────┐
│  Gateway Service│  (Security, routing)
└────────┬────────┘
         │
         ↓
┌─────────────────┐
│  Game Agents    │  (Backend logic)
│  - Game Agent   │
│  - Reward Agent │
│  - Achievement  │
│  - Parent Agent │
└─────────────────┘
         │
         ↓
┌─────────────────┐
│     Redis       │  (State & messaging)
└─────────────────┘
```

## Next Steps

1. **Build a UI** - Use Textual for a terminal UI, or build a web/mobile app
2. **Customize Rewards** - Adjust point values, unlock conditions
3. **Add More Levels** - Expand word lists and difficulty
4. **Parent Dashboard** - Build a parent monitoring interface

## Troubleshooting

**"Redis connection refused"**
- Make sure Redis is running: `redis-cli ping`

**"Agent not found"**
- Make sure the game system is running (`./run.sh`)
- Check agent IDs match (default: `child_alex`)

**"Gateway authentication failed"**
- Make sure Gateway Service is started
- Agents need to register before sending messages

## Example: Complete Typing Session

```python
import asyncio
import time
from mas import Agent

async def play_game():
    # Connect as client
    client = Agent("player", redis_url="redis://localhost:6379", use_gateway=True)
    await client.start()
    
    # Type the word "cat"
    word = "cat"
    for letter in word:
        await client.send("child_alex", "letter.typed", {
            "letter": letter,
            "timestamp": time.time()
        })
        await asyncio.sleep(0.5)  # Simulate typing delay
    
    await asyncio.sleep(2)  # Wait for processing
    await client.stop()

asyncio.run(play_game())
```

This will:
1. Send letters 'c', 'a', 't' to the game agent
2. Game agent processes each letter
3. Reward agent awards points
4. Achievement agent checks for milestones
5. Parent agent receives notifications

