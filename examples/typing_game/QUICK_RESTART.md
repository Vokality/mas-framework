# Quick Restart Guide

## Step 1: Kill Old System
```bash
pkill -f "python.*main.py"
```

## Step 2: Wait a moment
```bash
sleep 3
```

## Step 3: Start Fresh
```bash
cd examples/typing_game
uv run python simple_typing.py
```

This will:
- Detect no game system running
- Start fresh with NEW code (correct agent IDs + logging)
- Write logs to `typing_game.log`

## Step 4: Watch Logs
In another terminal:
```bash
tail -f examples/typing_game/typing_game.log
```

## Step 5: Type a Letter
You should see in the log:
```
- Sending letter.correct to child_alex_rewards
- Reward agent received letter.correct
- Awarding 10 points
```

And points should update! 🎉

