"""Fetch, normalize, audit and apply NSE/BSE corporate actions."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import date
from io import StringIO
import json
from pathlib import Path
import re
from threading import Lock
from typing import Iterable, List, Optional

import aiohttp
import pandas as pd

from .symbol_history import SymbolHistoryStore


SPLIT_PATTERN = re.compile(r"(\d+\.?\d*)[\/\- a-z\.]+(\d+\.?\d*)", re.I)
BONUS_PATTERN = re.compile(r"(\d+)\s*:\s*(\d+)")


def parse_split_factor(subject: str) -> Optional[float]:
    match = SPLIT_PATTERN.search(str(subject).lower())
    if match is None:
        return None
    try:
        return float(match.group(1)) / float(match.group(2))
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def parse_bonus_factor(subject: str) -> Optional[float]:
    match = BONUS_PATTERN.search(str(subject).lower())
    if match is None:
        return None
    try:
        denominator = int(match.group(2))
        return 1 + int(match.group(1)) / denominator if denominator else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None


@dataclass(frozen=True)
class CorporateAction:
    exchange: str
    symbol: str
    stable_id: str
    ex_date: date
    action_type: str
    factor: float
    description: str
    series: str = ""

    @property
    def key(self) -> str:
        raw = "|".join([
            self.exchange.upper(), self.stable_id.upper(), self.ex_date.isoformat(),
            self.action_type.lower(), f"{self.factor:.12g}",
        ])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _action_type_and_factor(description: str):
    subject = str(description).strip()
    lowered = subject.lower()
    if "bonus" in lowered:
        if any(term in lowered for term in ("deb", "pref", "ncrps", "dvr")):
            return None, None
        return "bonus", parse_bonus_factor(subject)
    if any(term in lowered for term in (
        "split", "splt", "sub-division", "sub division", "consolidation"
    )):
        kind = "consolidation" if "consolidation" in lowered else "split"
        return kind, parse_split_factor(subject)
    return None, None


def normalize_nse_actions(
    records: Iterable[dict], add_sme_suffix: bool = True
) -> List[CorporateAction]:
    result = []
    for record in records:
        action_type, factor = _action_type_and_factor(record.get("subject", ""))
        ex_date = pd.to_datetime(record.get("exDate"), errors="coerce", dayfirst=True)
        if not action_type or not factor or pd.isna(ex_date) or factor <= 0:
            continue
        series = str(record.get("series", "")).strip().upper()
        if series not in {"EQ", "BE", "BZ", "SM", "ST"}:
            continue
        symbol = str(record.get("symbol", "")).strip().upper()
        if add_sme_suffix and series in {"SM", "ST"}:
            symbol += "_SME"
        stable_id = str(record.get("isin") or symbol).strip().upper()
        result.append(CorporateAction(
            "NSE", symbol, stable_id, ex_date.date(), action_type, float(factor),
            str(record.get("subject", "")).strip(), series,
        ))
    return result


def normalize_bse_actions(frame: pd.DataFrame) -> List[CorporateAction]:
    frame = frame.copy()
    frame.columns = [str(column).strip() for column in frame.columns]
    result = []
    for _, record in frame.iterrows():
        description = " ".join([
            str(record.get("Purpose", "")), str(record.get("Detail/Remarks", ""))
        ]).strip()
        action_type, factor = _action_type_and_factor(description)
        ex_date = pd.to_datetime(record.get("Ex Date"), errors="coerce", dayfirst=True)
        if not action_type or not factor or pd.isna(ex_date) or factor <= 0:
            continue
        symbol = str(record.get("Security Name", "")).strip().upper()
        stable_id = str(record.get("Security Code") or symbol).strip().upper()
        result.append(CorporateAction(
            "BSE", symbol, stable_id, ex_date.date(), action_type, float(factor),
            description,
        ))
    return result


class CorporateActionClient:
    """Small aiohttp client for the exchanges' official action endpoints."""

    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    async def fetch(
        self,
        exchange: str,
        segment: str,
        start_date: date,
        end_date: date,
        add_sme_suffix: bool = True,
    ) -> List[CorporateAction]:
        if exchange.upper() == "NSE":
            records = await self._fetch_nse(segment, start_date, end_date)
            return normalize_nse_actions(records, add_sme_suffix=add_sme_suffix)
        if exchange.upper() == "BSE":
            frame = await self._fetch_bse(start_date, end_date)
            return normalize_bse_actions(frame)
        return []

    async def _fetch_nse(self, segment: str, start_date: date, end_date: date):
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 Chrome/124 Safari/537.36",
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://www.nseindia.com/companies-listing/"
                       "corporate-filings-actions",
        }
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(
            timeout=timeout, connector=connector, headers=headers
        ) as session:
            async with session.get("https://www.nseindia.com/") as response:
                await response.read()
            params = {
                "index": "sme" if segment.upper() == "SME" else "equities",
                "from_date": start_date.strftime("%d-%m-%Y"),
                "to_date": end_date.strftime("%d-%m-%Y"),
            }
            async with session.get(
                "https://www.nseindia.com/api/corporates-corporateActions",
                params=params,
            ) as response:
                response.raise_for_status()
                return await response.json(content_type=None)

    async def _fetch_bse(self, start_date: date, end_date: date) -> pd.DataFrame:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 Chrome/124 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.bseindia.com/",
            "Origin": "https://www.bseindia.com",
        }
        params = {
            "scripcode": "", "Fdate": start_date.strftime("%Y%m%d"),
            "TDate": end_date.strftime("%Y%m%d"), "Purposecode": "",
            "strSearch": "S", "ddlindustrys": "", "ddlcategorys": "E",
            "segment": "0",
        }
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(
            timeout=timeout, connector=connector, headers=headers
        ) as session:
            async with session.get(
                "https://api.bseindia.com/BseIndiaAPI/api/CorpactCSVDownload/w",
                params=params,
            ) as response:
                response.raise_for_status()
                text = await response.text()
        return pd.read_csv(StringIO(text), dtype=str)


