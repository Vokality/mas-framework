"""Parent monitoring agent - tracks child's progress."""

from typing import Any

from mas import Agent, AgentMessage

try:
    from .typing_game_state import ParentState
except ImportError:
    from typing_game_state import ParentState


class ParentAgent(Agent[ParentState]):
    """Parent monitoring and encouragement agent."""

    ENCOURAGEMENT_MESSAGES = [
        "Wow! You're typing so fast! 🚀",
        "Great job! Keep it up! ⭐",
        "You're getting better every day! 🌟",
        "Amazing progress! I'm so proud! 🎉",
        "You're a typing superstar! ⭐⭐⭐",
    ]

    def __init__(self, parent_id: str, child_id: str, **kwargs):
        super().__init__(
            parent_id, capabilities=["parent"], state_model=ParentState, **kwargs
        )
        self.child_id = child_id  # Store as instance variable, set in state after start

    async def on_start(self):
        """Initialize parent state after agent starts."""
        patch: dict[str, Any] = {"child_id": self.child_id}
        if not hasattr(self.state, "notifications_enabled"):
            patch["notifications_enabled"] = True
        if not hasattr(self.state, "daily_summary_enabled"):
            patch["daily_summary_enabled"] = True
        if not hasattr(self.state, "weekly_report_enabled"):
            patch["weekly_report_enabled"] = True
        if not hasattr(self.state, "encouragement_messages"):
            patch["encouragement_messages"] = []
        if not hasattr(self.state, "milestones_celebrated"):
            patch["milestones_celebrated"] = []
        await self.update_state(patch)

    @Agent.on("game.started")
    async def handle_game_start(self, msg: AgentMessage, payload: dict | None):
        """Child started playing."""
        if self._notifications_enabled():
            if payload is None:
                payload = {}
            level = payload.get("level", 1)
            progress = payload.get("progress", 0)
            await self.show_notification(
                "Game Started", f"Level {level} - {progress} words completed"
            )

    @Agent.on("word.complete")
    async def handle_word_complete(self, msg: AgentMessage, payload: dict | None):
        """Child completed a word."""
        # Track progress silently, celebrate milestones
        pass

    @Agent.on("level.complete")
    async def handle_level_complete_parent(
        self, msg: AgentMessage, payload: dict | None
    ):
        """Child completed a level."""
        if payload is None:
            payload = {}
        level = payload.get("level", 0)

        if self._notifications_enabled():
            await self.show_notification(
                "Level Complete! 🎉", f"Your child completed level {level}!"
            )

        # Send encouragement
        await self.send_encouragement(self.child_id, f"level_{level}")

    @Agent.on("all_levels.complete")
    async def handle_all_levels_complete(self, msg: AgentMessage, payload: dict | None):
        """Child completed all levels!"""
        if self._notifications_enabled():
            await self.show_notification(
                "Amazing Achievement! 🏆", "Your child completed all levels!"
            )

        await self.send_encouragement(self.child_id, "all_levels")

    @Agent.on("reward.unlocked")
    async def handle_reward_unlocked(self, msg: AgentMessage, payload: dict | None):
        """Child unlocked a reward."""
        if payload is None:
            payload = {}
        reward_type = payload.get("reward_type", "")
        reward_id = payload.get("reward_id", "")

        if self._notifications_enabled():
            await self.show_notification(
                "Reward Unlocked! 🎁", f"Unlocked: {reward_type} - {reward_id}"
            )

    @Agent.on("weekly_goal.achieved")
    async def handle_weekly_goal(self, msg: AgentMessage, payload: dict | None):
        """Child reached weekly goal."""
        if self._notifications_enabled():
            if payload is None:
                payload = {}
            await self.show_notification(
                "Weekly Goal Achieved! 🎯",
                f"Earned {payload.get('points', 0)} points this week!",
            )

    @Agent.on("achievement.unlocked")
    async def handle_achievement(self, msg: AgentMessage, payload: dict | None):
        """Child unlocked an achievement."""
        if payload is None:
            payload = {}
        achievement = payload.get("achievement", "")

        if self.state.notifications_enabled:
            await self.show_notification("Achievement Unlocked! 🏅", achievement)

        await self.send_encouragement(self.child_id, "achievement")

    async def send_encouragement(self, child_id: str, context: str):
        """Send encouragement message to child."""
        import random

        message = random.choice(self.ENCOURAGEMENT_MESSAGES)

        await self.send(
            child_id, "encouragement.message", {"message": message, "from": "parent"}
        )

        messages = list(getattr(self.state, "encouragement_messages", []))
        messages.append(message)
        await self.update_state({"encouragement_messages": messages})

    async def show_notification(self, title: str, message: str):
        """Show notification to parent (implement based on platform)."""
        # This would integrate with your notification system
        # For example: push notification, desktop notification, etc.
        print(f"📱 Parent Notification: {title} - {message}")

    async def get_daily_summary(self) -> dict:
        """Get daily progress summary."""
        # Request progress from child's agents
        try:
            await self.discover(capabilities=["reward_system"])
            # In real implementation, would request data from discovered agent
            return {
                "points_earned": 0,
                "words_completed": 0,
                "stars_earned": 0,
                "practice_time": 0,
            }
        except Exception:
            return {}

    def _notifications_enabled(self) -> bool:
        """Return whether notifications are enabled, defaulting to True."""
        return bool(getattr(self.state, "notifications_enabled", True))
