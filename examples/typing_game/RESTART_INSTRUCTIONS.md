# How to Restart the Game System

## The Problem
The game system is running with OLD code that:
- Sends messages to wrong agent IDs (`reward_agent` instead of `child_alex_rewards`)
- Doesn't have logging configured
- Points won't update

## The Solution: Restart Everything

### Step 1: Stop the Current Game System

Find the process and kill it:
```bash
# Find the process
ps aux | grep "main.py" | grep -v grep

# Kill it (replace PID with actual process ID)
kill <PID>

# Or kill all Python processes running main.py
pkill -f "python.*main.py"
```

### Step 2: Restart the Game System

**Option A: Use simple_typing.py (Recommended)**
```bash
cd examples/typing_game
uv run python simple_typing.py
```

This will:
- Detect the old system is gone
- Start fresh with new code
- Configure logging
- Use correct agent IDs

**Option B: Use run.sh**
```bash
cd examples/typing_game
./run.sh
```

### Step 3: Verify It's Working

After restart, check the log file:
```bash
tail -f examples/typing_game/typing_game.log
```

You should see:
- Game system starting
- Agents starting
- Messages being sent/received

Then type a letter and you should see:
```
- Sending letter.correct to child_alex_rewards
- Reward agent received letter.correct
- Awarding X points
```

### Step 4: Check Points

```bash
redis-cli HGET "agent.state:child_alex_rewards" "total_points"
```

Should show increasing points after typing letters!

