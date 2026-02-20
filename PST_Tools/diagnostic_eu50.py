import asyncio
import MetaTrader5 as mt5
import pandas as pd
import pandas_ta as ta
import logging

from PST_Core.engine.mt5_async import init_mt5_async, fetch_rates_async, sym_info_async, get_positions_async
from PST_Core.models.database import PSTDatabase
from PST_Core.portfolio.manager import PortfolioManager
from PST_Core.strategies.pst_ema_flow import PSTEMAFlow
from PST_Core.models.classifier import RegimeClassifier, RegimeMode

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Diagnostic")

async def run_diagnostic():
    await init_mt5_async()
    db = PSTDatabase()
    await db.initialize()
    portfolio = PortfolioManager(db=db)
    strat = PSTEMAFlow()
    classifier = RegimeClassifier()
    
    symbol = "EU50.cash"
    logger.info(f"--- DIAGNOSTIC FOR {symbol} ---")
    
    # 1. Market Open Check
    from datetime import datetime
    now = datetime.now()
    is_market_open = True
    current_hour = now.hour + (now.minute / 60)
    if now.weekday() >= 5: is_market_open = False
    elif not (8.0 <= current_hour < 22.0): is_market_open = False
    
    logger.info(f"Market Open (Logic): {is_market_open} (Hour: {current_hour:.2f}, WDay: {now.weekday()})")
    
    # 2. MT5 Info
    mt5.symbol_select(symbol, True)
    s_info = await sym_info_async(symbol)
    if not s_info:
        logger.error("Could not fetch symbol info")
        return
    logger.info(f"MT5 Symbol Info: Ask: {s_info.ask}, Bid: {s_info.bid}, Point: {s_info.point}, Digits: {s_info.digits}")
    
    # 3. Data & Regime
    df_m5 = await fetch_rates_async(symbol, 5, 100)
    df_m15 = await fetch_rates_async(symbol, 15, 100)
    df_h1 = await fetch_rates_async(symbol, 60, 100)
    
    if df_m5 is not None:
        mode, adx = classifier.classify(df_m5)
        logger.info(f"Regime: {mode} (ADX: {adx:.2f})")
    
    # 4. Strategy Signal
    data_input = {
        'symbol': symbol,
        'm5': df_m5,
        'm15': df_m15,
        'h1': df_h1
    }
    s_result = await strat.calculate_signal(data_input, mode)
    logger.info(f"Strategy Entry: {s_result.get('entry')}")
    logger.info(f"Strategy Score: {s_result.get('score')}")
    
    # 5. Portfolio Check
    trading_mode = await db.get_config('trading_mode', 'AUTO')
    logger.info(f"Trading Mode (DB): {trading_mode}")
    
    is_locked = await portfolio.is_daily_locked()
    logger.info(f"Daily Locked (DB): {is_locked}")
    
    open_positions = await get_positions_async()
    can_trade = await portfolio.can_open_trade(symbol, "BUY" if s_result.get('entry') == 1 else "SELL", open_positions)
    logger.info(f"Portfolio Can Trade: {can_trade}")
    
    # 6. Lot Calculation
    if s_result.get('entry') != 0:
        acc = await portfolio.get_account_status()
        atr_val = s_result.get('atr', 0)
        sl_points = (atr_val * 3.5) / s_info.point # Using INDEX multiplier
        
        lot = portfolio.calculate_lot_size(
            acc['balance'], 
            0.5, # Manual Risk 0.5%
            sl_points,
            s_info,
            current_atr=atr_val,
            ma_atr=atr_val, # Simple fallback
            open_positions_count=len(open_positions) if open_positions else 0
        )
        logger.info(f"Calculated Lot: {lot}")

    mt5.shutdown()

if __name__ == "__main__":
    asyncio.run(run_diagnostic())
