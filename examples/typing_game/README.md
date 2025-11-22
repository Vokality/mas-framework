# Typing Game for 4-Year-Olds

A fun, reward-driven typing game built with MAS Framework that incentivizes learning to type through immediate feedback, achievements, and parent involvement.

## Quick Start

### Prerequisites

1. **Redis** - Running locally on port 6379
   ```bash
   # macOS with Homebrew
   brew install redis
   brew services start redis
   
   # Or with Docker
   docker run -d -p 6379:6379 redis:latest
   ```

2. **Python 3.14+** with `uv` package manager
   ```bash
   # Install uv if needed
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

### Running the Game

From the `examples/typing_game` directory:

```bash
# Quick start with the run script (recommended)
./run.sh

# Or manually with uv
uv run python main.py

# Or from the project root
uv run python -m examples.typing_game
```

### Environment Variables (Optional)

```bash
# Customize child/parent IDs
export CHILD_ID="child_alex"
export PARENT_ID="parent"

# Custom Redis URL
export REDIS_URL="redis://localhost:6379"
```

## Architecture

```
Child (Tablet)          Parent (Phone)
    │                        │
    ├─ TypingGameAgent ──────┼─ ParentAgent
    ├─ RewardAgent           │
    ├─ ProgressAgent         │
    └─ AchievementAgent      │
```

## Reward System Design

### Immediate Rewards (Per Keystroke)
- ✅ Visual feedback (green checkmark, sparkles)
- ✅ Sound effects (happy chime for correct)
- ✅ Character animation (character celebrates)
- ✅ Points earned (shown immediately)

### Short-Term Rewards (Per Word/Level)
- ⭐ Stars (1-3 stars based on accuracy)
- 🎈 Balloon animation
- 🎵 Victory music
- 💎 Gems/coins earned

### Medium-Term Rewards (Milestones)
- 🏆 Unlock new characters
- 🎨 Unlock new themes
- 📚 Unlock new word sets
- 🎪 Unlock mini-games

### Long-Term Rewards (Achievements)
- 📅 Daily practice streak
- 🎯 Weekly goals
- 🏅 Skill badges
- 📊 Progress reports to parent

### Parent Involvement
- 📱 Real-time progress notifications
- 🎉 Celebration alerts when milestones reached
- 📈 Weekly progress reports
- 💬 Encouragement messages

## Game Progression

1. **Level 1: Single Letters** (a-z)
   - Reward: 1 star per letter
   - Unlock: Character selection

2. **Level 2: Simple Words** (cat, dog, sun)
   - Reward: 2 stars per word
   - Unlock: New theme

3. **Level 3: Common Words** (the, and, is)
   - Reward: 3 stars per word
   - Unlock: Mini-games

4. **Level 4: Short Sentences** (I see a cat)
   - Reward: Bonus gems
   - Unlock: Achievement badges

## Points System

- **Correct letter**: 10 points
- **Correct word**: 50 points + bonus for speed
- **Perfect word** (no mistakes): 100 points
- **Daily streak**: 2x multiplier
- **Weekly goal**: 3x multiplier

## Reward Store

Points can be spent on:
- 🎨 New character skins (500 points)
- 🌈 New themes (300 points)
- 🎮 Mini-games (1000 points)
- 🎁 Surprise boxes (200 points)

## Features

- ✅ Adaptive difficulty (adjusts to child's skill)
- ✅ Progress persistence (saves across sessions)
- ✅ Parent dashboard (monitor progress)
- ✅ Safe environment (gateway security)
- ✅ Multi-device support (parent + child)
- ✅ Learning analytics (track improvement)

## How the Child Types

### Textual Interface (Starts Everything)

```bash
# Just run this - it starts everything needed!
cd examples/typing_game
uv run python simple_typing.py
```

**That's it!** The script will:
1. Check if the game system is running
2. Start it if needed (MAS Service, Gateway, all agents)
3. Launch the Textual UI
4. Let the child type immediately with live feedback

The child types one letter at a time and presses Enter. The interface shows:
- Current word to type with highlighted letter
- Which letter to type next
- Points and stars earned
- Connection status and helpful guidance

### Option 2: Launch the UI Only (Game Already Running)

```bash
# Install textual first if you haven't already
uv pip install textual

# Start the typing interface (assumes the game system is running elsewhere)
cd examples/typing_game
uv run python ui.py
```

This connects to the existing game system without trying to start services.

### Option 3: Manual Start (Advanced)

If you want to run the game system separately:

```bash
# Terminal 1: Start game system
cd examples/typing_game
./run.sh

# Terminal 2: Start typing interface
cd examples/typing_game
uv run python simple_typing.py
```

### Option 4: Build Your Own UI

The game agents listen for `letter.typed` messages. You can build any interface that:
1. Connects to Redis
2. Sends messages to the `child_alex` agent
3. Receives updates from reward/achievement agents

See `HOW_TO_RUN.md` for examples.

