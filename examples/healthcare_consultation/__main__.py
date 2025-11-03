"""Allow running as: python -m examples.healthcare_consultation"""

from .main import main
import asyncio

if __name__ == "__main__":
    asyncio.run(main())
