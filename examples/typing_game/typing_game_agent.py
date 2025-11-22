"""Main typing game agent."""

import logging
import time

from mas import Agent, AgentMessage
from pydantic import BaseModel

logger = logging.getLogger(__name__)

try:
    from .typing_game_state import TypingGameState
except ImportError:
    from typing_game_state import TypingGameState


class LetterInput(BaseModel):
    """Single letter input from child."""

    letter: str
    timestamp: float


class WordComplete(BaseModel):
    """Word completion data."""

    word: str
    time_seconds: float
    mistakes: int
    accuracy: float


class TypingGameAgent(Agent[TypingGameState]):
    """Main typing game agent - handles game logic."""

    # Word lists by level
    LEVEL_WORDS = {
        1: ["a", "b", "c", "d", "e"],  # Single letters
        2: ["cat", "dog", "sun", "hat", "car"],  # Simple words
        3: ["the", "and", "is", "it", "to"],  # Common words
        4: ["I see a cat", "The dog runs", "Sun is bright"],  # Sentences
    }

    def __init__(self, child_id: str, **kwargs):
        super().__init__(
            child_id,
            capabilities=["typing_game"],
            state_model=TypingGameState,
            **kwargs,
        )
        self.child_id = child_id
        self.reward_agent_id = f"{child_id}_rewards"
        self.achievement_agent_id = f"{child_id}_achievements"
        self.current_word_list = []
        self.word_start_time = None

        # Debug: Check if handlers are registered
        handlers = getattr(self.__class__, "_handlers", {})
        logger.info(
            f"TypingGameAgent initialized with {len(handlers)} handlers: {list(handlers.keys())}"
        )

    async def on_start(self):
        """Initialize game when agent starts."""
        # Load or initialize game state
        if self.state.words_completed == 0:
            # First time - start with level 1
            await self.start_level(1)
        else:
            # Resume from saved state
            await self.load_current_word()

        # Notify parent that game started
        await self.notify_parent(
            "game.started",
            {"level": self.state.current_level, "progress": self.state.words_completed},
        )

    async def start_level(self, level: int):
        """Start a new level."""
        self.state.current_level = level
        self.current_word_list = self.LEVEL_WORDS.get(level, [])

        if self.current_word_list:
            self.state.current_word = self.current_word_list[0]
            self.state.current_letter_index = 0

        await self.update_state(
            {
                "current_level": level,
                "current_word": self.state.current_word,
                "current_letter_index": 0,
            }
        )

        # Notify reward agent
        await self.send(self.reward_agent_id, "level.started", {"level": level})

    async def load_current_word(self):
        """Load current word from state."""
        self.current_word_list = self.LEVEL_WORDS.get(self.state.current_level, [])
        self.word_start_time = time.time()

    @Agent.on("letter.typed", model=LetterInput)
    async def handle_letter(self, msg: AgentMessage, payload: LetterInput):
        """Handle a single letter typed by child."""
        expected_letter = self.state.current_word[
            self.state.current_letter_index
        ].lower()
        typed_letter = payload.letter.lower()

        is_correct = typed_letter == expected_letter
        logger.info(
            f"Letter typed: '{typed_letter}' (expected: '{expected_letter}', word: '{self.state.current_word}', index: {self.state.current_letter_index}, correct: {is_correct})"
        )

        # Update statistics
        self.state.letters_typed += 1
        if is_correct:
            self.state.correct_letters += 1
            self.state.current_streak += 1
            self.state.current_letter_index += 1

            # Check if word is complete
            if self.state.current_letter_index >= len(self.state.current_word):
                await self.complete_word()
            else:
                # Immediate reward for correct letter
                logger.info(
                    f"Sending letter.correct to {self.reward_agent_id}, streak={self.state.current_streak}"
                )
                await self.send(
                    self.reward_agent_id,
                    "letter.correct",
                    {
                        "letter": typed_letter,
                        "points": 10,
                        "streak": self.state.current_streak,
                    },
                )
        else:
            self.state.incorrect_letters += 1
            self.state.current_streak = 0

            # Gentle feedback for incorrect
            await self.send(
                self.reward_agent_id,
                "letter.incorrect",
                {"expected": expected_letter, "typed": typed_letter},
            )

        # Update best streak
        if self.state.current_streak > self.state.best_streak:
            self.state.best_streak = self.state.current_streak

        await self.update_state(
            {
                "letters_typed": self.state.letters_typed,
                "correct_letters": self.state.correct_letters,
                "incorrect_letters": self.state.incorrect_letters,
                "current_letter_index": self.state.current_letter_index,
                "current_streak": self.state.current_streak,
                "best_streak": self.state.best_streak,
            }
        )

    async def complete_word(self):
        """Handle word completion."""
        if self.word_start_time is None:
            self.word_start_time = time.time()

        time_taken = time.time() - self.word_start_time
        word = self.state.current_word
        mistakes = self.state.incorrect_letters
        accuracy = self.state.correct_letters / max(self.state.letters_typed, 1) * 100

        # Update progress
        self.state.words_completed += 1
        logger.info(
            f"Word complete: '{word}' (words_completed={self.state.words_completed}, word_list_size={len(self.current_word_list)})"
        )

        # Calculate stars (1-3 based on accuracy and speed)
        if accuracy >= 95 and time_taken < 5.0:
            stars = 3
        elif accuracy >= 80:
            stars = 2
        else:
            stars = 1

        # Send completion to reward agent
        await self.send(
            self.reward_agent_id,
            "word.complete",
            {
                "word": word,
                "time_seconds": time_taken,
                "mistakes": mistakes,
                "accuracy": accuracy,
                "stars": stars,
                "level": self.state.current_level,
            },
        )

        # Check for level completion
        words_in_level = len(self.current_word_list)
        logger.info(
            f"Checking level completion: words_completed={self.state.words_completed}, words_in_level={words_in_level}"
        )
        if self.state.words_completed % words_in_level == 0:
            logger.info("Level complete! Moving to next level")
            await self.complete_level()
        else:
            # Load next word
            word_index = self.state.words_completed % words_in_level
            next_word = self.current_word_list[word_index]
            logger.info(
                f"Loading next word: index={word_index}, word='{next_word}', word_list={self.current_word_list}"
            )
            self.state.current_word = next_word
            self.state.current_letter_index = 0
            self.word_start_time = time.time()

            await self.update_state(
                {
                    "current_word": self.state.current_word,
                    "current_letter_index": 0,
                    "words_completed": self.state.words_completed,
                }
            )

    async def complete_level(self):
        """Handle level completion."""
        # Notify achievement agent
        await self.send(
            self.achievement_agent_id,
            "level.complete",
            {"level": self.state.current_level},
        )

        # Unlock next level
        next_level = self.state.current_level + 1
        if next_level <= len(self.LEVEL_WORDS):
            await self.start_level(next_level)
        else:
            # All levels complete!
            await self.send(self.achievement_agent_id, "game.complete", {})
            await self.notify_parent("all_levels.complete", {})

    async def notify_parent(self, event_type: str, data: dict):
        """Notify parent agent of events."""
        try:
            parents = await self.discover(capabilities=["parent"])
            if parents:
                await self.send(parents[0]["id"], event_type, data)
        except Exception:
            # Parent might not be connected, that's okay
            pass
