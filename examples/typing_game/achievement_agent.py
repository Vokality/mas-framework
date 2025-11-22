"""Achievement system agent - tracks milestones and unlocks."""

from mas import Agent, AgentMessage

try:
    from .typing_game_state import AchievementState
except ImportError:
    from typing_game_state import AchievementState


class AchievementAgent(Agent[AchievementState]):
    """Manages achievements and milestones."""

    ACHIEVEMENTS = {
        "first_letter": {
            "name": "First Letter",
            "description": "Type your first letter",
            "reward_points": 50,
            "check": lambda state: state.get("letters_typed", 0) >= 1,
        },
        "first_word": {
            "name": "First Word",
            "description": "Complete your first word",
            "reward_points": 100,
            "check": lambda state: state.get("words_completed", 0) >= 1,
        },
        "ten_words": {
            "name": "Word Master",
            "description": "Complete 10 words",
            "reward_points": 200,
            "check": lambda state: state.get("words_completed", 0) >= 10,
        },
        "streak_5": {
            "name": "Hot Streak",
            "description": "Get 5 letters in a row correct",
            "reward_points": 150,
            "check": lambda state: state.get("best_streak", 0) >= 5,
        },
        "streak_10": {
            "name": "On Fire!",
            "description": "Get 10 letters in a row correct",
            "reward_points": 300,
            "check": lambda state: state.get("best_streak", 0) >= 10,
        },
        "level_2": {
            "name": "Level Up!",
            "description": "Reach level 2",
            "reward_points": 250,
            "check": lambda state: state.get("current_level", 0) >= 2,
        },
        "level_3": {
            "name": "Level Master",
            "description": "Reach level 3",
            "reward_points": 500,
            "check": lambda state: state.get("current_level", 0) >= 3,
        },
        "perfect_word": {
            "name": "Perfect!",
            "description": "Complete a word with 100% accuracy",
            "reward_points": 200,
            "check": lambda state: state.get("accuracy", 0) >= 100,
        },
        "daily_streak_7": {
            "name": "Week Warrior",
            "description": "Practice 7 days in a row",
            "reward_points": 500,
            "check": lambda state: state.get("daily_streak", 0) >= 7,
        },
        "hundred_stars": {
            "name": "Star Collector",
            "description": "Earn 100 stars",
            "reward_points": 1000,
            "check": lambda state: state.get("stars_earned", 0) >= 100,
        },
    }

    def __init__(self, child_id: str, **kwargs):
        super().__init__(
            f"{child_id}_achievements",
            capabilities=["achievements"],
            state_model=AchievementState,
            **kwargs,
        )
        self.child_id = child_id
        self.reward_agent_id = f"{child_id}_rewards"

    @Agent.on("check.achievements")
    async def check_achievements(self, msg: AgentMessage, payload: dict | None):
        """Check if any achievements should be unlocked."""
        if payload is None:
            payload = {}
        # Get current game state (would need to request from game agent)
        game_state = payload  # Simplified - in real implementation would fetch

        for achievement_id, achievement in self.ACHIEVEMENTS.items():
            if achievement_id not in self.state.achievements:
                # Check if achievement criteria met
                if achievement["check"](game_state):
                    await self.unlock_achievement(achievement_id, achievement)

    @Agent.on("level.complete")
    async def handle_level_complete_achievement(
        self, msg: AgentMessage, payload: dict | None
    ):
        """Check for level-based achievements."""
        if payload is None:
            payload = {}
        level = payload.get("level", 0)

        # Check level achievements
        if level == 2 and "level_2" not in self.state.achievements:
            await self.unlock_achievement("level_2", self.ACHIEVEMENTS["level_2"])
        elif level == 3 and "level_3" not in self.state.achievements:
            await self.unlock_achievement("level_3", self.ACHIEVEMENTS["level_3"])

    @Agent.on("game.complete")
    async def handle_game_complete_achievement(
        self, msg: AgentMessage, payload: dict | None
    ):
        """All levels completed - special achievement."""
        if "all_levels" not in self.state.achievements:
            await self.unlock_achievement(
                "all_levels",
                {
                    "name": "Champion",
                    "description": "Complete all levels!",
                    "reward_points": 2000,
                },
            )

    async def unlock_achievement(self, achievement_id: str, achievement: dict):
        """Unlock an achievement."""
        from datetime import datetime

        # Record achievement
        self.state.achievements[achievement_id] = {
            "unlocked": True,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "reward": achievement.get("reward_points", 0),
        }

        await self.update_state({"achievements": self.state.achievements})

        # Award reward points
        reward_points = achievement.get("reward_points", 0)
        if reward_points > 0:
            await self.send(
                self.reward_agent_id,
                "achievement.reward",
                {"achievement_id": achievement_id, "points": reward_points},
            )

        # Notify child
        await self.send(
            self.child_id,
            "achievement.unlocked",
            {
                "achievement": achievement.get("name", ""),
                "description": achievement.get("description", ""),
                "reward_points": reward_points,
                "celebration": True,
            },
        )

        # Notify parent
        await self.notify_parent(
            "achievement.unlocked",
            {
                "achievement": achievement.get("name", ""),
                "description": achievement.get("description", ""),
            },
        )

    async def notify_parent(self, event_type: str, data: dict):
        """Notify parent agent."""
        try:
            parents = await self.discover(capabilities=["parent"])
            if parents:
                await self.send(parents[0]["id"], event_type, data)
        except Exception:
            pass
