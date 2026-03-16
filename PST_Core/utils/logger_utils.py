import logging
import asyncio
from PST_Core.models.database import PSTDatabase

class DBLogHandler(logging.Handler):
    """
    Custom logging handler that sends logs to the PST SQLite database.
    """
    def __init__(self, db: PSTDatabase):
        super().__init__()
        self.db = db
        # We need an event loop to run async database calls.
        # Since the bot runs in an asyncio loop, we can use it.
        try:
            self.loop = asyncio.get_event_loop()
        except RuntimeError:
            self.loop = None

    def emit(self, record):
        try:
            msg = self.format(record)
            level = record.levelname
            source = record.name
            
            # If we are in an event loop, schedule the async call
            if self.loop and self.loop.is_running():
                self.loop.create_task(self.db.add_log(level, msg, source))
            else:
                # Fallback: create a temporary loop if necessary (less efficient)
                # Note: This is rare in the orchestrator as it is fully async.
                asyncio.run(self.db.add_log(level, msg, source))
        except Exception:
            self.handleError(record)
