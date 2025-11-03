"""Allow running as: python -m examples.chemistry_tutoring"""

from .main import main
import asyncio

if __name__ == "__main__":
    asyncio.run(main())
