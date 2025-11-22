"""Reward system agent - manages points, stars, and rewards."""

import logging
from datetime import datetime, timedelta

from mas import Agent, AgentMessage
from pydantic import BaseModel

logger = logging.getLogger(__name__)

try:
    from .typing_game_state import RewardState
except ImportError:
    from typing_game_state import RewardState


class RewardRequest(BaseModel):
    """Request to purchase a reward."""

    reward_type: str  # "character", "theme", "mini_game", "surprise_box"
    reward_id: str


class RewardAgent(Agent[RewardState]):
    """Manages reward system - points, stars, achievements."""

    # Reward costs
    REWARD_COSTS = {
        "character": 500,
        "theme": 300,
        "mini_game": 1000,
        "surprise_box": 200,
    }

    def __init__(self, child_id: str, **kwargs):
        super().__init__(
            f"{child_id}_rewards",
            capabilities=["reward_system"],
            state_model=RewardState,
            **kwargs,
        )
        self.child_id = child_id
        self.achievement_agent_id = f"{child_id}_achievements"

    async def on_start(self):
        """Initialize reward system."""
        # Check if it's a new day (reset daily streak if needed)
        today = datetime.now().strftime("%Y-%m-%d")
        if self.state.last_practice_date != today:
            # Check if streak continues (practiced yesterday)
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            if self.state.last_practice_date != yesterday:
                # Streak broken
                self.state.daily_streak = 0

            self.state.last_practice_date = today
            await self.update_state({"last_practice_date": today})

        # Check weekly goal progress
        await self.check_weekly_goal()

    @Agent.on("letter.correct")
    async def reward_correct_letter(self, msg: AgentMessage, payload: dict | None):
        """Reward for correct letter."""
        if payload is None:
            payload = {}
        logger.info(f"Reward agent received letter.correct: {payload}")

        base_points = payload.get("points", 10)
        streak = payload.get("streak", 0)

        # Streak bonus (every 5 letters = 2x multiplier)
        multiplier = 1.0
        if streak > 0 and streak % 5 == 0:
            multiplier = 2.0
            # Bonus celebration
            await self.send(
                self.child_id,
                "streak.bonus",
                {"streak": streak, "multiplier": multiplier},
            )

        # Daily streak multiplier
        if self.state.daily_streak >= 7:
            multiplier *= 1.5  # 7+ day streak bonus

        points_earned = int(base_points * multiplier)
        old_points = self.state.total_points
        self.state.total_points += points_earned

        logger.info(
            f"Awarding {points_earned} points (was {old_points}, now {self.state.total_points})"
        )
        await self.update_state({"total_points": self.state.total_points})

        # Immediate visual feedback
        await self.send(
            self.child_id,
            "reward.visual",
            {"type": "points", "amount": points_earned, "animation": "sparkle"},
        )

    @Agent.on("word.complete")
    async def reward_word_completion(self, msg: AgentMessage, payload: dict | None):
        """Reward for completing a word."""
        if payload is None:
            payload = {}
        logger.info(f"Reward agent received word.complete: {payload}")
        stars = payload.get("stars", 1)
        accuracy = payload.get("accuracy", 0)
        time_seconds = payload.get("time_seconds", 10.0)

        # Base points for word completion
        base_points = 50

        # Speed bonus (faster = more points)
        if time_seconds < 3.0:
            base_points += 30
        elif time_seconds < 5.0:
            base_points += 15

        # Accuracy bonus
        if accuracy >= 95:
            base_points += 50  # Perfect word bonus

        # Star rewards
        self.state.stars_earned += stars

        # Gems for high performance
        gems = 0
        if stars == 3:
            gems = 5
            self.state.gems_earned += gems

        # Update points
        self.state.total_points += base_points

        await self.update_state(
            {
                "total_points": self.state.total_points,
                "stars_earned": self.state.stars_earned,
                "gems_earned": self.state.gems_earned,
            }
        )

        # Celebration animation
        await self.send(
            self.child_id,
            "reward.celebration",
            {
                "stars": stars,
                "points": base_points,
                "gems": gems,
                "animation": "balloon_burst" if stars == 3 else "confetti",
            },
        )

        # Check for achievements
        await self.send(
            self.achievement_agent_id,
            "check.achievements",
            {
                "stars": self.state.stars_earned,
                "points": self.state.total_points,
                "gems": self.state.gems_earned,
            },
        )

        # Update weekly goal
        self.state.weekly_goal_progress += base_points
        await self.check_weekly_goal()

    @Agent.on("level.started")
    async def handle_level_start(self, msg: AgentMessage, payload: dict | None):
        """Handle level start - may unlock rewards."""
        if payload is None:
            payload = {}
        level = payload.get("level", 1)

        # Unlock character at level 2
        if level == 2:
            await self.unlock_reward("character", "robot")

        # Unlock theme at level 3
        if level == 3:
            await self.unlock_reward("theme", "space")

    @Agent.on("reward.purchase", model=RewardRequest)
    async def handle_purchase(self, msg: AgentMessage, payload: RewardRequest):
        """Handle reward purchase request."""
        cost = self.REWARD_COSTS.get(payload.reward_type, 0)

        if self.state.total_points < cost:
            await msg.reply(
                "purchase.failed",
                {
                    "reason": "insufficient_points",
                    "required": cost,
                    "current": self.state.total_points,
                },
            )
            return

        # Deduct points
        self.state.total_points -= cost
        self.state.points_spent += cost
        self.state.rewards_purchased.append(
            f"{payload.reward_type}:{payload.reward_id}"
        )

        await self.update_state(
            {
                "total_points": self.state.total_points,
                "points_spent": self.state.points_spent,
                "rewards_purchased": self.state.rewards_purchased,
            }
        )

        # Unlock reward
        await self.unlock_reward(payload.reward_type, payload.reward_id)

        await msg.reply(
            "purchase.success",
            {
                "reward_type": payload.reward_type,
                "reward_id": payload.reward_id,
                "points_remaining": self.state.total_points,
            },
        )

    async def unlock_reward(self, reward_type: str, reward_id: str):
        """Unlock a reward."""
        await self.send(
            self.child_id,
            "reward.unlocked",
            {"reward_type": reward_type, "reward_id": reward_id, "celebration": True},
        )

        # Notify parent
        await self.notify_parent(
            "reward.unlocked", {"reward_type": reward_type, "reward_id": reward_id}
        )

    async def check_weekly_goal(self):
        """Check if weekly goal is reached."""
        if self.state.weekly_goal_progress >= self.state.weekly_goal_target:
            # Goal reached!
            bonus_points = 500
            self.state.total_points += bonus_points

            await self.update_state(
                {
                    "total_points": self.state.total_points,
                    "weekly_goal_progress": 0,  # Reset for next week
                }
            )

            # Big celebration
            await self.send(
                self.child_id,
                "goal.achieved",
                {
                    "type": "weekly",
                    "bonus_points": bonus_points,
                    "animation": "fireworks",
                },
            )

            # Notify parent
            await self.notify_parent(
                "weekly_goal.achieved", {"points": self.state.weekly_goal_progress}
            )

    async def notify_parent(self, event_type: str, data: dict):
        """Notify parent agent."""
        try:
            parents = await self.discover(capabilities=["parent"])
            if parents:
                await self.send(parents[0]["id"], event_type, data)
        except Exception:
            pass
