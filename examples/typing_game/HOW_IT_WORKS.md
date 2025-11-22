# How the Typing Game Works

## The Complete Flow

### 1. **Child Types a Letter**

```
Child types "c" → simple_typing.py → sends message
```

**What happens:**
```python
# In simple_typing.py
await client.send("child_alex", "letter.typed", {
    "letter": "c",
    "timestamp": time.time()
})
```

### 2. **Message Goes Through Gateway**

```
simple_typing.py → Gateway → Redis Stream → Game Agent
```

**Gateway does:**
- ✅ Authenticates the sender (checks token)
- ✅ Authorizes (checks if UI client can message game agent)
- ✅ Rate limits (prevents spam)
- ✅ Routes to game agent's stream

### 3. **Game Agent Receives Letter**

```python
# In typing_game_agent.py
@Agent.on("letter.typed", model=LetterInput)
async def handle_letter(self, msg: AgentMessage, payload: LetterInput):
    # payload.letter = "c"
    # Check if it matches expected letter
    expected = self.state.current_word[self.state.current_letter_index]  # "a"
    typed = payload.letter.lower()  # "c"
    
    if typed == expected:
        # CORRECT! ✅
        self.state.current_letter_index += 1  # Move to next letter
        await self.send("reward_agent", "letter.correct", {...})
    else:
        # Wrong, try again
        await self.send("reward_agent", "letter.incorrect", {...})
```

### 4. **Reward Agent Awards Points**

```python
# In reward_agent.py
@Agent.on("letter.correct")
async def reward_correct_letter(self, msg: AgentMessage, payload: dict):
    points = 10  # Base points
    streak = payload.get("streak", 0)
    
    # Streak bonus: every 5 letters = 2x multiplier
    if streak % 5 == 0:
        points *= 2
    
    self.state.total_points += points
    await self.update_state({"total_points": self.state.total_points})
```

### 5. **Word Completion**

When child types all letters correctly:

```python
# In typing_game_agent.py
async def complete_word(self):
    # Calculate stars (1-3 based on accuracy and speed)
    if accuracy >= 95 and time < 5 seconds:
        stars = 3  # Perfect!
    elif accuracy >= 80:
        stars = 2
    else:
        stars = 1
    
    # Send to reward agent
    await self.send("reward_agent", "word.complete", {
        "stars": stars,
        "points": 50 + bonuses
    })
    
    # Load next word
    self.state.current_word = next_word
```

### 6. **Achievement Agent Checks Milestones**

```python
# In achievement_agent.py
@Agent.on("check.achievements")
async def check_achievements(self, msg: AgentMessage, payload: dict):
    # Check if any achievements unlocked
    if words_completed >= 10:
        await self.unlock_achievement("ten_words", {...})
    
    if best_streak >= 5:
        await self.unlock_achievement("streak_5", {...})
```

### 7. **Parent Agent Gets Notified**

```python
# In parent_agent.py
@Agent.on("level.complete")
async def handle_level_complete(self, msg: AgentMessage, payload: dict):
    # Send notification to parent
    await self.show_notification("Level Complete! 🎉", ...)
    
    # Send encouragement to child
    await self.send(child_id, "encouragement.message", {
        "message": "Great job! ⭐"
    })
```

## Complete Example: Typing "cat"

### Step 1: Child types "c"
```
UI → Gateway → Game Agent
Game Agent: "c" matches "c" ✅
Game Agent → Reward Agent: "letter.correct" (+10 points)
State: current_letter_index = 1
```

### Step 2: Child types "a"
```
UI → Gateway → Game Agent
Game Agent: "a" matches "a" ✅
Game Agent → Reward Agent: "letter.correct" (+10 points)
State: current_letter_index = 2
```

### Step 3: Child types "t"
```
UI → Gateway → Game Agent
Game Agent: "t" matches "t" ✅
Word complete! 🎉
Game Agent → Reward Agent: "word.complete" (+50 points, 2 stars)
Game Agent → Achievement Agent: "check.achievements"
Game Agent → Parent Agent: "word.complete"
State: words_completed = 1, current_word = "dog"
```

## State Persistence

**Everything is saved to Redis automatically:**

```redis
agent.state:child_alex
  current_word: "cat"
  current_letter_index: 2
  words_completed: 5
  correct_letters: 23
  best_streak: 8

agent.state:child_alex_rewards
  total_points: 450
  stars_earned: 12
  daily_streak: 3
```

**If you restart the game:**
- All progress is restored
- Points, stars, achievements persist
- Child continues where they left off

## Agent Communication Flow

```
┌─────────────┐
│   Child UI  │ Types "c"
└──────┬──────┘
       │ send("letter.typed", {"letter": "c"})
       ↓
┌─────────────┐
│   Gateway   │ Auth → Authz → Rate Limit → Route
└──────┬──────┘
       │
       ↓
┌─────────────┐
│ Game Agent  │ Checks if "c" == expected letter
└──────┬──────┘
       │
       ├─→ Correct? → Reward Agent (+10 points)
       │
       ├─→ Word complete? → Reward Agent (+50, stars)
       │                    → Achievement Agent (check milestones)
       │                    → Parent Agent (notify)
       │
       └─→ Update state in Redis (auto-persisted)
```

## Key Concepts

### 1. **Message-Driven Architecture**
- Everything happens via messages
- Agents don't call each other directly
- Gateway routes all messages

### 2. **State Persistence**
- All state saved to Redis automatically
- Survives restarts
- No manual save/load needed

### 3. **Agent Responsibilities**

**Game Agent:**
- Tracks current word/letter
- Validates typed letters
- Manages level progression

**Reward Agent:**
- Awards points/stars/gems
- Tracks daily streaks
- Manages weekly goals

**Achievement Agent:**
- Checks milestone criteria
- Unlocks achievements
- Awards achievement points

**Parent Agent:**
- Monitors progress
- Sends notifications
- Provides encouragement

### 4. **Decorator Handlers**

```python
@Agent.on("letter.typed", model=LetterInput)
async def handle_letter(self, msg: AgentMessage, payload: LetterInput):
    # This function is called automatically when
    # a "letter.typed" message arrives
    # payload is validated LetterInput model
```

The `@Agent.on()` decorator registers handlers. When a message arrives, MAS automatically:
1. Validates the payload against the model
2. Calls the handler function
3. Passes the validated payload

## Why This Architecture?

**Benefits:**
- ✅ **Scalable**: Add new agents without changing existing code
- ✅ **Reliable**: Gateway ensures messages are delivered
- ✅ **Persistent**: State survives crashes/restarts
- ✅ **Observable**: All messages logged/audited
- ✅ **Secure**: Gateway enforces authorization

**Example: Adding a sound agent:**
```python
class SoundAgent(Agent):
    @Agent.on("letter.correct")
    async def play_success_sound(self, msg, payload):
        # Play happy chime
        pass
```

Just create it and it automatically receives all "letter.correct" messages!

