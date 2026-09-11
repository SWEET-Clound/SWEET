"""A minimal, educational A-share ETF backtest and paper-trading demo."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = BASE_DIR / "runtime"
CACHE_DIR = RUNTIME_DIR / "cache"
REQUIRED_COLUMNS = [
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "adj_close",
    "cash_dividend",
]
JOURNAL_COLUMNS = [
    "as_of_date",
    "signal_date",
    "fill_date",
    "action",
    "qty",
    "exec_price",
    "fee",
    "dividend",
    "cash",
    "shares",
    "close",
    "equity",
    "target_next",
    "symbol",
    "initial_cash",
    "fast",
    "slow",
    "commission_bps",
    "min_commission",
    "slippage_bps",
    "sell_tax_bps",
    "note",
]
TRADE_COLUMNS = [
    "date",
    "signal_date",
    "side",
    "qty",
    "price",
    "notional",
    "fee",
    "status",
    "reason",
]


@dataclass(frozen=True)
class Config:
    symbol: str = "510300"
    initial_cash: float = 100_000.0
    fast: int = 20
    slow: int = 60
    commission_bps: float = 3.0
    min_commission: float = 5.0
    slippage_bps: float = 5.0
    sell_tax_bps: float = 0.0
    lot_size: int = 100

    def validate(self) -> None:
        if self.symbol != "510300":
            raise ValueError("v1 supports only the 510300 ETF")
        if not 0 < self.fast < self.slow:
            raise ValueError("moving averages require 0 < fast < slow")
        if not math.isfinite(self.initial_cash) or self.initial_cash <= 0 or self.lot_size <= 0:
            raise ValueError("initial cash and lot size must be positive")
        costs = (
            self.commission_bps,
            self.min_commission,
            self.slippage_bps,
            self.sell_tax_bps,
        )
        if not all(math.isfinite(value) for value in costs) or min(costs) < 0:
            raise ValueError("fees and slippage must be finite and non-negative")
        if max(self.commission_bps, self.slippage_bps, self.sell_tax_bps) >= 10_000:
            raise ValueError("rates and slippage must be below 10000 bps")


def _parse_date(value: str) -> str:
    return datetime.strptime(value, "%Y-%m-%d").strftime("%Y%m%d")


def _market_symbol(symbol: str) -> str:
    return ("sh" if symbol.startswith(("5", "6")) else "sz") + symbol


def _normalise_history(frame: pd.DataFrame, adjusted: bool) -> pd.DataFrame:
    if frame is None or frame.empty:
        raise ValueError("AKShare returned empty ETF history")
    mapping = {
        "日期": "date",
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "收盘": "adj_close" if adjusted else "close",
        "成交量": "volume",
        "成交额": "amount",
    }
    missing = [name for name in mapping if name not in frame.columns]
    if missing:
        raise ValueError(f"AKShare history columns changed; missing: {missing}")
    result = frame.rename(columns=mapping)[list(mapping.values())].copy()
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    for column in result.columns.drop("date"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    return result.sort_values("date", ignore_index=True)


def _normalise_dividends(frame: pd.DataFrame | None) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["date", "cash_dividend"])
    required = {"日期", "累计分红"}
    if not required.issubset(frame.columns):
        raise ValueError("AKShare dividend columns changed")
    result = frame.rename(columns={"日期": "date", "累计分红": "cumulative"})[
        ["date", "cumulative"]
    ].copy()
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    result["cumulative"] = pd.to_numeric(result["cumulative"], errors="coerce")
    result = result.sort_values("date", ignore_index=True)
    if result["date"].isna().any() or result["cumulative"].isna().any():
        raise ValueError("invalid ETF dividend data")
    if result["date"].duplicated().any():
        raise ValueError("duplicate ETF dividend dates")
    result["cash_dividend"] = result["cumulative"].diff().fillna(result["cumulative"])
    if (result["cash_dividend"] < -1e-9).any():
        raise ValueError("cumulative ETF dividends decreased unexpectedly")
    return result[["date", "cash_dividend"]]


def fetch_market_data(symbol: str, start: str, end: str) -> pd.DataFrame:
    """Fetch raw prices, qfq close, and dividends from zero-key AKShare APIs."""
    Config(symbol=symbol).validate()
    start_api, end_api = _parse_date(start), _parse_date(end)
    if start_api > end_api:
        raise ValueError("start date must not be after end date")

    import akshare as ak

    raw = _normalise_history(
        ak.fund_etf_hist_em(
            symbol=symbol,
            period="daily",
            start_date=start_api,
            end_date=end_api,
            adjust="",
        ),
        adjusted=False,
    )
    adjusted = _normalise_history(
        ak.fund_etf_hist_em(
            symbol=symbol,
            period="daily",
            start_date=start_api,
            end_date=end_api,
            adjust="qfq",
        ),
        adjusted=True,
    )
    if not raw["date"].equals(adjusted["date"]):
        raise ValueError("raw and adjusted ETF histories have different dates")

    dividends = _normalise_dividends(
        ak.fund_etf_dividend_sina(symbol=_market_symbol(symbol))
    )
    if symbol == "510300" and dividends.empty:
        raise ValueError("510300 dividend history is unexpectedly empty")
    in_range_dividends = dividends[
        dividends["date"].between(raw["date"].iloc[0], raw["date"].iloc[-1])
    ]
    unmatched_dividends = in_range_dividends.loc[
        ~in_range_dividends["date"].isin(raw["date"]), "date"
    ]
    if not unmatched_dividends.empty:
        missing_dates = ", ".join(str(value.date()) for value in unmatched_dividends)
        raise ValueError(f"dividend dates are absent from ETF history: {missing_dates}")
    result = raw.merge(adjusted[["date", "adj_close"]], on="date", how="left")
    result = result.merge(dividends, on="date", how="left")
    result["cash_dividend"] = result["cash_dividend"].fillna(0.0)
    return _prepare_bars(result[REQUIRED_COLUMNS])


def validate_bars(frame: pd.DataFrame) -> None:
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"market data is missing columns: {missing}")
    if frame.empty:
        raise ValueError("market data is empty")
    dates = pd.to_datetime(frame["date"], errors="coerce")
    if dates.isna().any() or dates.duplicated().any() or not dates.is_monotonic_increasing:
        raise ValueError("dates must be valid, unique, and increasing")
    numeric = frame[REQUIRED_COLUMNS[1:]].apply(pd.to_numeric, errors="coerce")
    always_required = numeric[["close", "volume", "amount", "adj_close", "cash_dividend"]]
    if always_required.isna().any().any() or not always_required.map(math.isfinite).all().all():
        raise ValueError("market data contains missing or non-finite required values")
    if (numeric[["close", "adj_close"]] <= 0).any().any():
        raise ValueError("close and adjusted close must be positive")
    if (numeric[["volume", "amount", "cash_dividend"]] < 0).any().any():
        raise ValueError("volume, amount, and dividends cannot be negative")
    tradable = numeric["volume"] > 0
    ohlc = numeric.loc[tradable, ["open", "high", "low", "close"]]
    if ohlc.isna().any().any() or not ohlc.map(math.isfinite).all().all() or (ohlc <= 0).any().any():
        raise ValueError("tradable OHLC values must be finite and positive")
    if (
        (ohlc["low"] > ohlc[["open", "close"]].min(axis=1))
        | (ohlc["high"] < ohlc[["open", "close"]].max(axis=1))
        | (ohlc["low"] > ohlc["high"])
    ).any():
        raise ValueError("invalid OHLC relationship")


def _prepare_bars(frame: pd.DataFrame) -> pd.DataFrame:
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"market data is missing columns: {missing}")
    result = frame.copy()
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    for column in REQUIRED_COLUMNS[1:]:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.sort_values("date", ignore_index=True)
    validate_bars(result)
    return result


def make_targets(frame: pd.DataFrame, fast: int, slow: int) -> pd.Series:
    if not 0 < fast < slow:
        raise ValueError("moving averages require 0 < fast < slow")
    if "adj_close" not in frame:
        raise ValueError("adj_close is required for signals")
    fast_ma = frame["adj_close"].rolling(fast, min_periods=fast).mean()
    slow_ma = frame["adj_close"].rolling(slow, min_periods=slow).mean()
    return (fast_ma > slow_ma).where(slow_ma.notna(), False).astype(int)


def _round_price(price: float, side: str) -> float:
    rounding = ROUND_CEILING if side == "BUY" else ROUND_FLOOR
    return float(Decimal(str(price)).quantize(Decimal("0.001"), rounding=rounding))


def _fee(notional: float, side: str, config: Config) -> float:
    if notional <= 0:
        return 0.0
    commission = max(config.min_commission, notional * config.commission_bps / 10_000)
    tax = notional * config.sell_tax_bps / 10_000 if side == "SELL" else 0.0
    return commission + tax


def _blocked(row: pd.Series, previous_close: float, side: str) -> str | None:
    if row["volume"] <= 0 or not math.isfinite(float(row["open"])):
        return "no tradable open/volume"
    one_price = abs(float(row["high"]) - float(row["low"])) < 1e-12
    reference = previous_close - float(row["cash_dividend"])
    change = float(row["open"]) / reference - 1 if reference > 0 else 0
    # ponytail: daily bars can only approximate a locked limit; use official limit
    # fields before extending this demo beyond one liquid broad-market ETF.
    if one_price and side == "BUY" and change >= 0.095:
        return "one-price limit-up approximation"
    if one_price and side == "SELL" and change <= -0.095:
        return "one-price limit-down approximation"
    return None


def _max_buy_qty(cash: float, price: float, config: Config) -> int:
    lots = int(cash // (price * config.lot_size))
    while lots > 0:
        qty = lots * config.lot_size
        notional = qty * price
        if notional + _fee(notional, "BUY", config) <= cash + 1e-9:
            return qty
        lots -= 1
    return 0


def _execute_target(
    row: pd.Series,
    previous_close: float,
    target: int,
    cash: float,
    shares: int,
    config: Config,
    signal_date: pd.Timestamp,
    last_buy_date: pd.Timestamp | None = None,
) -> tuple[float, int, dict | None, pd.Timestamp | None]:
    current = int(shares > 0)
    if target == current:
        return cash, shares, None, last_buy_date
    side = "BUY" if target else "SELL"
    reason = _blocked(row, previous_close, side)
    fill_date = pd.Timestamp(row["date"])
    if side == "SELL" and last_buy_date is not None and fill_date <= last_buy_date:
        reason = "T+1 restriction"
    if reason:
        quoted_open = float(row["open"])
        safe_open = quoted_open if math.isfinite(quoted_open) else 0.0
        return cash, shares, {
            "date": fill_date,
            "signal_date": signal_date,
            "side": side,
            "qty": 0,
            "price": safe_open,
            "notional": 0.0,
            "fee": 0.0,
            "status": "blocked",
            "reason": reason,
        }, last_buy_date

    multiplier = 1 + config.slippage_bps / 10_000 if side == "BUY" else 1 - config.slippage_bps / 10_000
    price = _round_price(float(row["open"]) * multiplier, side)
    qty = _max_buy_qty(cash, price, config) if side == "BUY" else shares
    if qty <= 0:
        return cash, shares, {
            "date": fill_date,
            "signal_date": signal_date,
            "side": side,
            "qty": 0,
            "price": price,
            "notional": 0.0,
            "fee": 0.0,
            "status": "blocked",
            "reason": "insufficient cash for one lot",
        }, last_buy_date

    notional = qty * price
    fee = _fee(notional, side, config)
    if side == "BUY":
        cash -= notional + fee
        shares += qty
        last_buy_date = fill_date
    else:
        cash += notional - fee
        shares = 0
    if cash < -1e-7:
        raise AssertionError("execution produced negative cash")
    cash = max(cash, 0.0)
    return cash, shares, {
        "date": fill_date,
        "signal_date": signal_date,
        "side": side,
        "qty": qty,
        "price": price,
        "notional": notional,
        "fee": fee,
        "status": "filled",
        "reason": "",
    }, last_buy_date


def _simulate(frame: pd.DataFrame, targets: pd.Series, config: Config) -> tuple[pd.DataFrame, pd.DataFrame]:
    cash, shares = config.initial_cash, 0
    last_buy_date = None
    equity_rows, trades = [], []
    for index, row in frame.iterrows():
        dividend = shares * float(row["cash_dividend"])
        cash += dividend
        action = "HOLD"
        if index > 0:
            target = int(targets.iloc[index - 1])
            cash, shares, trade, last_buy_date = _execute_target(
                row,
                float(frame.iloc[index - 1]["close"]),
                target,
                cash,
                shares,
                config,
                pd.Timestamp(frame.iloc[index - 1]["date"]),
                last_buy_date,
            )
            if trade:
                trades.append(trade)
                action = trade["side"] if trade["status"] == "filled" else f"BLOCKED_{trade['side']}"
        equity_rows.append(
            {
                "date": pd.Timestamp(row["date"]),
                "close": float(row["close"]),
                "target": int(targets.iloc[index]),
                "cash": cash,
                "shares": shares,
                "dividend": dividend,
                "equity": cash + shares * float(row["close"]),
                "action": action,
            }
        )
    return pd.DataFrame(equity_rows), pd.DataFrame(trades, columns=TRADE_COLUMNS)


def _metrics(equity: pd.DataFrame, trades: pd.DataFrame) -> dict:
    if equity.empty:
        return {}
    values = equity["equity"].astype(float)
    start, end = float(values.iloc[0]), float(values.iloc[-1])
    days = max((pd.Timestamp(equity["date"].iloc[-1]) - pd.Timestamp(equity["date"].iloc[0])).days, 1)
    years = days / 365.25
    daily = values.pct_change().dropna()
    volatility = float(daily.std(ddof=1) * math.sqrt(252)) if len(daily) > 1 else 0.0
    sharpe = float(daily.mean() / daily.std(ddof=1) * math.sqrt(252)) if len(daily) > 1 and daily.std(ddof=1) > 0 else 0.0
    drawdown = values / values.cummax() - 1
    max_drawdown = float(drawdown.min())
    cagr = float((end / start) ** (1 / years) - 1) if start > 0 and end > 0 else -1.0
    filled = trades[trades["status"] == "filled"] if not trades.empty else trades
    total_notional = float(filled["notional"].sum()) if not filled.empty else 0.0
    total_fees = float(filled["fee"].sum()) if not filled.empty else 0.0
    return {
        "start_date": str(pd.Timestamp(equity["date"].iloc[0]).date()),
        "end_date": str(pd.Timestamp(equity["date"].iloc[-1]).date()),
        "ending_equity": end,
        "total_return": end / start - 1,
        "cagr": cagr,
        "annual_volatility": volatility,
        "sharpe_rf_0": sharpe,
        "max_drawdown": max_drawdown,
        "calmar": cagr / abs(max_drawdown) if max_drawdown < 0 else None,
        "exposure": float((equity["shares"] > 0).mean()),
        "turnover_on_start_equity": total_notional / start if start > 0 else 0.0,
        "trade_count": int(len(filled)),
        "total_fees": total_fees,
    }


def _holdout_metrics(equity: pd.DataFrame, trades: pd.DataFrame, start: str) -> dict:
    """Measure from the prior close while counting activity only from start."""
    start_date = pd.Timestamp(start)
    eligible = equity.index[equity["date"] >= start_date]
    if len(eligible) == 0:
        return {}
    first = int(eligible[0])
    baseline = max(first - 1, 0)
    subset = equity.iloc[baseline:].copy()
    relevant_trades = trades[trades["date"] >= start_date].copy() if not trades.empty else trades
    result = _metrics(subset, relevant_trades)
    result["baseline_date"] = str(pd.Timestamp(subset["date"].iloc[0]).date())
    result["start_date"] = str(pd.Timestamp(equity["date"].iloc[first]).date())
    result["exposure"] = float((equity.iloc[first:]["shares"] > 0).mean())
    return result


def _calmar_score(metrics: dict) -> float:
    """Compare Calmar while keeping undefined zero-drawdown values out of JSON."""
    if metrics.get("calmar") is not None:
        return float(metrics["calmar"])
    return math.inf if metrics.get("cagr", 0.0) > 0 else 0.0


def run_backtest(frame: pd.DataFrame, config: Config) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    config.validate()
    frame = _prepare_bars(frame)
    if len(frame) < config.slow + 2:
        raise ValueError(f"need at least {config.slow + 2} daily bars")

    targets = make_targets(frame, config.fast, config.slow)
    strategy_equity, strategy_trades = _simulate(frame, targets, config)
    benchmark_targets = pd.Series(0, index=frame.index, dtype=int)
    benchmark_targets.iloc[config.slow - 1 :] = 1
    benchmark_equity, benchmark_trades = _simulate(frame, benchmark_targets, config)
    stress = replace(
        config,
        commission_bps=config.commission_bps * 2,
        min_commission=config.min_commission * 2,
        slippage_bps=max(config.slippage_bps, 20.0),
    )
    stress_equity, stress_trades = _simulate(frame, targets, stress)

    strategy_full = _metrics(strategy_equity, strategy_trades)
    benchmark_full = _metrics(benchmark_equity, benchmark_trades)
    strategy_holdout = _holdout_metrics(strategy_equity, strategy_trades, "2023-01-01")
    benchmark_holdout = _holdout_metrics(benchmark_equity, benchmark_trades, "2023-01-01")
    stress_full = _metrics(stress_equity, stress_trades)
    stress_holdout = _holdout_metrics(stress_equity, stress_trades, "2023-01-01")
    benchmark_dd = abs(benchmark_holdout.get("max_drawdown", 0.0))
    drawdown_reduction = (
        1 - abs(strategy_holdout.get("max_drawdown", 0.0)) / benchmark_dd
        if benchmark_dd > 0
        else 0.0
    )
    first_date = pd.Timestamp(frame["date"].iloc[0])
    pre_holdout_rows = int((frame["date"] < pd.Timestamp("2023-01-01")).sum())
    holdout_rows = int((frame["date"] >= pd.Timestamp("2023-01-01")).sum())
    eligibility = {
        "history_starts_in_january_2013": first_date.year == 2013 and first_date.month == 1,
        "pre_holdout_rows_at_least_slow_plus_2": pre_holdout_rows >= config.slow + 2,
        "holdout_rows_at_least_252": holdout_rows >= 252,
    }
    eligibility["eligible"] = all(eligibility.values())
    gate_checks = {
        "holdout_positive": strategy_holdout.get("total_return", -1) > 0,
        "holdout_max_drawdown_at_most_25pct": strategy_holdout.get("max_drawdown", -1) >= -0.25,
        "holdout_drawdown_reduction_at_least_20pct": drawdown_reduction >= 0.20,
        "holdout_calmar_not_below_benchmark": _calmar_score(strategy_holdout)
        >= _calmar_score(benchmark_holdout),
        "stress_holdout_positive": stress_holdout.get("total_return", -1) > 0,
    }
    holdout_gates_passed = eligibility["eligible"] and all(gate_checks.values())

    equity = strategy_equity.rename(
        columns={
            "target": "strategy_target",
            "cash": "strategy_cash",
            "shares": "strategy_shares",
            "dividend": "strategy_dividend",
            "equity": "strategy_equity",
            "action": "strategy_action",
        }
    )
    equity["benchmark_equity"] = benchmark_equity["equity"]
    equity["strategy_drawdown"] = equity["strategy_equity"] / equity["strategy_equity"].cummax() - 1
    equity["benchmark_drawdown"] = equity["benchmark_equity"] / equity["benchmark_equity"].cummax() - 1
    summary = {
        "data": {
            "rows": len(frame),
            "first_date": str(pd.Timestamp(frame["date"].iloc[0]).date()),
            "last_date": str(pd.Timestamp(frame["date"].iloc[-1]).date()),
            "holdout_start": "2023-01-01",
        },
        "config": asdict(config),
        "strategy": {"full": strategy_full, "holdout": strategy_holdout},
        "buy_and_hold": {"full": benchmark_full, "holdout": benchmark_holdout},
        "stress": {"config": asdict(stress), "full": stress_full, "holdout": stress_holdout},
        "gates": {
            "eligibility": eligibility,
            "drawdown_reduction": drawdown_reduction,
            **gate_checks,
            "holdout_gates_passed": holdout_gates_passed,
        },
    }
    return equity, strategy_trades, summary


def run_robustness(frame: pd.DataFrame, config: Config) -> tuple[pd.DataFrame, dict]:
    """Report a fixed SMA neighbourhood on pre-2023 data without selecting a winner."""
    config.validate()
    if "date" not in frame:
        raise ValueError("market data is missing columns: ['date']")
    dates = pd.to_datetime(frame["date"], errors="coerce")
    if dates.isna().any():
        raise ValueError("dates must be valid before isolating robustness training data")
    training = frame.loc[dates < pd.Timestamp("2023-01-01")].copy()
    training["date"] = dates[dates < pd.Timestamp("2023-01-01")]
    training = _prepare_bars(training)
    if len(training) < 72:
        raise ValueError("robustness needs at least 72 pre-2023 daily bars")
    common_baseline = max(50, 60, 70) - 1
    comparison_frame = training.iloc[common_baseline:].reset_index(drop=True)
    rows = []
    for fast in (15, 20, 25):
        for slow in (50, 60, 70):
            candidate = replace(config, fast=fast, slow=slow)
            targets = make_targets(training, fast, slow).iloc[common_baseline:].reset_index(drop=True)
            equity, trades = _simulate(comparison_frame, targets, candidate)
            metrics = _metrics(equity, trades)
            rows.append(
                {
                    "fast": fast,
                    "slow": slow,
                    "is_baseline": fast == 20 and slow == 60,
                    "total_return": metrics["total_return"],
                    "max_drawdown": metrics["max_drawdown"],
                    "calmar": metrics["calmar"],
                    "exposure": metrics["exposure"],
                    "trade_count": metrics["trade_count"],
                    "total_fees": metrics["total_fees"],
                }
            )
    results = pd.DataFrame(rows)
    measured = ["total_return", "max_drawdown", "calmar", "exposure", "trade_count", "total_fees"]
    distribution = {}
    for column in measured:
        values = pd.to_numeric(results[column], errors="coerce").dropna()
        distribution[column] = (
            {
                "min": float(values.min()),
                "median": float(values.median()),
                "max": float(values.max()),
            }
            if not values.empty
            else {"min": None, "median": None, "max": None}
        )
    summary = {
        "scope": {
            "data_first_date": str(pd.Timestamp(training["date"].iloc[0]).date()),
            "common_baseline_date": str(pd.Timestamp(comparison_frame["date"].iloc[0]).date()),
            "last_date": str(pd.Timestamp(comparison_frame["date"].iloc[-1]).date()),
            "holdout_examined": False,
            "selection_performed": False,
        },
        "config": asdict(config),
        "grid": {"fast": [15, 20, 25], "slow": [50, 60, 70]},
        "distribution": distribution,
    }
    return results, summary


def _journal_row(
    config: Config,
    as_of: pd.Timestamp,
    action: str,
    cash: float,
    shares: int,
    close: float,
    target_next: int,
    *,
    signal_date: pd.Timestamp | None = None,
    fill_date: pd.Timestamp | None = None,
    qty: int = 0,
    exec_price: float = 0.0,
    fee: float = 0.0,
    dividend: float = 0.0,
    note: str = "",
) -> dict:
    return {
        "as_of_date": str(as_of.date()),
        "signal_date": str((signal_date or as_of).date()),
        "fill_date": str(fill_date.date()) if fill_date is not None else "",
        "action": action,
        "qty": qty,
        "exec_price": exec_price,
        "fee": fee,
        "dividend": dividend,
        "cash": cash,
        "shares": shares,
        "close": close,
        "equity": cash + shares * close,
        "target_next": target_next,
        "symbol": config.symbol,
        "initial_cash": config.initial_cash,
        "fast": config.fast,
        "slow": config.slow,
        "commission_bps": config.commission_bps,
        "min_commission": config.min_commission,
        "slippage_bps": config.slippage_bps,
        "sell_tax_bps": config.sell_tax_bps,
        "note": note,
    }


def _assert_same_config(last: pd.Series, config: Config) -> None:
    expected = {
        "symbol": config.symbol,
        "initial_cash": config.initial_cash,
        "fast": config.fast,
        "slow": config.slow,
        "commission_bps": config.commission_bps,
        "min_commission": config.min_commission,
        "slippage_bps": config.slippage_bps,
        "sell_tax_bps": config.sell_tax_bps,
    }
    for key, value in expected.items():
        actual = last[key]
        equal = str(actual) == value if isinstance(value, str) else math.isclose(float(actual), float(value))
        if not equal:
            raise ValueError(f"paper journal config mismatch: {key}")


def paper_step(frame: pd.DataFrame, journal_path: Path, config: Config) -> dict:
    config.validate()
    frame = _prepare_bars(frame)
    if len(frame) < config.slow:
        raise ValueError(f"need at least {config.slow} daily bars for paper trading")
    targets = make_targets(frame, config.fast, config.slow)
    latest = pd.Timestamp(frame["date"].iloc[-1])

    if not journal_path.exists():
        row = _journal_row(
            config,
            latest,
            "INIT",
            config.initial_cash,
            0,
            float(frame["close"].iloc[-1]),
            int(targets.iloc[-1]),
            note="first run: signal created, no historical fill invented",
        )
        _write_dataframe(pd.DataFrame([row], columns=JOURNAL_COLUMNS), journal_path)
        return {"status": "initialized", **row}

    journal = pd.read_csv(journal_path, keep_default_na=False)
    missing = [column for column in JOURNAL_COLUMNS if column not in journal.columns]
    if journal.empty or missing:
        raise ValueError(f"invalid paper journal; missing columns: {missing}")
    last = journal.iloc[-1]
    _assert_same_config(last, config)
    last_date = pd.Timestamp(last["as_of_date"])
    if latest < last_date:
        raise ValueError("market data moved backwards relative to the paper journal")
    if latest == last_date:
        return {"status": "no-op", "as_of_date": str(latest.date()), "reason": "no new daily bar"}

    new_bars = frame[frame["date"] > last_date]
    first = new_bars.iloc[0]
    cash, shares = float(last["cash"]), int(last["shares"])
    total_dividend = shares * float(new_bars["cash_dividend"].sum())
    cash += total_dividend
    previous_rows = frame[frame["date"] == last_date]
    if previous_rows.empty:
        raise ValueError("paper journal date is absent from market data")
    action = "HOLD"
    qty, exec_price, fee, fill_date, note = 0, 0.0, 0.0, None, ""
    if len(new_bars) > 1:
        action = "MISSED_WINDOW"
        note = f"{len(new_bars)} new bars; expired pending order was not filled historically"
    else:
        previous_close = float(previous_rows.iloc[-1]["close"])
        pending = int(last["target_next"])
        last_buy_date = pd.Timestamp(last["fill_date"]) if last["action"] == "BUY" and last["fill_date"] else None
        cash, shares, trade, _ = _execute_target(
            first,
            previous_close,
            pending,
            cash,
            shares,
            config,
            last_date,
            last_buy_date,
        )
        if trade:
            action = trade["side"] if trade["status"] == "filled" else f"BLOCKED_{trade['side']}"
            qty, exec_price, fee = int(trade["qty"]), float(trade["price"]), float(trade["fee"])
            fill_date = pd.Timestamp(trade["date"])
            note = trade["reason"]
    row = _journal_row(
        config,
        latest,
        action,
        cash,
        shares,
        float(frame["close"].iloc[-1]),
        int(targets.iloc[-1]),
        signal_date=last_date,
        fill_date=fill_date,
        qty=qty,
        exec_price=exec_price,
        fee=fee,
        dividend=total_dividend,
        note=note,
    )
    journal = pd.concat([journal, pd.DataFrame([row])], ignore_index=True)
    _write_dataframe(journal[JOURNAL_COLUMNS], journal_path)
    return {"status": "updated", **row}


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _write_dataframe(frame: pd.DataFrame, path: Path) -> None:
    _atomic_write(path, frame.to_csv(index=False, date_format="%Y-%m-%d", lineterminator="\n"))


def _cache_paths(symbol: str, cache_root: Path = CACHE_DIR) -> tuple[Path, Path]:
    directory = cache_root / symbol
    return directory, directory / "current.json"


def _save_cache(
    frame: pd.DataFrame,
    symbol: str,
    start: str,
    end: str,
    *,
    source_version: str | None = None,
    cache_root: Path = CACHE_DIR,
) -> tuple[Path, Path]:
    if source_version is None:
        import akshare as ak

        source_version = ak.__version__
    cache_directory, pointer_path = _cache_paths(symbol, cache_root)
    csv_text = frame.to_csv(index=False, date_format="%Y-%m-%d", lineterminator="\n")
    digest = hashlib.sha256(csv_text.encode("utf-8")).hexdigest()
    csv_path = cache_directory / f"{digest}.csv"
    metadata = {
        "source_library": "AKShare",
        "source_endpoints": ["fund_etf_hist_em", "fund_etf_dividend_sina"],
        "upstreams": ["Eastmoney", "Sina Finance"],
        "symbol": symbol,
        "signal_adjustment": "qfq",
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "akshare_version": source_version,
        "requested_start": start,
        "requested_end": end,
        "first_trade_date": str(pd.Timestamp(frame["date"].iloc[0]).date()),
        "last_trade_date": str(pd.Timestamp(frame["date"].iloc[-1]).date()),
        "row_count": len(frame),
        "data_file": csv_path.name,
        "sha256": digest,
    }
    if not csv_path.exists() or hashlib.sha256(csv_path.read_bytes()).hexdigest() != digest:
        _atomic_write(csv_path, csv_text)
    _atomic_write(pointer_path, json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    return csv_path, pointer_path


def _load_cache(symbol: str, cache_root: Path = CACHE_DIR) -> tuple[pd.DataFrame, dict]:
    cache_directory, pointer_path = _cache_paths(symbol, cache_root)
    if not pointer_path.exists():
        raise FileNotFoundError(f"cache not found; run fetch first: {pointer_path}")
    metadata = json.loads(pointer_path.read_text(encoding="utf-8"))
    required_metadata = {"data_file", "fetched_at_utc", "last_trade_date", "sha256"}
    if not required_metadata.issubset(metadata):
        raise ValueError("cache metadata is incomplete")
    data_file = str(metadata["data_file"])
    if Path(data_file).name != data_file:
        raise ValueError("cache metadata contains an invalid data filename")
    csv_path = cache_directory / data_file
    if not csv_path.exists():
        raise FileNotFoundError(f"cache snapshot is missing: {csv_path}")
    csv_bytes = csv_path.read_bytes()
    if hashlib.sha256(csv_bytes).hexdigest() != metadata.get("sha256"):
        raise ValueError("cache checksum does not match its metadata")
    frame = pd.read_csv(csv_path, parse_dates=["date"])
    return _prepare_bars(frame), metadata


def _ensure_completed_latest(frame: pd.DataFrame) -> None:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    latest = pd.Timestamp(frame["date"].iloc[-1]).date()
    age = (now.date() - latest).days
    if age < 0:
        raise ValueError("latest daily bar is dated in the future")
    if age > 10:
        raise ValueError(f"latest daily bar is {age} calendar days old")
    if latest == now.date() and now.time() < time(15, 30):
        raise ValueError("today's daily bar is not considered complete before 15:30 Asia/Shanghai")


def _make_config(args: argparse.Namespace) -> Config:
    return Config(
        symbol=args.symbol,
        initial_cash=args.initial_cash,
        fast=getattr(args, "fast", 20),
        slow=getattr(args, "slow", 60),
        commission_bps=args.commission_bps,
        min_commission=args.min_commission,
        slippage_bps=args.slippage_bps,
        sell_tax_bps=args.sell_tax_bps,
    )


def _config_tag(config: Config) -> str:
    payload = json.dumps(asdict(config), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:10]


def _self_check_frame() -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=8)
    adjusted = [10.0, 10.0, 10.0, 12.0, 13.0, 8.0, 7.0, 7.0]
    raw_close = [10.0, 10.0, 10.0, 12.0, 12.0, 8.0, 7.0, 7.0]
    return pd.DataFrame(
        {
            "date": dates,
            "open": raw_close,
            "high": [value + 0.2 for value in raw_close],
            "low": [value - 0.2 for value in raw_close],
            "close": raw_close,
            "volume": [10_000] * len(dates),
            "amount": [100_000] * len(dates),
            "adj_close": adjusted,
            "cash_dividend": [0, 0, 0, 0, 1, 0, 0, 0],
        }
    )


def self_check() -> None:
    frame = _self_check_frame()
    config = Config(symbol="510300", initial_cash=2_000, fast=2, slow=3)
    validate_bars(frame)
    targets = make_targets(frame, config.fast, config.slow)
    assert targets.iloc[:2].eq(0).all(), "slow MA warm-up traded too early"
    changed_raw = frame.copy()
    changed_raw.loc[4, "close"] = 11.5
    changed_raw.loc[4, "low"] = 11.3
    assert make_targets(changed_raw, 2, 3).equals(targets), "raw ex-dividend price affected signals"

    equity, trades, _ = run_backtest(frame, config)
    filled = trades[trades["status"] == "filled"]
    assert not filled.empty and (filled["date"] > filled["signal_date"]).all(), "same-day signal fill"
    assert (filled["qty"] % config.lot_size == 0).all(), "non-lot trade"
    assert (equity["strategy_cash"] >= -1e-9).all(), "negative cash"
    zero_cost = replace(config, commission_bps=0, min_commission=0, slippage_bps=0)
    free_equity, _, _ = run_backtest(frame, zero_cost)
    assert equity["strategy_equity"].iloc[-1] < free_equity["strategy_equity"].iloc[-1], "costs had no effect"
    locked = pd.Series({"open": 11.0, "high": 11.0, "low": 11.0, "volume": 1, "cash_dividend": 0})
    suspended = locked.copy()
    suspended[["open", "high", "low"]] = math.nan
    suspended["volume"] = 0
    assert _blocked(locked, 10.0, "BUY") and _blocked(suspended, 10.0, "BUY"), "blocked bars traded"
    _, _, suspended_trade, _ = _execute_target(
        pd.concat([suspended, pd.Series({"date": frame["date"].iloc[1]})]),
        10.0,
        1,
        config.initial_cash,
        0,
        config,
        frame["date"].iloc[0],
    )
    assert suspended_trade and suspended_trade["price"] == 0.0, "blocked trade emitted non-finite price"
    ex_dividend_limit = locked.copy()
    ex_dividend_limit[["open", "high", "low", "cash_dividend"]] = [9.9, 9.9, 9.9, 1.0]
    assert _blocked(ex_dividend_limit, 10.0, "BUY"), "ex-dividend limit reference was wrong"
    limit_down = locked.copy()
    limit_down[["open", "high", "low"]] = [9.0, 9.0, 9.0]
    assert _blocked(limit_down, 10.0, "SELL"), "one-price limit-down sale was not blocked"

    string_frame = frame.astype({column: "string" for column in REQUIRED_COLUMNS})
    string_equity, _, _ = run_backtest(string_frame, config)
    assert len(string_equity) == len(frame), "string input was not normalised"
    infinite = frame.copy()
    infinite["amount"] = infinite["amount"].astype(float)
    infinite.loc[0, "amount"] = math.inf
    try:
        validate_bars(infinite)
    except ValueError as error:
        assert "non-finite" in str(error)
    else:
        raise AssertionError("infinite market data was accepted")
    _, empty_trades = _simulate(frame, pd.Series(0, index=frame.index), config)
    assert list(empty_trades.columns) == TRADE_COLUMNS, "empty trade output lost its schema"
    boundary_equity = pd.DataFrame(
        {
            "date": pd.to_datetime(["2022-12-30", "2023-01-03", "2023-01-04"]),
            "equity": [100.0, 110.0, 121.0],
            "shares": [0, 0, 0],
        }
    )
    boundary = _holdout_metrics(boundary_equity, empty_trades, "2023-01-01")
    assert math.isclose(boundary["total_return"], 0.21) and boundary["baseline_date"] == "2022-12-30"
    rising_metrics = _metrics(boundary_equity, empty_trades)
    assert rising_metrics["calmar"] is None and math.isinf(_calmar_score(rising_metrics))

    short_frame = pd.concat([frame] * 8, ignore_index=True)
    short_frame["date"] = pd.bdate_range("2025-01-02", periods=len(short_frame))
    short_summary = run_backtest(short_frame, config)[2]
    assert not short_summary["gates"]["eligibility"]["eligible"]
    assert not short_summary["gates"]["holdout_gates_passed"]
    high_slippage = run_backtest(frame, replace(config, slippage_bps=30))[2]
    assert high_slippage["stress"]["config"]["slippage_bps"] == 30

    with tempfile.TemporaryDirectory() as directory:
        journal = Path(directory) / "paper.csv"
        paper_step(frame.iloc[:5], journal, config)
        paper_step(frame.iloc[:6], journal, config)
        rows = len(pd.read_csv(journal))
        assert paper_step(frame.iloc[:6], journal, config)["status"] == "no-op"
        assert len(pd.read_csv(journal)) == rows, "same daily bar duplicated the journal"
        missing_journal_date = frame.drop(index=5).reset_index(drop=True)
        try:
            paper_step(missing_journal_date, journal, config)
        except ValueError as error:
            assert "journal date is absent" in str(error)
        else:
            raise AssertionError("paper advanced after its journal date disappeared from data")
        try:
            paper_step(frame.iloc[:6], journal, replace(config, fast=1))
        except ValueError as error:
            assert "config mismatch" in str(error)
        else:
            raise AssertionError("paper journal accepted changed parameters")

        gap_journal = Path(directory) / "paper_gap.csv"
        paper_step(frame.iloc[:4], gap_journal, config)
        result = paper_step(frame, gap_journal, config)
        gap_rows = pd.read_csv(gap_journal)
        assert len(gap_rows) == 2 and result["action"] == "MISSED_WINDOW"
        assert result["fill_date"] == "" and result["shares"] == 0
        assert "was not filled historically" in result["note"]

        cache_root = Path(directory) / "cache"
        snapshot, pointer = _save_cache(
            frame,
            config.symbol,
            "2024-01-02",
            "2024-01-11",
            source_version="self-check",
            cache_root=cache_root,
        )
        loaded, metadata = _load_cache(config.symbol, cache_root)
        assert snapshot.exists() and pointer.exists() and len(loaded) == len(frame)
        assert metadata["data_file"] == snapshot.name and metadata["akshare_version"] == "self-check"

    robust_frame = pd.DataFrame(
        {
            "date": pd.bdate_range("2022-01-03", periods=100),
            "open": [10 + index / 100 for index in range(100)],
            "high": [10.1 + index / 100 for index in range(100)],
            "low": [9.9 + index / 100 for index in range(100)],
            "close": [10 + index / 100 for index in range(100)],
            "volume": [10_000] * 100,
            "amount": [100_000] * 100,
            "adj_close": [10 + index / 100 for index in range(100)],
            "cash_dividend": [0] * 100,
        }
    )
    poisoned_holdout = robust_frame.iloc[[-1]].copy()
    poisoned_holdout["date"] = pd.Timestamp("2023-01-03")
    poisoned_holdout["amount"] = math.inf
    robustness_input = pd.concat([robust_frame, poisoned_holdout], ignore_index=True)
    robustness, robustness_summary = run_robustness(robustness_input, config)
    assert len(robustness) == 9 and not robustness_summary["scope"]["holdout_examined"]
    assert robustness_summary["config"]["initial_cash"] == config.initial_cash
    print("self-check: OK")


def _add_strategy_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--symbol", default="510300")
    parser.add_argument("--initial-cash", type=float, default=100_000)
    parser.add_argument("--commission-bps", type=float, default=3)
    parser.add_argument("--min-commission", type=float, default=5)
    parser.add_argument("--slippage-bps", type=float, default=5)
    parser.add_argument("--sell-tax-bps", type=float, default=0)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Educational 510300 ETF quant demo; never sends real orders")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("self-check", help="run deterministic offline assertions")

    fetch = commands.add_parser("fetch", help="download and atomically cache ETF daily data")
    fetch.add_argument("--symbol", default="510300")
    fetch.add_argument("--start", default="2013-01-01")
    fetch.add_argument("--end", default=str(date.today()))

    backtest = commands.add_parser("backtest", help="backtest from the existing local cache")
    _add_strategy_args(backtest)

    robustness = commands.add_parser(
        "robustness",
        help="report the fixed pre-2023 SMA neighbourhood without choosing a winner",
    )
    _add_strategy_args(robustness)

    paper = commands.add_parser("paper", help="refresh online data and advance the paper journal once")
    _add_strategy_args(paper)
    paper.add_argument("--start", default="2013-01-01")
    paper.add_argument("--end", default=str(date.today()))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "self-check":
            self_check()
            return 0
        if args.command == "fetch":
            frame = fetch_market_data(args.symbol, args.start, args.end)
            _ensure_completed_latest(frame)
            csv_path, meta_path = _save_cache(frame, args.symbol, args.start, args.end)
            print(f"cached {len(frame)} rows through {frame['date'].iloc[-1].date()}")
            print(csv_path)
            print(meta_path)
            return 0

        config = _make_config(args)
        config.validate()
        if args.command in {"backtest", "robustness"}:
            frame, metadata = _load_cache(config.symbol)
            data_tag = str(metadata["sha256"])[:10]
            if args.command == "robustness":
                results, summary = run_robustness(frame, config)
                summary["input_cache_sha256"] = metadata["sha256"]
                output = RUNTIME_DIR / f"robustness_{config.symbol}_{_config_tag(config)}_{data_tag}"
                _write_dataframe(results, output / "grid.csv")
                _atomic_write(output / "summary.json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
                print(f"cache fetched at {metadata['fetched_at_utc']}; data through {metadata['last_trade_date']}")
                print(json.dumps(summary, ensure_ascii=False, indent=2))
                return 0
            equity, trades, summary = run_backtest(frame, config)
            summary["input_cache_sha256"] = metadata["sha256"]
            output = RUNTIME_DIR / (
                f"backtest_{config.symbol}_{config.fast}_{config.slow}_{_config_tag(config)}_{data_tag}"
            )
            _write_dataframe(equity, output / "equity.csv")
            _write_dataframe(trades, output / "trades.csv")
            _atomic_write(output / "summary.json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
            print(f"cache fetched at {metadata['fetched_at_utc']}; data through {metadata['last_trade_date']}")
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0

        frame = fetch_market_data(config.symbol, args.start, args.end)
        _ensure_completed_latest(frame)
        _save_cache(frame, config.symbol, args.start, args.end)
        journal = RUNTIME_DIR / f"paper_{config.symbol}_{config.fast}_{config.slow}.csv"
        result = paper_step(frame, journal, config)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print(journal)
        return 0
    except (AssertionError, FileNotFoundError, ImportError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
