#!/bin/bash

# Chemistry Tutoring Demo Runner
# This script checks prerequisites and runs the example

set -e

echo "================================"
echo "Chemistry Tutoring Demo"
echo "================================"
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

# Check OpenAI API key (in .env file or environment variable)
if [ -z "$OPENAI_API_KEY" ] && [ ! -f "../../.env" ]; then
    echo "ERROR: OPENAI_API_KEY not found!"
    echo ""
    echo "Please either:"
    echo "  1. Add to .env file: echo 'OPENAI_API_KEY=your-key' >> ../../.env"
    echo "  2. Set environment variable: export OPENAI_API_KEY='your-key'"
    exit 1
fi

if [ -f "../../.env" ]; then
    echo "✓ Found .env file in project root"
else
    echo "✓ OpenAI API key is set in environment"
fi
echo ""

# Install dependencies using uv
echo "Installing dependencies with uv..."
uv pip install openai python-dotenv --quiet 2>&1 || {
    echo "WARNING: Could not install dependencies"
    echo "Attempting to continue anyway..."
}
echo "✓ Dependencies ready"
echo ""

echo "Starting demo..."
echo "================================"
echo ""

# Run the example with uv
uv run python main.py
