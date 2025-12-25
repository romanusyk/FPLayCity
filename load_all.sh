#!/bin/bash

# Check if .env file exists
if [ ! -f .env ]; then
    echo "Error: .env file not found"
    exit 1
fi

# Check if NEXT_GAMEWEEK is defined in .env
if ! grep -q "^NEXT_GAMEWEEK=" .env; then
    echo "Error: NEXT_GAMEWEEK is not set in .env file"
    exit 1
fi

# Check that argument is provided
if [ -z "$1" ]; then
    echo "Error: Gameweek number argument is required"
    exit 1
fi

# Update NEXT_GAMEWEEK
sed -i '' "s/^NEXT_GAMEWEEK=.*/NEXT_GAMEWEEK=$1/" .env
echo "Updated NEXT_GAMEWEEK to $1"

uv run -m src.fpl.fetch

uv run -m src.fotmob.load
