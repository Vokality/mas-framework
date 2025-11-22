"""State models for typing game."""

from pydantic import BaseModel, Field
from typing import Literal
from datetime import datetime


class TypingGameState(BaseModel):
    """Main game state."""

    current_level: int = 1
    current_word: str = ""
    current_letter_index: int = 0
    words_completed: int = 0
    letters_typed: int = 0
    correct_letters: int = 0
    incorrect_letters: int = 0
    current_streak: int = 0
    best_streak: int = 0
    session_start_time: float = Field(
        default_factory=lambda: datetime.now().timestamp()
    )
    unlocked_characters: list[str] = Field(default_factory=lambda: ["default"])
    unlocked_themes: list[str] = Field(default_factory=lambda: ["default"])
    current_character: str = "default"
    current_theme: str = "default"


class RewardState(BaseModel):
    """Reward system state."""

    total_points: int = 0
    stars_earned: int = 0
    gems_earned: int = 0
    daily_streak: int = 0
    last_practice_date: str = ""
    weekly_goal_progress: int = 0
    weekly_goal_target: int = 1000  # points per week
    achievements_unlocked: list[str] = Field(default_factory=list)
    rewards_purchased: list[str] = Field(default_factory=list)
    points_spent: int = 0


class ProgressState(BaseModel):
    """Learning progress tracking."""

    letters_mastered: list[str] = Field(default_factory=list)
    words_mastered: list[str] = Field(default_factory=list)
    wpm_average: float = 0.0  # words per minute
    accuracy_percentage: float = 0.0
    practice_sessions: int = 0
    total_practice_minutes: int = 0
    skill_level: Literal["beginner", "intermediate", "advanced"] = "beginner"
    improvement_rate: float = 0.0  # percentage improvement per week


class AchievementState(BaseModel):
    """Achievement tracking."""

    achievements: dict[str, dict] = Field(default_factory=dict)
    # Format: {"first_word": {"unlocked": True, "date": "2024-01-15", "reward": 100}}
    badges_earned: list[str] = Field(default_factory=list)
    milestones_reached: list[str] = Field(default_factory=list)


class ParentState(BaseModel):
    """Parent dashboard state."""

    child_id: str = ""
    notifications_enabled: bool = True
    daily_summary_enabled: bool = True
    weekly_report_enabled: bool = True
    encouragement_messages: list[str] = Field(default_factory=list)
    milestones_celebrated: list[str] = Field(default_factory=list)
