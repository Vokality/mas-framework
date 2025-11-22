"""Textual TUI for interacting with the typing game."""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Optional

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Input, Static, ProgressBar

from mas import Agent

from runtime import GameSystemHandles, configure_client_authorization


class TypingGameUI(App):
    """Textual TUI for the typing game."""

    CSS = """
    Screen {
        background: $boost;
        align: center middle;
    }

    #game_container {
        width: 80%;
        max-width: 100;
        height: auto;
        background: $surface;
        border: thick $primary;
        padding: 2;
    }

    #title {
        width: 100%;
        height: 3;
        text-align: center;
        text-style: bold;
        content-align: center middle;
        background: $primary;
        color: $text;
        margin-bottom: 1;
    }

    #status {
        width: 100%;
        height: 2;
        text-align: center;
        color: $text-muted;
        margin-bottom: 1;
    }

    #word_container {
        width: 100%;
        height: auto;
        min-height: 8;
        background: $panel;
        border: solid $accent;
        border-title-color: $accent;
        padding: 2;
        margin-bottom: 1;
    }

    #word_prompt {
        width: 100%;
        height: 2;
        text-align: center;
        color: $text-muted;
        text-style: italic;
    }

    #word_display {
        width: 100%;
        height: auto;
        min-height: 3;
        text-align: center;
        text-style: bold;
        content-align: center middle;
    }

    #input_container {
        width: 100%;
        height: auto;
        border: solid $primary;
        border-title-color: $primary;
        padding: 1;
        margin-bottom: 1;
    }

    #input_area {
        width: 100%;
        height: auto;
    }

    #letter_input {
        width: 1fr;
        margin-bottom: 1;
    }

    #send_button {
        width: 100%;
        margin-bottom: 0;
    }

    #stats_container {
        width: 100%;
        height: auto;
        background: $panel;
        border: solid $success;
        padding: 1;
        margin-bottom: 1;
    }

    #stats_grid {
        width: 100%;
        height: auto;
        layout: grid;
        grid-size: 3;
        grid-gutter: 1;
    }

    .stat_box {
        width: 100%;
        height: 4;
        text-align: center;
        background: $surface;
        border: round $accent;
        padding: 1;
    }

    .stat_value {
        text-style: bold;
        color: $accent;
    }

    .stat_label {
        color: $text-muted;
        text-style: italic;
    }

    #progress_container {
        width: 100%;
        height: auto;
        padding: 1;
        background: $panel;
        border: solid $warning;
        margin-bottom: 1;
    }

    #progress_label {
        width: 100%;
        text-align: center;
        color: $text-muted;
        margin-bottom: 1;
    }

    #level_progress {
        width: 100%;
    }

    #encouragement {
        width: 100%;
        height: 3;
        text-align: center;
        text-style: bold italic;
        color: $success;
        background: $panel;
        border: round $success;
        padding: 1;
    }

    .celebration {
        background: $success;
        color: $text;
        text-style: bold;
    }

    .error_shake {
        background: $error;
        color: $text;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        child_id: str = "child_alex",
        parent_id: str = "parent",
        redis_url: str = "redis://localhost:6379",
        system_handles: GameSystemHandles | None = None,
        started_system: bool = False,
    ):
        super().__init__()
        self.child_id = child_id
        self.parent_id = parent_id
        self.redis_url = redis_url
        self.system_handles = system_handles
        self.started_system = started_system
        self.client_agent: Agent | None = None
        self.current_word = ""
        self.current_letter_index = 0
        self.points = 0
        self.stars = 0
        self.level = 1
        self.words_completed = 0
        self.total_words_in_level = 5  # Default
        self.streak = 0
        self.last_feedback = ""
        self._poll_task: asyncio.Task[None] | None = None
        self._temporary_gateway = None
        self._cleanup_complete = False
        self._status_widget: Optional[Static] = None
        self._encouragement_widget: Optional[Static] = None
        self._redis_refresh_interval = 0.5
        self._encouragement_messages = [
            "🌟 Amazing! Keep going!",
            "💪 You're doing great!",
            "🎉 Fantastic work!",
            "⭐ Super star!",
            "🚀 You're on fire!",
            "👏 Well done!",
            "🎯 Perfect!",
            "🏆 Champion!",
        ]
        self._message_index = 0

    async def on_mount(self) -> None:
        """Initialize when the app mounts."""
        await self._set_status("Connecting to typing game…")
        self.client_agent = Agent(
            f"{self.child_id}_ui",
            redis_url=self.redis_url,
            use_gateway=True,
        )
        try:
            await self.client_agent.start()
            await self._set_status("✓ Connected. Preparing authorization…")
            gateway = self.system_handles.gateway if self.system_handles else None
            self._temporary_gateway = await configure_client_authorization(
                self.client_agent.id,
                self.child_id,
                self.redis_url,
                gateway=gateway,
            )
            await self._set_status("✓ Authorized. Loading game state…")
        except Exception as exc:
            await self._set_status(f"Connection failed: {exc}")
            self.notify(f"Unable to connect: {exc}", severity="error")
            await self.shutdown_resources()
            self.exit()
            return

        await self.update_game_state()
        if self.started_system:
            await self._set_status("Game system started locally. Ready to type!")
        else:
            await self._set_status("Connected to existing game system. Ready to type!")
        self._poll_task = asyncio.create_task(self._poll_game_state())

    async def update_game_state(self) -> None:
        """Update UI with current game state."""
        import redis.asyncio as redis

        r = redis.from_url(self.redis_url, decode_responses=True)
        try:
            state = await r.hgetall(f"agent.state:{self.child_id}")
            if state:
                self.current_word = state.get("current_word", "")
                self.current_letter_index = int(state.get("current_letter_index", 0))
                self.level = int(state.get("current_level", 1))
                self.words_completed = int(state.get("words_completed", 0))
                self.streak = int(state.get("current_streak", 0))

                # Calculate level progress
                self.total_words_in_level = self._get_words_in_level(self.level)

                reward_state = await r.hgetall(f"agent.state:{self.child_id}_rewards")
                if reward_state:
                    self.points = int(reward_state.get("total_points", 0))
                    self.stars = int(reward_state.get("stars_earned", 0))

                self._render_display()
            else:
                await self._set_status("Waiting for game to start…")
        except Exception as exc:
            self.notify(f"Error updating state: {exc}", severity="error")
        finally:
            await r.aclose()

    def _get_words_in_level(self, level: int) -> int:
        """Get the number of words in a level."""
        # This mirrors the LEVEL_WORDS structure in typing_game_agent.py
        level_word_counts = {
            1: 5,  # Single letters
            2: 5,  # Simple words
            3: 5,  # Common words
            4: 3,  # Sentences
        }
        return level_word_counts.get(level, 5)

    def _render_display(self) -> None:
        """Update the display widgets with the latest state."""
        # Update word display
        word_widget = self.query_one("#word_display", Static)
        if self.current_word:
            # Build word with each letter formatted and spaced
            letter_parts = []
            for i, letter in enumerate(self.current_word):
                if i == self.current_letter_index:
                    # Current letter to type - bright and highlighted
                    letter_parts.append(f"[bold reverse green on black]{letter}[/]")
                elif i < self.current_letter_index:
                    # Already typed - green checkmark style
                    letter_parts.append(f"[green]{letter}[/]")
                else:
                    # Not yet typed - dimmed
                    letter_parts.append(f"[dim]{letter}[/]")

            # Join formatted letters with spaces between them
            display_word = " ".join(letter_parts)
            word_widget.update(f"\n{display_word}\n")
            asyncio.create_task(
                self._set_status("Type the highlighted letter and press Enter ⌨️")
            )
        else:
            word_widget.update("\n[dim]Waiting for game to start...[/]\n")

        # Update stats boxes
        level_stat = self.query_one("#level_stat", Static)
        level_stat.update(f"[bold cyan]Level {self.level}[/]\n[dim]Current[/]")

        points_stat = self.query_one("#points_stat", Static)
        points_stat.update(f"[bold yellow]⭐ {self.points}[/]\n[dim]Points[/]")

        stars_stat = self.query_one("#stars_stat", Static)
        stars_stat.update(f"[bold magenta]🌟 {self.stars}[/]\n[dim]Stars[/]")

        # Update progress bar
        progress_label = self.query_one("#progress_label", Static)
        level_position = self.words_completed % self.total_words_in_level
        progress_label.update(
            f"Level Progress: {level_position}/{self.total_words_in_level} words"
        )

        try:
            progress_bar = self.query_one("#level_progress", ProgressBar)
            progress_bar.total = self.total_words_in_level
            progress_bar.progress = level_position
        except Exception:
            pass  # Progress bar might not be mounted yet

        # Update encouragement message based on streak
        if self._encouragement_widget:
            if self.streak >= 5:
                message = f"🔥 {self.streak} Letter Streak! Amazing! 🔥"
                self._encouragement_widget.update(message)
                self._encouragement_widget.add_class("celebration")
            elif self.streak >= 3:
                message = f"✨ {self.streak} in a row! Keep it up! ✨"
                self._encouragement_widget.update(message)
                self._encouragement_widget.remove_class("celebration")
            else:
                message = self._encouragement_messages[
                    self._message_index % len(self._encouragement_messages)
                ]
                self._encouragement_widget.update(message)
                self._encouragement_widget.remove_class("celebration")

    def compose(self) -> ComposeResult:
        """Create child widgets."""
        yield Header()

        with Container(id="game_container"):
            # Title
            yield Static("🎮 Typing Game! 🎮", id="title")

            # Status message
            self._status_widget = Static("", id="status")
            yield self._status_widget

            # Word display container
            with Container(id="word_container"):
                yield Static("", id="word_display")

            # Input area
            with Container(id="input_container"):
                with Vertical(id="input_area"):
                    yield Input(
                        placeholder="Type the letter here… 📝",
                        id="letter_input",
                    )
                    yield Button("✓ Send Letter", id="send_button", variant="primary")

            # Stats display (3-column grid)
            with Container(id="stats_container"):
                with Horizontal(id="stats_grid"):
                    yield Static("", id="level_stat", classes="stat_box")
                    yield Static("", id="points_stat", classes="stat_box")
                    yield Static("", id="stars_stat", classes="stat_box")

            # Progress bar
            with Container(id="progress_container"):
                yield Static("", id="progress_label")
                yield ProgressBar(id="level_progress", total=5, show_eta=False)

            # Encouragement message
            self._encouragement_widget = Static("You can do it! 💪", id="encouragement")
            yield self._encouragement_widget

        yield Footer()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle letter input."""
        if not self.client_agent:
            return

        letter = event.value.strip()
        if not letter:
            return

        letter = letter[0].lower()

        # Check if the letter is correct before sending
        expected_letter = (
            self.current_word[self.current_letter_index].lower()
            if self.current_word and self.current_letter_index < len(self.current_word)
            else ""
        )

        is_correct = letter == expected_letter

        try:
            await self.client_agent.send(
                self.child_id,
                "letter.typed",
                {
                    "letter": letter,
                    "timestamp": time.time(),
                },
            )

            event.input.value = ""

            # Visual feedback
            if is_correct:
                self.notify("✓ Correct!", severity="information", timeout=1)
                # Cycle encouragement message
                self._message_index += 1

                # Check if word will be complete
                if self.current_letter_index + 1 >= len(self.current_word):
                    self.notify(
                        "🎉 Word Complete! 🎉", severity="information", timeout=2
                    )
            else:
                self.notify(
                    f"✗ Try '{expected_letter}' instead", severity="warning", timeout=2
                )

            await asyncio.sleep(0.2)
            await self.update_game_state()

        except Exception as exc:
            self.notify(f"Error sending letter: {exc}", severity="error")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press."""
        if event.button.id == "send_button":
            input_widget = self.query_one("#letter_input", Input)
            input_widget.submit()

    async def action_quit(self) -> None:
        """Handle quit action."""
        await self.shutdown_resources()
        self.exit()

    async def on_unmount(self) -> None:
        """Ensure resources are freed when the app unmounts."""
        await self.shutdown_resources()

    async def shutdown_resources(self) -> None:
        """Idempotent cleanup for background tasks and connections."""
        if self._cleanup_complete:
            return
        self._cleanup_complete = True

        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._poll_task
        self._poll_task = None

        if self.client_agent:
            await self.client_agent.stop()
            self.client_agent = None

        if self._temporary_gateway:
            await self._temporary_gateway.stop()
            self._temporary_gateway = None

    async def _poll_game_state(self) -> None:
        """Continuously poll Redis for updates while the app is running."""
        try:
            while True:
                await self.update_game_state()
                await asyncio.sleep(self._redis_refresh_interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.notify(f"Polling error: {exc}", severity="error")

    async def _set_status(self, message: str) -> None:
        """Update the status widget if available."""
        if self._status_widget is not None:
            self._status_widget.update(message)


async def main() -> None:
    """Run the typing game UI."""
    import os

    child_id = os.getenv("CHILD_ID", "child_alex")
    parent_id = os.getenv("PARENT_ID", "parent")
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")

    app = TypingGameUI(child_id=child_id, parent_id=parent_id, redis_url=redis_url)
    await app.run_async()
    await app.shutdown_resources()


if __name__ == "__main__":
    asyncio.run(main())
