from dataclasses import dataclass

@dataclass(frozen=True)
class Settings:
    BOT_TAG: str = "5x5i-A"

    # -------------------------------------------------
    # Scanner
    # -------------------------------------------------
    SCAN_INTERVAL_SECONDS: int = 60
    CANDLE_LIMIT: int = 200

    # -------------------------------------------------
    # Timeframes
    # -------------------------------------------------
    TF_1H: str = "1h"
    TF_4H: str = "4h"
    TF_1D: str = "1d"

    # -------------------------------------------------
    # Signal Source / Files
    # -------------------------------------------------
    SIGNALS_RAW: str = "data/signals/signals_raw.csv"
    SIGNALS_CONFIRMED: str = "data/signals/signals_confirmed.csv"

    # ✅ NEU: Vollstaendiges Scanner-Eval-Log (JSONL)
    SCANNER_EVAL_LOG: str = "data_5x5iA/scanner_eval_5x5iA.jsonl"
    # Eval Logs (Scanner Forensik)
    SCANNER_EVAL_DIR: str = "data_5x5iA"  # wird relativ zum Working-Dir genutzt
    SCANNER_EVAL_ROLL_DAILY: bool = True  # Option A
    
    # Option C: Split FULL + FOCUS
    SCANNER_EVAL_FULL_PREFIX: str = "scanner_eval_5x5iA_full"
    SCANNER_EVAL_FOCUS_PREFIX: str = "scanner_eval_5x5iA_focus"
    
    # Option B: Focus Filter (nur für FOCUS-Log)
    SCANNER_EVAL_FOCUS_MIN_SCORE: int = 3
    SCANNER_EVAL_FOCUS_ONLY_SIGNALS: bool = False  # True => nur LONG/SHORT
    
    # Optional: Vortag automatisch gzippen
    SCANNER_EVAL_GZIP_YESTERDAY: bool = True

    # Papertrader reads confirmed signals by default
    SIGNAL_CSV: str = SIGNALS_CONFIRMED
    SIGNALS_CSV: str = SIGNALS_CONFIRMED  # compatibility alias

    # -------------------------------------------------
    # Proof Trading
    # -------------------------------------------------
    START_BALANCE_USDT: float = 10000.0
    RISK_PCT: float = 0.01
    MAX_OPEN_TRADES: int = 3
    ONE_POSITION_PER_SYMBOL: bool = True

    # -------------------------------------------------
    # Execution realism
    # -------------------------------------------------
    SLIPPAGE: float = 0.0005   # 0.05%
    FEE_PER_SIDE: float = 0.0

    # Stop buffer
    SL_BUFFER: float = 0.001  # 0.10%

    # -------------------------------------------------
    # Loop
    # -------------------------------------------------
    PAPER_LOOP_SECONDS: int = 45
    PT_LOOP_SLEEP_SEC: float = float(PAPER_LOOP_SECONDS)  # compatibility alias


SETTINGS = Settings()
