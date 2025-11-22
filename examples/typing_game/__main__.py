"""Allow running as: python -m examples.typing_game"""

from .main import main
import asyncio

if __name__ == "__main__":
    asyncio.run(main())
