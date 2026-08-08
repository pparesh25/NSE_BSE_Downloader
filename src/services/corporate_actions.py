"""Fetch, normalize, audit and apply NSE/BSE corporate actions."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import date
from io import StringIO
from pathlib import Path
import re
from threading import Lock
from typing import Any, Iterable, List, Optional, Tuple

import aiohttp
import pandas as pd

from .canonical_data import valid_isin, valid_security_id
from .symbol_history import SymbolHistoryStore
from .state_store import (
    StateStoreError,
    VersionedJSONStore,
    corporate_action_key,
    default_corporate_ledger,
    file_sha256,
    migrate_corporate_ledger,
    quarantine_copy,
    validate_corporate_ledger,
)


# Free-text dates have never appeared in either exchange's feed (0 of 7,996
# distinct descriptions sampled from 2006-2026), but a single stray one would
# be read as a ratio, so they are removed before any number is trusted.
DATE_PATTERN = re.compile(
    r"\b\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}\b"
    r"|\b\d{1,2}[\s-]*(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    r"[\s,-]*\d{2,4}\b"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[\s,-]*"
    r"\d{1,2}[\s,-]*\d{2,4}\b",
    re.I,
)
BONUS_KEYWORD = re.compile(r"\bbonus\b", re.I)
# Bonus debentures, preference shares, NCRPS and DVR units are not equity, so
# the ratio beside them must never reach an equity price history.
NON_EQUITY_BONUS = re.compile(r"\b(deb|pref|ncrps|dvr)", re.I)
BONUS_PATTERN = re.compile(r"(?<![\d.])(\d{1,6})\s*:\s*(\d{1,6})(?![\d.])")
SPLIT_KEYWORD = re.compile(r"consolidat|sub[\s-]?division|split|splt", re.I)
# "<face value> ... to ... <face value>", anchored after the keyword.  The word
# "from" and the currency token are both optional because the feeds omit them
# ("Fv Split Rs.10 To Re.1", "Face Value Split From 10/- To Face Value 2/-").
SPLIT_PATTERN = re.compile(
    r"(?<!\d)(?<!\d\.)(\d{1,6}(?:\.\d+)?)(?!\d)"
    r"[^\d]{0,20}?\bto\b[^\d]{0,20}?"
    r"(?<!\d)(?<!\d\.)(\d{1,6}(?:\.\d+)?)(?!\d)",
    re.I,
)
MAX_FACE_VALUE = 100000.0

# A capital adjustment changes the *unit*, so every per-share quantity moves
# with it.  Prices are divided by the factor and share counts multiplied by it,
# which is what keeps turnover (price x volume) constant across the ex-date.
# TOTAL_TRADES counts transactions, not shares, so it is left alone, and
# DELIVERY_PERCENT is a ratio of two columns that both scale, so it is too.
ADJUSTED_PRICE_COLUMNS = ("OPEN", "HIGH", "LOW", "CLOSE")
ADJUSTED_SHARE_COUNT_COLUMNS = ("VOLUME", "DELIVERY_QTY")
ADJUSTED_RATE_COLUMNS = ("QTY_PER_TRADE",)
PRICE_DECIMALS = 2
RATE_DECIMALS = 2


def adjust_rows(frame: pd.DataFrame, mask: Any, factor: float) -> None:
    """Apply one capital adjustment, in place, to the selected rows.

    Both adjustment paths call this — the engine over a whole history and the
    raw replay over a single re-downloaded row — so a replayed row can never
    disagree with the row the engine wrote.  Values that are absent stay
    absent: delivery fields are legitimately blank for many dates.
    """

    def scale_price(values: pd.Series) -> pd.Series:
        return (values / factor).round(PRICE_DECIMALS)

    def scale_rate(values: pd.Series) -> pd.Series:
        return (values * factor).round(RATE_DECIMALS)

    def _scaled_share_counts(values: pd.Series) -> pd.Series:
        scaled = (values * factor).round()
        # A consolidation scales share counts *down*, and rounding a thinly
        # traded day to zero would rewrite a day that traded as a day that did
        # not -- turnover collapsing to zero is the very thing this adjustment
        # exists to prevent.  No whole number is right (4 shares at 10:1 is
        # 0.4), but "it traded" is a fact, so keep the smallest count that
        # still says so.
        return scaled.mask((scaled <= 0) & (values > 0), 1).astype("int64")

    for column in ADJUSTED_PRICE_COLUMNS:
        _write_scaled(frame, mask, column, scale_price)
    for column in ADJUSTED_RATE_COLUMNS:
        _write_scaled(frame, mask, column, scale_rate)
    for column in ADJUSTED_SHARE_COUNT_COLUMNS:
        _write_scaled(frame, mask, column, _scaled_share_counts)


def _write_scaled(
    frame: pd.DataFrame, mask: Any, column: str, scale: Any
) -> None:
    if column not in frame.columns:
        return
    values = pd.to_numeric(frame.loc[mask, column], errors="coerce")
    present = values[values.notna()]
    if present.empty:
        return
    frame.loc[present.index, column] = scale(present)


@dataclass(frozen=True)
class ParsedAction:
    """One capital adjustment read out of an announcement."""

    action_type: str
    factor: float


@dataclass(frozen=True)
class ParsedDescription:
    """Every adjustment in one description, or why none could be trusted."""

    actions: Tuple[ParsedAction, ...] = ()
    review: str = ""


def _parse_bonus(text: str) -> Tuple[Optional[ParsedAction], str]:
    keyword = BONUS_KEYWORD.search(text)
    if keyword is None:
        return None, ""
    if NON_EQUITY_BONUS.search(text):
        return None, ""
    ratio = BONUS_PATTERN.search(text, keyword.end())
    if ratio is None:
        return None, "bonus without a readable ratio"
    numerator, denominator = float(ratio.group(1)), float(ratio.group(2))
    if numerator <= 0 or denominator <= 0:
        return None, "bonus ratio is not usable"
    return ParsedAction("bonus", 1 + numerator / denominator), ""


def _parse_split(text: str) -> Tuple[Optional[ParsedAction], str]:
    keyword = SPLIT_KEYWORD.search(text)
    if keyword is None:
        return None, ""
    kind = (
        "consolidation"
        if keyword.group(0).lower().startswith("consolidat")
        else "split"
    )
    # Search only after the keyword.  Descriptions routinely carry an unrelated
    # amount first ("Interim Dividend Rs 2/- Per Share And Face Value Split
    # From Rs 10/- To Rs 2/-"), which the old whole-string search read as the
    # face value and turned into a factor of 0.2 instead of 5.
    pairs = {
        (match.group(1), match.group(2))
        for match in SPLIT_PATTERN.finditer(text, keyword.end())
    }
    if not pairs:
        return None, f"{kind} without a readable face-value change"
    if len(pairs) > 1:
        return None, f"{kind} names more than one face-value change"
    before, after = (float(value) for value in pairs.pop())
    if not 0 < before <= MAX_FACE_VALUE or not 0 < after <= MAX_FACE_VALUE:
        return None, f"{kind} face values are out of range"
    factor = before / after
    if kind == "split" and factor <= 1:
        return None, "split does not reduce the face value"
    if kind == "consolidation" and factor >= 1:
        return None, "consolidation does not raise the face value"
    return ParsedAction(kind, factor), ""


def parse_actions(description: str) -> ParsedDescription:
    """Read every adjustment in one announcement, or none at all.

    A single announcement often carries both a bonus and a face-value split
    ("Bonus 1:1 And Face Value Split From Rs.10/- To Re.1/-", whose true factor
    is 20).  Returning a list lets the engine compose them; returning the first
    one silently discarded the other.
    """

    text = DATE_PATTERN.sub(" ", str(description).lower())
    actions: List[ParsedAction] = []
    reasons: List[str] = []
    for reader in (_parse_bonus, _parse_split):
        action, reason = reader(text)
        if action is not None:
            actions.append(action)
        elif reason:
            reasons.append(reason)
    return ParsedDescription(tuple(actions), "; ".join(reasons))


def _single_factor(description: str, wanted: Iterable[str]) -> Optional[float]:
    wanted = set(wanted)
    factors = [
        action.factor for action in parse_actions(description).actions
        if action.action_type in wanted
    ]
    return factors[0] if len(factors) == 1 else None


def parse_split_factor(subject: str) -> Optional[float]:
    """Return the face-value factor alone, for callers that want just that."""

    return _single_factor(subject, ("split", "consolidation"))


def parse_bonus_factor(subject: str) -> Optional[float]:
    """Return the bonus factor alone, for callers that want just that."""

    return _single_factor(subject, ("bonus",))


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
        return corporate_action_key(
            self.exchange, self.stable_id, self.ex_date.isoformat(),
            self.action_type,
        )


def normalize_nse_actions(
    records: Iterable[dict], add_sme_suffix: bool = True
) -> List[CorporateAction]:
    result: List[CorporateAction] = []
    for record in records:
        description = str(record.get("subject", "")).strip()
        parsed = parse_actions(description)
        ex_date = pd.to_datetime(record.get("exDate"), errors="coerce", dayfirst=True)
        if not parsed.actions or pd.isna(ex_date):
            continue
        series = str(record.get("series", "")).strip().upper()
        if series not in {"EQ", "BE", "BZ", "SM", "ST"}:
            continue
        symbol = str(record.get("symbol", "")).strip().upper()
        if add_sme_suffix and series in {"SM", "ST"}:
            symbol += "_SME"
        # The stable_id is the audit key and the match key.  A malformed ISIN
        # would collide across unrelated announcements, so it falls back to
        # the ticker exactly as a missing one does.
        isin = str(record.get("isin") or "").strip().upper()
        stable_id = isin if valid_isin(isin) else symbol
        result.extend(
            CorporateAction(
                "NSE", symbol, stable_id, ex_date.date(), action.action_type,
                float(action.factor), description, series,
            )
            for action in parsed.actions
        )
    return result


def normalize_bse_actions(frame: pd.DataFrame) -> List[CorporateAction]:
    frame = frame.copy()
    frame.columns = [str(column).strip() for column in frame.columns]
    result: List[CorporateAction] = []
    for _, record in frame.iterrows():
        description = " ".join([
            str(record.get("Purpose", "")), str(record.get("Detail/Remarks", ""))
        ]).strip()
        parsed = parse_actions(description)
        ex_date = pd.to_datetime(record.get("Ex Date"), errors="coerce", dayfirst=True)
        if not parsed.actions or pd.isna(ex_date):
            continue
        symbol = str(record.get("Security Name", "")).strip().upper()
        code = str(record.get("Security Code") or "").strip().upper()
        code = code.removesuffix(".0")
        stable_id = code if valid_security_id(code) else symbol
        result.extend(
            CorporateAction(
                "BSE", symbol, stable_id, ex_date.date(), action.action_type,
                float(action.factor), description,
            )
            for action in parsed.actions
        )
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
        async with aiohttp.ClientSession(
            timeout=timeout, headers=headers
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
        async with aiohttp.ClientSession(
            timeout=timeout, headers=headers
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

    def __init__(self, base_data_path: Path):
        self.base_path = Path(base_data_path)
        self.state_path = self.base_path / ".state"
        self.ledger_path = self.state_path / "corporate_actions.json"
        self.transaction_path = self.state_path / "corporate_action_transactions"
        self.histories = SymbolHistoryStore(self.base_path)
        self._ledger_state = VersionedJSONStore(
            self.ledger_path,
            default=default_corporate_ledger(),
            validator=validate_corporate_ledger,
            quarantine_root=self.state_path / "quarantine",
            category="corporate_actions",
            migrate=migrate_corporate_ledger,
        )

    def _read_ledger(self) -> dict:
        return self._ledger_state.read()

    def _write_ledger(self, ledger: dict) -> None:
        self._ledger_state.write(ledger)

    @staticmethod
    def _empty_summary() -> dict:
        return {
            "applied": 0,
            "skipped": 0,
            "deferred": 0,
            "manual_review": 0,
            "factor_conflicts": 0,
            "recovered": 0,
        }

    @staticmethod
    def _same_factor(recorded: Any, parsed: float) -> bool:
        try:
            return abs(float(recorded) - float(parsed)) <= 1e-9 * max(
                1.0, abs(float(parsed))
            )
        except (TypeError, ValueError):
            return False

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
            "updated_at": pd.Timestamp.now(tz="Asia/Kolkata").isoformat(),
        })
        if status == "applied":
            record["applied_at"] = record["updated_at"]
        return record

    @staticmethod
    def _action_from_record(record: dict) -> CorporateAction:
        try:
            return CorporateAction(
                exchange=str(record["exchange"]),
                symbol=str(record["symbol"]),
                stable_id=str(record["stable_id"]),
                ex_date=date.fromisoformat(str(record["ex_date"])),
                action_type=str(record["action_type"]),
                factor=float(record["factor"]),
                description=str(record.get("description", "")),
                series=str(record.get("series", "")),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise StateStoreError(
                f"Invalid corporate-action audit record: {error}"
            ) from error

    def reconcile_pending(self, exchange: Optional[str] = None) -> dict:
        """Retry actions that were recorded before their history existed."""

        recovered = self.recover_incomplete_transactions()
        ledger = self._read_ledger()
        wanted_exchange = exchange.upper() if exchange else None
        actions = [
            self._action_from_record(record)
            for record in ledger["actions"].values()
            if record.get("status") in {
                "symbol_not_found", "no_prior_history", "awaiting_ex_date"
            }
            and (
                wanted_exchange is None
                or str(record.get("exchange", "")).upper() == wanted_exchange
            )
        ]
        if not actions:
            summary = self._empty_summary()
            summary["recovered"] = recovered
            return summary
        summary = self.apply(actions)
        summary["recovered"] += recovered
        return summary

    def _transaction_stage(self, transaction_id: str) -> Path:
        if re.fullmatch(r"[0-9a-f]{64}", transaction_id) is None:
            raise StateStoreError("Invalid corporate-action transaction id")
        return self.transaction_path / f"{transaction_id}.csv"

    @staticmethod
    def _transaction_id(group: list[CorporateAction]) -> str:
        material = "|".join(sorted(action.key for action in group))
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _write_transaction_stage(
        self, transaction_id: str, adjusted: pd.DataFrame
    ) -> tuple[Path, str]:
        stage = self._transaction_stage(transaction_id)
        stage.parent.mkdir(parents=True, exist_ok=True)
        temporary = stage.with_suffix(".csv.tmp")
        normalized = self.histories._deduplicate(adjusted)
        self.histories._validate_history(normalized)
        try:
            normalized.to_csv(temporary, index=False)
            digest = file_sha256(temporary)
            temporary.replace(stage)
            return stage, digest
        finally:
            temporary.unlink(missing_ok=True)

    def _commit_transaction(
        self,
        ledger: dict,
        transaction_id: str,
        observed_history_hash: str,
    ) -> None:
        transaction = ledger["transactions"][transaction_id]
        if observed_history_hash != transaction["after_sha256"]:
            raise StateStoreError(
                "Corporate-action history checksum does not match prepared state"
            )
        for key, record in transaction["final_action_records"].items():
            ledger["actions"][key] = record
        transaction["status"] = "committed"
        transaction["observed_history_sha256"] = observed_history_hash
        transaction["committed_at"] = pd.Timestamp.now(
            tz="Asia/Kolkata"
        ).isoformat()
        self._write_ledger(ledger)
        self._transaction_stage(transaction_id).unlink(missing_ok=True)

    def _recover_transactions(self, ledger: dict) -> int:
        recovered = 0
        for transaction_id, transaction in sorted(
            ledger["transactions"].items()
        ):
            if transaction.get("status") == "committed":
                self._transaction_stage(transaction_id).unlink(missing_ok=True)
                continue
            if transaction.get("status") != "prepared":
                raise StateStoreError(
                    f"Unknown corporate-action transaction state: {transaction_id}"
                )

            stage = self._transaction_stage(transaction_id)
            stage_valid = (
                stage.exists()
                and file_sha256(stage) == transaction.get("after_sha256")
            )
            if not stage_valid:
                quarantine_path = quarantine_copy(
                    stage,
                    self.state_path / "quarantine",
                    "corporate_action_transactions",
                )
                backup_note = (
                    f" (backup: {quarantine_path})"
                    if quarantine_path is not None else ""
                )
                raise StateStoreError(
                    "Prepared corporate-action stage is missing or corrupt: "
                    f"{stage}{backup_note}"
                )
            target = self.histories.symbol_path(
                str(transaction["exchange"]), str(transaction["symbol"])
            )
            current_hash = file_sha256(target) if target.exists() else None
            if current_hash == transaction.get("before_sha256"):
                try:
                    staged_raw = pd.read_csv(stage, dtype=str)
                    staged_history = self.histories._upgrade_legacy_history(
                        staged_raw
                    )
                    self.histories._validate_history(staged_history)
                except Exception as error:
                    quarantine_path = quarantine_copy(
                        stage,
                        self.state_path / "quarantine",
                        "corporate_action_transactions",
                    )
                    raise StateStoreError(
                        "Prepared corporate-action history is invalid"
                        + (
                            f" (backup: {quarantine_path})"
                            if quarantine_path is not None else ""
                        )
                    ) from error
                self.histories.rewrite_symbol(
                    str(transaction["exchange"]),
                    str(transaction["symbol"]),
                    staged_history,
                )
                current_hash = file_sha256(target)
                if staged_history is not staged_raw:
                    # The stage was written by a pre-ISIN build and its bytes
                    # were already verified against ``after_sha256`` above;
                    # replaying it through the current schema changes the
                    # serialization, so the expected digest moves with it.
                    transaction["after_sha256"] = current_hash
            if current_hash != transaction.get("after_sha256"):
                if target.exists():
                    self.histories._read_history(target)
                raise StateStoreError(
                    "Corporate-action recovery found an unexpected history "
                    f"revision for {target}"
                )
            if current_hash is None:
                raise StateStoreError(
                    f"Corporate-action history is missing during recovery: {target}"
                )
            self._commit_transaction(
                ledger, transaction_id, current_hash
            )
            recovered += len(transaction.get("action_keys", []))
        return recovered

    def recover_incomplete_transactions(self) -> int:
        """Complete any prepared action after an interrupted prior run."""

        with self._lock:
            ledger = self._read_ledger()
            return self._recover_transactions(ledger)

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
            recovered = self._recover_transactions(ledger)
            summary = self._empty_summary()
            summary["recovered"] = recovered
            ledger_dirty = False

            pending = []
            for action in actions:
                record = ledger["actions"].get(action.key)
                if record is None or record.get("status") != "applied":
                    pending.append(action)
                    continue
                # The announcement is already in the history.  If this run read
                # a different factor from the same words, re-applying would
                # divide the prices a second time, so report it instead.
                if not self._same_factor(record.get("factor"), action.factor):
                    record["note"] = (
                        f"Applied with factor {float(record['factor']):.12g}; "
                        f"this run reads {action.factor:.12g} from the same "
                        "description. Rebuild this symbol to adopt the new "
                        "reading."
                    )
                    record["updated_at"] = pd.Timestamp.now(
                        tz="Asia/Kolkata"
                    ).isoformat()
                    ledger_dirty = True
                    summary["factor_conflicts"] += 1

            groups: dict[tuple[str, str, date], list[CorporateAction]] = {}
            for action in pending:
                groups.setdefault(
                    (action.exchange, action.stable_id, action.ex_date), []
                ).append(action)

            # Chronological within a symbol, so a run that receives two
            # ex-dates at once composes them in the order the raw replay does.
            # Rounding is not associative, so the order is observable.
            for group_key in sorted(groups):
                ex_date = group_key[2]
                group = groups[group_key]
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
                    ledger_dirty = True
                    summary["skipped"] += len(group)
                    continue

                history = self.histories._read_history(path)
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
                    ledger_dirty = True
                    summary["skipped"] += len(group)
                    continue

                if int((~mask).sum()) == 0:
                    # Without a bar on or after the ex-date the continuity
                    # check below cannot run, and an unchecked adjustment is
                    # exactly how a misread factor reaches the history.  A
                    # backfill bucket that stops the day before an ex-date hits
                    # this every time, so wait for the next run instead.
                    for action in group:
                        ledger["actions"][action.key] = self._ledger_record(
                            action, "awaiting_ex_date", 0,
                            "No bar on or after the ex-date yet, so the "
                            "continuity check cannot run",
                        )
                    ledger_dirty = True
                    summary["deferred"] += len(group)
                    continue

                combined_factor = 1.0
                for action in group:
                    combined_factor *= action.factor
                adjusted = history.copy()
                adjust_rows(adjusted, mask, combined_factor)

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
                    ledger_dirty = True
                    summary["manual_review"] += len(group)
                    continue

                transaction_id = self._transaction_id(group)
                _, after_sha256 = self._write_transaction_stage(
                    transaction_id, adjusted
                )
                before_sha256 = file_sha256(path)
                final_records = {
                    action.key: self._ledger_record(
                        action,
                        "applied",
                        rows_adjusted,
                        f"Combined factor {combined_factor:.12g}",
                    )
                    for action in group
                }
                ledger["transactions"][transaction_id] = {
                    "status": "prepared",
                    "exchange": first.exchange.upper(),
                    "symbol": symbol.upper(),
                    "action_keys": sorted(final_records),
                    "before_sha256": before_sha256,
                    "after_sha256": after_sha256,
                    "final_action_records": final_records,
                    "prepared_at": pd.Timestamp.now(
                        tz="Asia/Kolkata"
                    ).isoformat(),
                }
                for action in group:
                    ledger["actions"][action.key] = self._ledger_record(
                        action,
                        "prepared",
                        rows_adjusted,
                        f"Transaction {transaction_id}",
                    )
                self._write_ledger(ledger)
                self.histories.rewrite_symbol(first.exchange, symbol, adjusted)
                observed_sha256 = file_sha256(path)
                self._commit_transaction(
                    ledger, transaction_id, observed_sha256
                )
                summary["applied"] += len(group)

            if ledger_dirty:
                self._write_ledger(ledger)
            return summary
