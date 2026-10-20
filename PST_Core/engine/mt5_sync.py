"""
Sincronizador continuo MT5 -> BBDD (Fase 3 del plan de reconversion a dashboard de solo
monitorizacion, 2026-09-02). Sustituye a `sync_trades_task` del motor de trading retirado
(orchestrator.py) -- aquel solo reconciliaba historial CERRADO cada 60s via
`PSTDatabase.sync_mt5_history()`; esto ademas detecta posiciones ABIERTAS en cuanto aparecen
(sea cual sea su origen: los 9 EAs MQL5 en produccion, ver ../../MT5/README.md, o una operacion
manual desde MT5 directamente), para que aparezcan en la BBDD sin esperar a que se cierren.

No depende de orchestrator.py/executor.py -- estan retirados a proposito (ver PST_API/main.py,
lifespan()) para que no pueda enviarse ninguna orden real desde esta web.
"""
import asyncio
import logging
from datetime import datetime

import aiosqlite

from .mt5_async import get_positions_async, get_history_deals_async

logger = logging.getLogger("PST-Sync")

CHECK_INTERVAL_SEC = 15

# Mapa magic number -> nombre de estrategia, fuente de verdad: MT5/README.md (migrado desde
# PailsSentinelMT5TradeV2). Sin esto, cualquier posicion no abierta por el bot se etiquetaba
# genericamente "AUTO_SYNC" en sync_mt5_history -- con el mapa, las 9 estrategias en produccion
# aparecen con su nombre real en el historial/dashboard.
MAGIC_STRATEGY_MAP = {
    100500: "H5 Bollinger+RSI",
    100700: "H12 GapFill",
    101000: "H20 RelativeStrength",
    101100: "H25 VWAP+ADX",
    101300: "H28b MACD Momentum",
    101800: "H21 LiquiditySweep",
    101900: "H41 Donchian+Volumen (AAPL)",
    101901: "H41 Donchian+Volumen (TSLA)",
    101902: "H42 GapAndGo (AAPL)",
    102001: "H45 Ichimoku (GER40.cash)",
    102003: "H45 Ichimoku (CADJPY)",
}


class MT5PositionSync:
    """Tarea de fondo: cada CHECK_INTERVAL_SEC compara las posiciones abiertas en MT5 contra las
    conocidas la ultima vez. Las nuevas se insertan en `trades` de inmediato (price_out/time_out
    NULL); las que desaparecen se dan por cerradas y se reconcilian via historial de deals
    (reutiliza PSTDatabase.sync_mt5_history, que ya sabe emparejar por ticket=position_id)."""

    def __init__(self, db):
        self.db = db
        self._known_open_tickets: set[int] = set()

    async def run(self):
        logger.info("🔄 [SYNC] Sincronizador de posiciones MT5 iniciado (intervalo: %ds)", CHECK_INTERVAL_SEC)
        await self._backfill_recent_history()
        await self._seed_known_open()
        while True:
            try:
                await self._check_cycle()
            except Exception as e:
                logger.error("❌ [SYNC] Error en ciclo: %s", e)
            await asyncio.sleep(CHECK_INTERVAL_SEC)

    async def _backfill_recent_history(self):
        """Reconciliacion historica UNA SOLA VEZ al arrancar (30 dias) -- cubre cierres que
        ocurrieron mientras la app no corria. Antes esto se repetia en cada GET de /api/account
        y /api/symbols (sondeados cada 3s desde el frontend), lo que generaba una condicion de
        carrera: dos sync_mt5_history() casi simultaneos podian duplicar la misma fila antes de
        que el primero confirmara. Al hacerlo solo aqui, una vez, se elimina esa carrera."""
        try:
            deals = await get_history_deals_async(days=30)
            if deals:
                await self.db.sync_mt5_history(list(deals))
        except Exception as e:
            logger.error("❌ [SYNC] Error en backfill de historial: %s", e)

    async def _seed_known_open(self):
        """Al arrancar, las posiciones ya abiertas NO se tratan como 'nuevas' (podrian ya estar
        en la tabla trades de un arranque anterior) -- solo se insertan aqui si no existe fila,
        para cubrir el caso de un primer arranque con EAs ya operando."""
        try:
            positions = await get_positions_async() or []
            self._known_open_tickets = {p.ticket for p in positions}
            for p in positions:
                if not await self._trade_row_exists(p.ticket):
                    await self._insert_open_position(p)
        except Exception as e:
            logger.error("❌ [SYNC] Error sembrando posiciones abiertas: %s", e)

    async def _check_cycle(self):
        positions = await get_positions_async() or []
        current_tickets = {p.ticket for p in positions}

        new_tickets = current_tickets - self._known_open_tickets
        for p in positions:
            if p.ticket in new_tickets:
                await self._insert_open_position(p)

        closed_tickets = self._known_open_tickets - current_tickets
        if closed_tickets:
            await self._reconcile_closed()

        self._known_open_tickets = current_tickets

    async def _insert_open_position(self, p):
        if await self._trade_row_exists(p.ticket):
            return
        # El comment de la posicion es el EA_Name que el propio EA MQL5 pasa a trade.Buy/Sell
        # como comentario -- es literalmente el nombre guardado en MT5, preferible al mapa
        # magic->nombre "amigable" (que puede desincronizarse si se renombra un EA).
        strategy_name = (p.comment or "").strip() or MAGIC_STRATEGY_MAP.get(p.magic) or (f"Manual/otro EA (magic {p.magic})" if p.magic else "Manual")
        trade_type = "BUY" if p.type == 0 else "SELL"  # 0=ORDER_TYPE_BUY, 1=ORDER_TYPE_SELL en MT5
        time_in = datetime.fromtimestamp(p.time).strftime('%Y-%m-%d %H:%M:%S')
        await self.db.save_trade({
            "symbol": p.symbol,
            "type": trade_type,
            "volume": p.volume,
            "price_in": p.price_open,
            "price_out": 0,
            "sl": p.sl,
            "tp": p.tp,
            "profit": p.profit,
            "time_in": time_in,
            "time_out": None,
            "regime_at_entry": None,
            "strategy_name": strategy_name,
            "ticket": p.ticket,
        })
        logger.info("📥 [SYNC] Nueva posición: %s %s (ticket %s, %s)", trade_type, p.symbol, p.ticket, strategy_name)

    async def _trade_row_exists(self, ticket: int) -> bool:
        async with aiosqlite.connect(self.db.db_path, timeout=30) as db:
            async with db.execute("SELECT 1 FROM trades WHERE ticket = ?", (ticket,)) as cur:
                return (await cur.fetchone()) is not None

    async def _reconcile_closed(self):
        try:
            deals = await get_history_deals_async(days=1)
            if deals:
                await self.db.sync_mt5_history(list(deals))
        except Exception as e:
            logger.error("❌ [SYNC] Error reconciliando cierres: %s", e)
