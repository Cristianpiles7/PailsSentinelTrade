from pydantic import BaseModel
from typing import List, Optional, Dict

class AccountStatus(BaseModel):
    balance: float
    equity: float
    margin: float
    margin_free: float
    margin_level: float
    daily_pnl: float
    profit: float
    active_pnl: float

class Trade(BaseModel):
    ticket: int
    symbol: str
    type: str
    volume: float
    price_open: float
    price_current: float
    sl: float
    tp: float
    profit: float
    time_in: str
    strategy: str
    notes: Optional[str] = None
    snapshot_path: Optional[str] = None

class Factor(BaseModel):
    k: str
    v: str
    score: float
    desc: Optional[str] = None

class StrategyBreakdown(BaseModel):
    score: float
    factors: List[Factor]
    is_active: bool = True
    risk_mode: Optional[str] = None
    risk_value: Optional[float] = None
    sl_mult: Optional[float] = None
    tp_mult: Optional[float] = None
    use_breakeven: Optional[bool] = None
    use_trailing: Optional[bool] = None
    be_mult: Optional[float] = None
    ts_mult: Optional[float] = None

class SymbolStatus(BaseModel):
    symbol: str
    is_active: bool
    market_open: Optional[bool] = True
    regime: Optional[str] = "UNKNOWN"
    score: Optional[float] = 0.0
    signal_direction: Optional[str] = "NONE"
    price: Optional[float] = 0.0
    floating_pnl: Optional[float] = 0.0
    total_pnl: Optional[float] = 0.0
    daily_pnl: Optional[float] = 0.0
    profit_24h: Optional[float] = 0.0
    daily_change_pct: Optional[float] = 0.0
    sparkline: Optional[List[float]] = []
    factors: Optional[List[Factor]] = []
    factors_map: Optional[Dict[str, StrategyBreakdown]] = {}
    telemetry: Optional[Dict] = {}
    active_strategy: Optional[str] = "PST-Auto"

class OHLCBar(BaseModel):
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: int

class ConfigUpdate(BaseModel):
    symbol: str
    strategy: Optional[str] = None
    is_active: Optional[bool] = None
    score_threshold: Optional[float] = None
    # Parámetros adicionales soportados por el Matrix Editor
    sl_mult: Optional[float] = None
    tp_mult: Optional[float] = None
    risk_mode: Optional[str] = None
    risk_value: Optional[float] = None
    use_trailing: Optional[bool] = None
    use_breakeven: Optional[bool] = None
    be_mult: Optional[float] = None
    ts_mult: Optional[float] = None
    min_rr: Optional[float] = None

class PerformanceMetrics(BaseModel):
    total_trades: int
    win_rate: float
    profit_factor: float
    max_win_streak: int
    max_loss_streak: int
    gross_profit: float
    gross_loss: float

class EquityPoint(BaseModel):
    time: str
    equity: float

class TradeNoteUpdate(BaseModel):
    ticket: int
    notes: str

class StrategyPerformance(BaseModel):
    strategy: str
    trades_count: int
    profit: float

class BotConfigUpdate(BaseModel):
    key: str
    value: str

class ManualOrder(BaseModel):
    symbol: str
    action: str # "BUY" | "SELL"
    volume: Optional[float] = 0.01
    is_smart: Optional[bool] = False
    risk_amount: Optional[float] = None
    sl_price: Optional[float] = None
    tp_price: Optional[float] = None
    rr_ratio: Optional[float] = None

class LogEntry(BaseModel):
    id: int
    timestamp: str
    level: str
    message: str
    source: str

class APIResponse(BaseModel):
    status: str
    message: Optional[str] = None
    data: Optional[Dict] = None
    logs: Optional[List[LogEntry]] = []

class LoginRequest(BaseModel):
    password: str

class RiskProfileRequest(BaseModel):
    name: str
    config_json: str
