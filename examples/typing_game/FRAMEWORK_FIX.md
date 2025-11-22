# Framework Fix: Agent Handler Registry Isolation

## Problem

The `@Agent.on()` decorator in the MAS framework was registering all message handlers on the base `Agent` class, causing all agent subclasses to share the same handler registry. This led to:

1. **Handler Conflicts**: When multiple agents registered handlers for the same message type, the last registration would overwrite previous ones
2. **Wrong Agent Execution**: All agents would try to execute the most recently registered handler, causing `AttributeError` when methods from one agent class were called on instances of another agent class

### Example Error
```
AttributeError: 'AchievementAgent' object has no attribute '_notifications_enabled'
```

This occurred because `AchievementAgent` was trying to execute `ParentAgent.handle_level_complete_parent()`, which calls `self._notifications_enabled()` - a method that only exists on `ParentAgent`.

## Root Cause

The `@Agent.on()` decorator was implemented as a classmethod:

```python
@classmethod
def on(cls, message_type: str, ...) -> ...:
    def decorator(fn):
        registry = dict(getattr(cls, "_handlers", {}))  # cls is Agent base class!
        registry[message_type] = Agent._HandlerSpec(fn=fn, model=model)
        setattr(cls, "_handlers", registry)  # Sets on Agent, not subclass!
        return fn
    return decorator
```

When decorating a method in a subclass like `ParentAgent`, `cls` in the classmethod is `Agent` (the base class), not `ParentAgent`. This caused all handlers from all agent classes to be stored in `Agent._handlers`.

## Solution

### Two-Phase Handler Registration

**Phase 1: Decoration Time** - Mark decorated methods with metadata:
```python
def decorator(fn):
    # Instead of registering immediately, mark the function
    if not hasattr(fn, "_agent_handlers"):
        setattr(fn, "_agent_handlers", [])
    handler_list = getattr(fn, "_agent_handlers")
    handler_list.append((message_type, model))
    return fn
```

**Phase 2: Class Definition Time** - Register handlers when subclass is defined:
```python
def __init_subclass__(cls, **kwargs: Any) -> None:
    """Ensure each subclass gets its own handler registry."""
    super().__init_subclass__(**kwargs)
    
    # Create a new handlers dict for this subclass
    cls._handlers = {}
    
    # Register all decorated methods from this class
    for name in dir(cls):
        attr = getattr(cls, name)
        if hasattr(attr, "_agent_handlers"):
            handler_list = getattr(attr, "_agent_handlers")
            for message_type, model in handler_list:
                cls._handlers[message_type] = Agent._HandlerSpec(fn=attr, model=model)
```

### How It Works

1. **Decoration**: When `@Agent.on("message_type")` is applied, it adds metadata to the function object instead of immediately registering it
2. **Class Creation**: When Python finishes defining a subclass (e.g., `ParentAgent`), `__init_subclass__` is automatically called
3. **Registration**: `__init_subclass__` creates a fresh `_handlers` dict for the new subclass and registers all decorated methods from that class
4. **Isolation**: Each agent subclass now has its own independent handler registry

### Benefits

- ✅ **Proper Isolation**: Each agent class has its own handler registry
- ✅ **No Conflicts**: Multiple agents can handle the same message type without interfering
- ✅ **Correct Dispatch**: Each agent instance uses its own class's handlers
- ✅ **Backward Compatible**: Existing code continues to work without changes
- ✅ **Type Safe**: Added proper type hints with `type: ignore` for dynamic attributes

## Files Modified

### Framework
- `src/mas/agent.py` - Fixed `@Agent.on()` decorator and added `__init_subclass__`

### Examples (No Changes Required!)
The typing game agents work correctly now without any workarounds:
- `examples/typing_game/typing_game_agent.py` ✓
- `examples/typing_game/reward_agent.py` ✓
- `examples/typing_game/achievement_agent.py` ✓
- `examples/typing_game/parent_agent.py` ✓

## Testing

### Before Fix
```
# All agents shared Agent._handlers
Agent._handlers = {
    "letter.typed": TypingGameAgent.handle_letter,
    "word.complete": RewardAgent.reward_word_completion,  # Overwrites parent's handler!
    "level.complete": ParentAgent.handle_level_complete_parent,  # Overwrites achievement's handler!
}

# When AchievementAgent receives "level.complete":
# It tries to execute ParentAgent.handle_level_complete_parent(achievement_instance, ...)
# → AttributeError: 'AchievementAgent' object has no attribute '_notifications_enabled'
```

### After Fix
```
# Each class has its own handlers
TypingGameAgent._handlers = {
    "letter.typed": TypingGameAgent.handle_letter,
}

RewardAgent._handlers = {
    "letter.correct": RewardAgent.reward_correct_letter,
    "word.complete": RewardAgent.reward_word_completion,
    ...
}

AchievementAgent._handlers = {
    "level.complete": AchievementAgent.handle_level_complete_achievement,
    "game.complete": AchievementAgent.handle_game_complete_achievement,
}

ParentAgent._handlers = {
    "level.complete": ParentAgent.handle_level_complete_parent,
    "achievement.unlocked": ParentAgent.handle_achievement,
    ...
}

# Each agent executes only its own handlers ✓
```

## Impact

### For Framework Users
- **No code changes required** - Existing agents continue to work
- **Better reliability** - No more handler conflicts
- **Clearer errors** - Stack traces show the correct agent class

### For Framework Developers
- **Proper class isolation** - Each subclass maintains its own state
- **Standard Python pattern** - Uses `__init_subclass__` as intended
- **Maintainable** - Clear separation of concerns

## Technical Notes

### Why `__init_subclass__`?

`__init_subclass__` is a Python magic method called automatically when a class is subclassed. It's the perfect place to set up class-level state (like handler registries) for each subclass.

### Type Hints

Added `type: ignore[misc]` for dynamic attribute assignments since:
- `_handlers` is not declared in the type stub
- Handler functions are attached dynamically to function objects
- This is a valid use of dynamic attributes in Python

### Qualname Inspection

The decorator checks `fn.__qualname__` to determine if a function is a method (e.g., `"ParentAgent.handle_level_complete_parent"`) vs a standalone function. This allows the decorator to handle both cases.

## Lessons Learned

1. **Classmethods and Decorators**: Be careful when using classmethods as decorators - the `cls` parameter refers to where the decorator is defined, not where it's used
2. **Shared Mutable State**: Class-level dictionaries are shared by all subclasses unless explicitly copied
3. **`__init_subclass__`**: The right tool for per-subclass initialization
4. **Test Multi-Agent Scenarios**: Bugs like this only appear when multiple agents interact

## Future Improvements

Consider adding:
- Validation to detect duplicate handler registrations
- Debug mode to log handler registration
- Documentation of the two-phase registration process
- Unit tests specifically for handler isolation

This fix ensures the MAS framework properly supports multiple independent agent classes! 🎉