class CorporateActionEngine:
    """Apply actions exactly once to symbol histories and keep an audit ledger."""

    _lock = Lock()

    def __init__(self, base_data_path: Path, tick_size: float = 0.05):
        self.base_path = Path(base_data_path)
        self.state_path = self.base_path / ".state"
        self.ledger_path = self.state_path / "corporate_actions.json"
        self.tick_size = tick_size
        self.histories = SymbolHistoryStore(self.base_path)

    def _read_ledger(self) -> dict:
        if not self.ledger_path.exists():
            return {"version": 1, "actions": {}}
        try:
            data = json.loads(self.ledger_path.read_text(encoding="utf-8"))
            if not isinstance(data.get("actions"), dict):
                raise ValueError("invalid corporate-action ledger")
            return data
        except Exception:
            return {"version": 1, "actions": {}}

    def _write_ledger(self, ledger: dict) -> None:
        self.state_path.mkdir(parents=True, exist_ok=True)
        temporary = self.ledger_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(ledger, indent=2, sort_keys=True), encoding="utf-8"
        )
        temporary.replace(self.ledger_path)

    @staticmethod
    def _ledger_record(
        action: CorporateAction, status: str, rows_adjusted: int, note: str = ""
    ) -> dict:
        record = asdict(action)
        record["ex_date"] = action.ex_date.isoformat()
        record.update({
            "status": status,
            "rows_adjusted": rows_adjusted,
            "note": note,
            "applied_at": pd.Timestamp.now(tz="Asia/Kolkata").isoformat(),
        })
        return record

    def apply(self, actions: Iterable[CorporateAction]) -> dict:
        """Apply new actions, composing same-symbol/same-date factors."""

        # Exchange feeds can repeat an identical action for multiple rows or
        # series.  The audit key defines identity, so never multiply a factor
        # twice merely because the source repeated it.
        unique_actions = {action.key: action for action in actions}
        actions = sorted(
            unique_actions.values(),
            key=lambda action: (
                action.exchange, action.stable_id, action.ex_date,
                action.action_type, action.key,
            ),
        )
        with self._lock:
            ledger = self._read_ledger()
            pending = [
                action for action in actions
                if ledger["actions"].get(action.key, {}).get("status") != "applied"
            ]
            groups = {}
            for action in pending:
                groups.setdefault(
                    (action.exchange, action.stable_id, action.ex_date), []
                ).append(action)

            summary = {"applied": 0, "skipped": 0, "manual_review": 0}
            for (_, _, ex_date), group in groups.items():
                first = group[0]
                symbol = self.histories.resolve_symbol(
                    first.exchange, first.stable_id
                ) or first.symbol
                path = self.histories.symbol_path(first.exchange, symbol)
                if not path.exists():
                    for action in group:
                        ledger["actions"][action.key] = self._ledger_record(
                            action, "symbol_not_found", 0
                        )
                    summary["skipped"] += len(group)
                    continue

                history = pd.read_csv(path)
                dates = pd.to_datetime(
                    history["DATE"].astype(str), format="%Y%m%d", errors="coerce"
                )
                mask = dates.dt.date < ex_date
                rows_adjusted = int(mask.sum())
                if rows_adjusted == 0:
                    for action in group:
                        ledger["actions"][action.key] = self._ledger_record(
                            action, "no_prior_history", 0
                        )
                    summary["skipped"] += len(group)
                    continue

                combined_factor = 1.0
                for action in group:
                    combined_factor *= action.factor
                adjusted = history.copy()
                for column in ("OPEN", "HIGH", "LOW", "CLOSE"):
                    values = pd.to_numeric(adjusted.loc[mask, column], errors="coerce")
                    adjusted.loc[mask, column] = (
                        (values / combined_factor / self.tick_size).round()
                        * self.tick_size
                    ).round(2)

                before = adjusted.loc[mask, "CLOSE"]
                after = adjusted.loc[~mask, "CLOSE"]
                continuity_note = ""
                if not before.empty and not after.empty:
                    previous_close = float(pd.to_numeric(before, errors="coerce").iloc[-1])
                    current_close = float(pd.to_numeric(after, errors="coerce").iloc[0])
                    ratio = current_close / previous_close if previous_close else 0
                    if ratio > 1.5 or ratio < 0.67:
                        continuity_note = (
                            f"Post-adjustment close ratio {ratio:.4f} is outside 0.67-1.50"
                        )

                if continuity_note:
                    for action in group:
                        ledger["actions"][action.key] = self._ledger_record(
                            action, "manual_review", rows_adjusted, continuity_note
                        )
                    summary["manual_review"] += len(group)
                    continue

                self.histories.rewrite_symbol(first.exchange, symbol, adjusted)
                for action in group:
                    ledger["actions"][action.key] = self._ledger_record(
                        action, "applied", rows_adjusted,
                        f"Combined factor {combined_factor:.12g}",
                    )
                summary["applied"] += len(group)

            self._write_ledger(ledger)
            return summary
