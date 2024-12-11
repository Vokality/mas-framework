import asyncio  # noqa: I001
from typing import Dict, Optional
from mas.logger import get_logger

logger = get_logger()


class TaskManager:
    """
    Internal task manager for transport layer background tasks.
    Ensures proper cleanup and prevents task leaks.
    """

    def __init__(self) -> None:
        self._tasks: Dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self._loop = asyncio.get_running_loop()

    async def create_task(
        self,
        name: str,
        coro,
        *,
        timeout: Optional[float] = None,
    ) -> asyncio.Task:
        """
        Create and track a new task.

        Args:
            name: Unique task identifier
            coro: Coroutine to run
            timeout: Optional timeout for task completion

        Returns:
            Created task
        """
        async with self._lock:
            if name in self._tasks and not self._tasks[name].done():
                logger.info(f"Task {name} already exists and running")
                return self._tasks[name]

            if timeout:

                async def wrapped():
                    try:
                        return await asyncio.wait_for(coro, timeout=timeout)
                    except asyncio.TimeoutError:
                        logger.error(f"Task {name} timed out")
                        raise

                task_coro = wrapped()
            else:
                task_coro = coro

            task = self._loop.create_task(task_coro, name=name)
            self._tasks[name] = task
            logger.info(f"Created task: {name}")
            return task

    async def cancel_task(self, name: str, timeout: float = 5.0) -> None:
        """
        Cancel a specific task.

        Args:
            name: Task identifier
            timeout: Maximum wait time for cancellation
        """
        async with self._lock:
            if task := self._tasks.get(name):
                if not task.done():
                    logger.info(f"Cancelling task: {name}")
                    task.cancel()
                    try:
                        await asyncio.wait_for(task, timeout=timeout)
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        logger.warning(f"Task cancellation interrupted: {name}")
                    except Exception as e:
                        logger.error(f"Error cancelling task {name}: {e}")
                del self._tasks[name]

    async def cancel_all(self, timeout: float = 5.0) -> None:
        """
        Cancel all tracked tasks.

        Args:
            timeout: Maximum wait time for all tasks
        """
        async with self._lock:
            if not self._tasks:
                return

            logger.info(f"Cancelling {len(self._tasks)} tasks")
            tasks = list(self._tasks.values())
            names = list(self._tasks.keys())

            # Cancel all tasks
            for task in tasks:
                if not task.done():
                    task.cancel()

            # Wait for cancellation
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True), timeout=timeout
                )
            except asyncio.TimeoutError:
                logger.error(f"Timeout cancelling tasks: {', '.join(names)}")
            except Exception as e:
                logger.error(f"Error during task cancellation: {e}")
            finally:
                self._tasks.clear()

    def get_running_tasks(self) -> Dict[str, asyncio.Task]:
        """Get all currently running tasks."""
        return {name: task for name, task in self._tasks.items() if not task.done()}

    def is_running(self, name: str) -> bool:
        """Check if a named task exists and is running."""
        if task := self._tasks.get(name):
            return not task.done()
        return False
