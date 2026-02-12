"""Maintenance message API endpoint.

Reads from a mounted maintenance_message.txt file to display
scheduled maintenance notices to users.
"""

from fastapi import APIRouter
from typing import Optional
import os
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/maintenance")

# Path to the maintenance message file (mounted via docker-compose)
MAINTENANCE_MESSAGE_FILE = os.getenv(
    "MAINTENANCE_MESSAGE_FILE",
    "/app/config/maintenance_message.txt"
)


def read_maintenance_message() -> Optional[str]:
    """
    Read the maintenance message from the file.

    Returns the message if one is set, or None if:
    - File doesn't exist
    - File is empty
    - All lines are comments (start with #)
    """
    try:
        if not os.path.exists(MAINTENANCE_MESSAGE_FILE):
            logger.debug('Maintenance file not found: %s', MAINTENANCE_MESSAGE_FILE)
            return None

        with open(MAINTENANCE_MESSAGE_FILE, 'r') as f:
            lines = f.readlines()

        # Find the first non-comment, non-empty line
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith('#'):
                logger.info('Maintenance message active: %s...', stripped[:50])
                return stripped

        return None

    except Exception as e:
        logger.error('Error reading maintenance message: %s', e)
        return None


@router.get("/message")
async def get_maintenance_message() -> dict:
    """
    Get the current maintenance message, if any.

    Returns:
        - message: The maintenance message text, or null if none
        - active: Boolean indicating if a maintenance message is active
    """
    message = read_maintenance_message()

    return {
        "message": message,
        "active": message is not None
    }
