"""Track which adjustment semantics produced each exchange's symbol files.

A corporate-action correction cannot reach bars that were already adjusted:
the action is recorded ``applied`` and is never divided a second time.  So a
symbol file written by an older build keeps the older arithmetic for its old
bars while new bars use the new one, and nothing on disk says which is which.

This marker says which, per exchange, so the application can tell the user that
a rebuild is what adopts the new arithmetic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from .state_store import VersionedJSONStore


# 1 -- v1.1.0 and earlier: pre-ex-date OPEN/HIGH/LOW/CLOSE divided by the
#      factor and snapped to a 0.05 grid; share counts left raw.
# 2 -- prices divided and rounded to 2dp with no grid; VOLUME, DELIVERY_QTY and
#      QTY_PER_TRADE multiplied by the factor so turnover stays continuous.
SYMBOL_ADJUSTMENT_REVISION = 2

REVISION_NOTICE = (
    "Symbol histories for {exchanges} were built before the corporate-action "
    "volume fix, so bars before an old split or bonus still carry unadjusted "
    "volume and grid-snapped prices. Run a rebuild for those exchanges to "
    "adopt the corrected arithmetic; it replays the checksummed .state/raw "
    "snapshots, so only dates this application downloaded can be repaired."
)


def default_history_revision() -> Dict[str, Any]:
    return {"version": 1, "adjustment": {}}


def validate_history_revision(data: Dict[str, Any]) -> None:
    if data.get("version") != 1:
        raise ValueError("unsupported history-revision version")
    adjustment = data.get("adjustment")
    if not isinstance(adjustment, dict):
        raise ValueError("history-revision adjustment must be an object")
    for exchange, revision in adjustment.items():
        if not isinstance(exchange, str) or not exchange:
            raise ValueError("history-revision has an invalid exchange")
        if not isinstance(revision, int) or isinstance(revision, bool):
            raise ValueError("history-revision has a non-integer revision")
        if revision < 1:
            raise ValueError("history-revision must be positive")


class HistoryRevisionStore:
    """Read and update the per-exchange symbol-adjustment revision."""

    def __init__(self, base_data_path: Path):
        self.base_path = Path(base_data_path)
        self.state_path = self.base_path / ".state"
        self._store = VersionedJSONStore(
            self.state_path / "history_revision.json",
            default=default_history_revision(),
            validator=validate_history_revision,
            quarantine_root=self.state_path / "quarantine",
            category="history_revision",
        )

    def read(self) -> Dict[str, Any]:
        return self._store.read()

    def revision_for(self, exchange: str) -> int:
        # An unmarked exchange predates the marker, so it is revision 1.  A
        # tree that does not exist at all is handled by stale_exchanges.
        return int(
            self.read()["adjustment"].get(exchange.upper(), 1)
        )

    def mark_current(self, exchange: str) -> None:
        document = self.read()
        document["adjustment"][exchange.upper()] = SYMBOL_ADJUSTMENT_REVISION
        self._store.write(document)

    def _has_symbol_files(self, exchange: str) -> bool:
        directory = self.base_path / exchange.upper() / "SYMBOLS"
        if not directory.is_dir():
            return False
        return any(directory.glob("*.txt"))

    def stale_exchanges(self) -> List[str]:
        """Exchanges whose existing symbol files predate the current rule.

        A fresh installation has no symbol files and is therefore never stale,
        so a new user is not told to rebuild something that does not exist.
        """

        document = self.read()
        stale = []
        for directory in sorted(self.base_path.glob("*")):
            if not directory.is_dir() or directory.name.startswith("."):
                continue
            exchange = directory.name.upper()
            if not self._has_symbol_files(exchange):
                continue
            recorded = int(document["adjustment"].get(exchange, 1))
            if recorded < SYMBOL_ADJUSTMENT_REVISION:
                stale.append(exchange)
        return stale

    def notice(self) -> str:
        """Return the rebuild prompt, or an empty string when nothing is stale."""

        stale = self.stale_exchanges()
        if not stale:
            return ""
        return REVISION_NOTICE.format(exchanges=" and ".join(stale))
