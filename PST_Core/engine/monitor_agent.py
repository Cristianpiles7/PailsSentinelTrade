import asyncio
import logging
from datetime import datetime, date, time as dt_time, timedelta
import MetaTrader5 as mt5
import aiosqlite

logger = logging.getLogger("PST-Monitor")

# ── Umbrales configurables ────────────────────────────────────────────────────
DRAWDOWN_WARN_PCT   = 1.5   # % equity sobre balance → alerta amarilla
DRAWDOWN_CRIT_PCT   = 3.0   # % → alerta roja
CONSEC_LOSSES_WARN  = 3     # Rachas consecutivas → alerta
CONSEC_LOSSES_CRIT  = 5     # Racha crítica
CHECK_INTERVAL_SEC  = 300   # Cada 5 minutos
DAILY_REPORT_HOUR   = 22    # Hora local del reporte automático diario (22:00)


class PSTMonitorAgent:
    """
    Agente de vigilancia continua del bot PST.

    Corre como tarea asyncio en segundo plano y:
    - Vigila drawdown, rachas de pérdidas y conexión MT5.
    - Envía alertas proactivas por Telegram sin intervención del usuario.
    - Genera un reporte diario automático a las 22:00.
    """

    def __init__(self, db, portfolio, telegram_bot):
        self.db = db
        self.portfolio = portfolio
        self.telegram = telegram_bot

        # Estado interno: evita repetir la misma alerta en cada ciclo
        self._alerted_drawdown_level = 0     # 0=nada, 1=warn, 2=crit
        self._alerted_consec_level   = 0
        self._last_report_date: date | None = None
        self._mt5_disconnected_alerted = False

    # ── Loop principal ────────────────────────────────────────────────────────

    async def run(self):
        logger.info("🔍 [MONITOR] Agente de vigilancia iniciado (intervalo: %ds)", CHECK_INTERVAL_SEC)
        while True:
            try:
                await self._check_cycle()
            except Exception as e:
                logger.error("❌ [MONITOR] Error en ciclo: %s", e)
            await asyncio.sleep(CHECK_INTERVAL_SEC)

    # ── Ciclo de comprobación ─────────────────────────────────────────────────

    async def _check_cycle(self):
        now = datetime.now()

        # 1. Conexión MT5
        await self._check_mt5_connection()

        # 2. Drawdown
        await self._check_drawdown()

        # 3. Racha de pérdidas
        await self._check_consecutive_losses()

        # 4. Reporte diario (una sola vez al día a DAILY_REPORT_HOUR)
        if now.hour == DAILY_REPORT_HOUR and self._last_report_date != now.date():
            self._last_report_date = now.date()
            report = await self.build_daily_report()
            await self.telegram.send_message(report)
            logger.info("📋 [MONITOR] Reporte diario enviado.")

    # ── Checks individuales ───────────────────────────────────────────────────

    async def _check_mt5_connection(self):
        acc = mt5.account_info()
        if acc is None:
            if not self._mt5_disconnected_alerted:
                self._mt5_disconnected_alerted = True
                await self.telegram.send_message(
                    "🔌 <b>ALERTA PST: MT5 DESCONECTADO</b>\n"
                    "No se puede obtener información de cuenta.\n"
                    "Verifica que MetaTrader 5 sigue abierto y logueado."
                )
        else:
            if self._mt5_disconnected_alerted:
                self._mt5_disconnected_alerted = False
                await self.telegram.send_message("✅ <b>PST Monitor:</b> Conexión MT5 restaurada.")

    async def _check_drawdown(self):
        try:
            acc = mt5.account_info()
            if acc is None or acc.balance <= 0:
                return

            drawdown_pct = (1 - acc.equity / acc.balance) * 100

            if drawdown_pct >= DRAWDOWN_CRIT_PCT and self._alerted_drawdown_level < 2:
                self._alerted_drawdown_level = 2
                await self.telegram.send_message(
                    f"🔴 <b>ALERTA CRÍTICA — DRAWDOWN</b>\n\n"
                    f"Balance: <b>{acc.balance:.2f}</b>\n"
                    f"Equity:  <b>{acc.equity:.2f}</b>\n"
                    f"Drawdown actual: <b>{drawdown_pct:.2f}%</b> (límite: {DRAWDOWN_CRIT_PCT}%)\n\n"
                    f"⚠️ Considera usar /closeall si la situación empeora."
                )
            elif drawdown_pct >= DRAWDOWN_WARN_PCT and self._alerted_drawdown_level < 1:
                self._alerted_drawdown_level = 1
                await self.telegram.send_message(
                    f"🟡 <b>Aviso — Drawdown elevado</b>\n\n"
                    f"Equity: <b>{acc.equity:.2f}</b> / Balance: {acc.balance:.2f}\n"
                    f"Drawdown: <b>{drawdown_pct:.2f}%</b> (umbral aviso: {DRAWDOWN_WARN_PCT}%)"
                )
            elif drawdown_pct < DRAWDOWN_WARN_PCT * 0.5:
                # Recuperación: resetear estado
                self._alerted_drawdown_level = 0

        except Exception as e:
            logger.error("[MONITOR] Error en check_drawdown: %s", e)

    async def _check_consecutive_losses(self):
        try:
            today_start = datetime.combine(date.today(), dt_time.min).strftime('%Y-%m-%d %H:%M:%S')
            consec = 0

            async with aiosqlite.connect(self.db.db_path) as conn:
                async with conn.execute(
                    "SELECT profit FROM trades WHERE price_out > 0 AND time_out >= ? ORDER BY time_out DESC LIMIT 10",
                    (today_start,)
                ) as cursor:
                    rows = await cursor.fetchall()
                    for row in rows:
                        if row[0] < 0:
                            consec += 1
                        else:
                            break

            if consec >= CONSEC_LOSSES_CRIT and self._alerted_consec_level < 2:
                self._alerted_consec_level = 2
                await self.telegram.send_message(
                    f"🔴 <b>RACHA CRÍTICA DE PÉRDIDAS</b>\n\n"
                    f"<b>{consec} operaciones seguidas en negativo</b> hoy.\n"
                    f"El kill-switch se activará si se alcanza el límite diario.\n"
                    f"Usa /status para revisar posiciones abiertas."
                )
            elif consec >= CONSEC_LOSSES_WARN and self._alerted_consec_level < 1:
                self._alerted_consec_level = 1
                await self.telegram.send_message(
                    f"🟡 <b>Aviso — Racha de pérdidas</b>\n"
                    f"{consec} operaciones negativas consecutivas hoy.\n"
                    f"Vigilando..."
                )
            elif consec < CONSEC_LOSSES_WARN:
                self._alerted_consec_level = 0

        except Exception as e:
            logger.error("[MONITOR] Error en check_consecutive_losses: %s", e)

    # ── Reporte diario ────────────────────────────────────────────────────────

    async def build_daily_report(self) -> str:
        """Construye el texto del reporte diario de operaciones."""
        try:
            today_start = datetime.combine(date.today(), dt_time.min).strftime('%Y-%m-%d %H:%M:%S')

            async with aiosqlite.connect(self.db.db_path) as conn:
                conn.row_factory = aiosqlite.Row

                # Trades del día
                async with conn.execute(
                    "SELECT profit, strategy_name, symbol FROM trades WHERE price_out > 0 AND time_out >= ?",
                    (today_start,)
                ) as cursor:
                    trades = await cursor.fetchall()

            total = len(trades)
            if total == 0:
                return (
                    f"📋 <b>PST — Resumen diario {date.today().strftime('%d/%m/%Y')}</b>\n\n"
                    f"Sin operaciones cerradas hoy."
                )

            wins   = [t['profit'] for t in trades if t['profit'] > 0]
            losses = [t['profit'] for t in trades if t['profit'] < 0]
            net    = sum(t['profit'] for t in trades)
            wr     = (len(wins) / total * 100) if total else 0

            # Mejor y peor estrategia
            strat_pnl: dict[str, float] = {}
            for t in trades:
                s = t['strategy_name'] or 'Manual'
                strat_pnl[s] = strat_pnl.get(s, 0.0) + t['profit']

            best_strat = max(strat_pnl, key=strat_pnl.get) if strat_pnl else '—'
            worst_strat = min(strat_pnl, key=strat_pnl.get) if strat_pnl else '—'

            # Cuenta actual
            acc = mt5.account_info()
            balance_str = f"{acc.balance:.2f}" if acc else "N/A"
            equity_str  = f"{acc.equity:.2f}"  if acc else "N/A"

            sign = "+" if net >= 0 else ""
            emoji = "🟢" if net >= 0 else "🔴"

            return (
                f"📋 <b>PST — Resumen diario {date.today().strftime('%d/%m/%Y')}</b>\n\n"
                f"{emoji} <b>PnL neto:</b> {sign}{net:.2f}\n"
                f"📊 <b>Operaciones:</b> {total} ({len(wins)}W / {len(losses)}L)\n"
                f"🎯 <b>Win rate:</b> {wr:.1f}%\n"
                f"💰 <b>Balance/Equity:</b> {balance_str} / {equity_str}\n\n"
                f"🏆 Mejor estrategia: <b>{best_strat}</b> ({sign}{strat_pnl.get(best_strat, 0):.2f})\n"
                f"📉 Peor estrategia:  <b>{worst_strat}</b> ({strat_pnl.get(worst_strat, 0):.2f})\n\n"
                f"<i>Pails Sentinel Trade · {datetime.now().strftime('%H:%M')}</i>"
            )

        except Exception as e:
            logger.error("[MONITOR] Error construyendo reporte: %s", e)
            return f"❌ Error generando reporte diario: {e}"

    async def build_metrics_snapshot(self) -> str:
        """Snapshot en tiempo real de la cuenta para el comando /metrics."""
        try:
            acc = mt5.account_info()
            if acc is None:
                return "❌ MT5 desconectado. No se pueden obtener métricas."

            positions = mt5.positions_get() or []
            floating  = sum(p.profit for p in positions)
            drawdown  = (1 - acc.equity / acc.balance) * 100 if acc.balance > 0 else 0

            today_start = datetime.combine(date.today(), dt_time.min).strftime('%Y-%m-%d %H:%M:%S')
            closed_today = 0.0
            win_rate_today = 0.0
            async with aiosqlite.connect(self.db.db_path) as conn:
                async with conn.execute(
                    "SELECT profit FROM trades WHERE price_out > 0 AND time_out >= ?",
                    (today_start,)
                ) as cursor:
                    rows = await cursor.fetchall()
                    if rows:
                        profits = [r[0] for r in rows]
                        closed_today = sum(profits)
                        win_rate_today = len([p for p in profits if p > 0]) / len(profits) * 100

            daily_total = closed_today + floating
            dd_emoji = "🟢" if drawdown < DRAWDOWN_WARN_PCT else ("🟡" if drawdown < DRAWDOWN_CRIT_PCT else "🔴")

            return (
                f"<b>📊 PST Métricas en Tiempo Real</b>\n"
                f"─────────────────────────\n"
                f"💰 Balance:      <b>{acc.balance:.2f}</b>\n"
                f"📈 Equity:       <b>{acc.equity:.2f}</b>\n"
                f"📉 Flotante:     <b>{floating:+.2f}</b>\n"
                f"{dd_emoji} Drawdown:    <b>{drawdown:.2f}%</b>\n"
                f"─────────────────────────\n"
                f"🗓️  PnL hoy:      <b>{daily_total:+.2f}</b>\n"
                f"🎯 WR hoy:       <b>{win_rate_today:.1f}%</b>\n"
                f"⚡ Posiciones:   <b>{len(positions)}</b> abiertas\n"
                f"─────────────────────────\n"
                f"<i>{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}</i>"
            )

        except Exception as e:
            return f"❌ Error obteniendo métricas: {e}"
