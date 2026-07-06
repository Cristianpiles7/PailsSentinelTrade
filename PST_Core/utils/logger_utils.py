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
        # Capturamos el loop del bot (el handler se crea dentro del loop en main()).
        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                self.loop = asyncio.get_event_loop()
            except RuntimeError:
                self.loop = None

    def emit(self, record):
        try:
            msg = self.format(record)
            level = record.levelname
            source = record.name
            loop = self.loop
            if not (loop and loop.is_running()):
                # Sin loop del bot: descartar. Nunca bloquear el hilo con asyncio.run().
                return

            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None

            coro = self.db.add_log(level, msg, source)
            if running is loop:
                loop.create_task(coro)
            else:
                # emit() llegó desde un hilo worker (p.ej. asyncio.to_thread):
                # create_task NO es thread-safe y perdía/corrompía registros.
                asyncio.run_coroutine_threadsafe(coro, loop)
        except Exception:
            self.handleError(record)
