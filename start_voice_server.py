"""Start the Pipecat WebSocket voice server."""

import sys
import os

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Now import and run the server
from websocket_server import main
import asyncio

if __name__ == "__main__":
    print("Starting Pipecat WebSocket Server on ws://localhost:8765")
    print("Press Ctrl+C to stop")
    asyncio.run(main())
