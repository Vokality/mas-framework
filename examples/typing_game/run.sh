#!/bin/bash

# Typing Game Demo Runner
# This script checks prerequisites and runs the typing game example

set -e

echo "================================================"
echo "Typing Game for 4-Year-Olds"
echo "Reward-Driven Learning System"
echo "================================================"
echo ""

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "ERROR: uv package manager not found!"
    echo ""
    echo "Please install uv:"
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo ""
    echo "Or visit: https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
fi
echo "✓ uv is installed"
echo ""

# Check Redis
echo "Checking Redis connection..."
if ! redis-cli ping > /dev/null 2>&1; then
    echo "ERROR: Redis is not running!"
    echo ""
    echo "Please start Redis:"
    echo "  macOS:  brew services start redis"
    echo "  Docker: docker run -d -p 6379:6379 redis:latest"
    echo "  Linux:  sudo systemctl start redis"
    exit 1
fi
echo "✓ Redis is running"
echo ""

# Check Redis version (Streams require 5.0+)
redis_version=$(redis-cli INFO | grep redis_version | cut -d: -f2 | cut -d. -f1 | tr -d '\r')
if [ "$redis_version" -lt 5 ]; then
    echo "WARNING: Redis version $redis_version detected"
    echo "Gateway mode requires Redis 5.0+ for Streams support"
    echo "Please upgrade Redis or gateway features may not work"
    echo ""
fi

echo "Starting Typing Game System..."
echo ""
echo "This will start:"
echo "  • MAS Service (agent registry)"
echo "  • Gateway Service (security & routing)"
echo "  • Game Agent (typing game logic)"
echo "  • Reward Agent (points, stars, gems)"
echo "  • Achievement Agent (milestones)"
echo "  • Parent Agent (monitoring & encouragement)"
echo ""
echo "Press Ctrl+C to stop"
echo "================================================"
echo ""

# Run the example with uv
uv run python main.py

