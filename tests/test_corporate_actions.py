import json
from datetime import date

import pandas as pd
import pytest

from src.services.corporate_actions import (
    CorporateAction,
    CorporateActionEngine,
    normalize_bse_actions,
    normalize_nse_actions,
    parse_actions,
    parse_bonus_factor,
    parse_split_factor,
)
from src.services.state_store import (
    corporate_action_key,
    migrate_corporate_ledger,
    validate_corporate_ledger,
)
from src.services.symbol_history import SymbolHistoryStore


def _history_rows(dates=("20250101", "20250102"), closes=(100, 50)):
    return pd.DataFrame([
        {
            "SYMBOL": "ABC", "DATE": day, "OPEN": close,
            "HIGH": close, "LOW": close, "CLOSE": close, "VOLUME": 10,
            "DELIVERY_QTY": 5, "DELIVERY_PERCENT": 50, "SERIES": "EQ",
            "TOTAL_TRADES": 1, "QTY_PER_TRADE": 10, "ISIN": "INE1",
            "SECURITY_ID": "500001",
        }
        for day, close in zip(dates, closes)
    ])


# Every string below is a verbatim NSE `subject` or BSE `Purpose` taken from
# the exchanges' own feeds between 2006 and 2026, except the two marked
# SYNTHETIC.  Expected values are ("action type", factor) pairs in the order
# the parser reports them; () means the description must produce no action.
REAL_DESCRIPTIONS = [
    # --- plain bonus, both exchanges' wordings -------------------------------
    ("BONUS 1:1", (("bonus", 2.0),)),
    ("Bonus 1 : 1", (("bonus", 2.0),)),
    ("Bonus 1: 2", (("bonus", 1.5),)),
    ("Bonus- 1:2", (("bonus", 1.5),)),
    ("Bonus issue 3:2", (("bonus", 2.5),)),
    ("BONUS 3:7", (("bonus", 1 + 3 / 7),)),
    ("Bonus 1 : 1250", (("bonus", 1.0008),)),
    ("Bonus issue 29:50", (("bonus", 1.58),)),
    ("Bonus Shares In The Ratio Of 1:1", (("bonus", 2.0),)),
    ("Bonus 4:5 (Pursuant To Scheme Of Amalgamation)", (("bonus", 1.8),)),
    ("Bonus 1:10 (Record Date Revised)", (("bonus", 1.1),)),
    ("Annual General Meeting/Dividend Rs.2/- Per Share/Bonus 1:3",
     (("bonus", 1 + 1 / 3),)),
    ("Bonus 1:1 /Dividend- Rs 29 Per Share", (("bonus", 2.0),)),

    # --- bonuses that are not equity ----------------------------------------
    ("Scheme Of Arrangement - Bonus Debentures 1:1", ()),
    ("Scheme Of Arrangement - Bonus Ncrps 1:10", ()),
    ("Bonus Preference Shares 21:1", ()),
    ("Bonus 1 Dvr : 10 Eq Share", ()),
    ("Sch Of Agmt- Bonus Deb1:1", ()),

    # --- face-value splits, every wording the feeds use ---------------------
    ("Face Value Split (Sub-Division) - From Rs 10/- Per Share To Re 1/- "
     "Per Share", (("split", 10.0),)),
    ("FACE VALUE SPLIT (SUB-DIVISION) - FROM RS10/- PER SHARE TO RS 2/- "
     "PER SHARE", (("split", 5.0),)),
    ("Face Value Split (Sub-Division): From Rs 10/- Per Share To Rs 2/- "
     "Per Share", (("split", 5.0),)),
    ("Face Value Split From  Rs.10/- To Re.1/-", (("split", 10.0),)),
    ("Face Value Split From Rs.10/- To Rs.6/-", (("split", 10 / 6),)),
    ("Face Value Split Rs.10/- To Re.1/- Per Share", (("split", 10.0),)),
    ("Fv Split Rs.2/- To Re.1/-", (("split", 2.0),)),
    ("Fv Splt Frm Rs 10 To Re 1", (("split", 10.0),)),
    ("Stock  Split From Rs.1000/- to Rs.10/-", (("split", 100.0),)),
    ("Sub-Division From Rs 10/- Per Share To Rs 2/- Per Share",
     (("split", 5.0),)),
    ("Face Valus Split (Sub-Division) - From Rs 10/- Per To Rs 2/- Per Share",
     (("split", 5.0),)),

    # --- a leading rupee amount must not be read as the face value ----------
    ("Annual General Meeting / Dividend - Rs 1.35/- Per Share / Face Value "
     "Split - From Rs 10/- Per Share To Rs 2/- Per Share", (("split", 5.0),)),
    ("Interim Dividend Rs 2/- Per Share And Face Value Split From Rs 10/- "
     "To Rs 2/-", (("split", 5.0),)),
    ("Interim Dividend Rs.3/- Per Share And Face Value Split From Rs.5/- "
     "To Rs.2/- (Purpose Revised)", (("split", 2.5),)),
    ("Dividend Rs.6/- Per Share And Face Value Split From Rs.2/- To Re.1/-",
     (("split", 2.0),)),
    ("1st Interim Dividend Rs.2.50 Per Share And Face Value Split From "
     "Rs.10/- To Re.1/- (Purpose Revised)", (("split", 10.0),)),

    # --- one announcement, two adjustments ----------------------------------
    ("Bonus 1:1/Face Value Split (Sub-Division) - From Rs 10/- Per Share To "
     "Re 1/- Per Share", (("bonus", 2.0), ("split", 10.0))),
    ("Bonus 1:1 And Face Value Split From Rs.2/- To Re.1/-",
     (("bonus", 2.0), ("split", 2.0))),
    ("Bonus 22:1 And Face Value Split From Rs.10/- To Re.1/-",
     (("bonus", 23.0), ("split", 10.0))),
    ("Annual General Meeting/Bonus 2:1/Face Value Split From Rs.10/- To "
     "Re.1/-", (("bonus", 3.0), ("split", 10.0))),
    ("Face Value Split From Rs.5/- To Re.1/- And Bonus 1:1",
     (("bonus", 2.0), ("split", 5.0))),
    ("Bonus 1:1 / Face Value Split From 10/- To Face Value 2/-",
     (("bonus", 2.0), ("split", 5.0))),

    # --- consolidation ------------------------------------------------------
    ("Consolidation Of Equity Shares From Re 1 Per Share To Rs 10 Per Share",
     (("consolidation", 0.1),)),
    ("Capital Reduction Rs 10 To Rs 3.30 / Consolidation Rs 3.30 To Rs.10",
     (("consolidation", 0.33),)),

    # --- action-shaped but unusable: never guess ----------------------------
    ("Consolidation of Shares", ()),
    ("Sub Division of Equity shares", ()),
    ("Bonus issue 28100", ()),
    ("Bonus issue 0:0", ()),

    # --- not capital adjustments at all -------------------------------------
    ("Interim Dividend - Rs 5.00 Per Share", ()),
    ("Scheme of Arrangement", ()),
    ("Rights - 4:25 Fully Paid Up Shares @ Premium Rs 500/- Per Share / "
     "2:25 Partly Paid Up Shares @ Premium Rs 605/- Per Share", ()),

    # --- SYNTHETIC: the review's date-bearing examples ----------------------
    ("Sub-Division w.e.f. 12-08-2024 From Rs.10/- To Re.1/-",
     (("split", 10.0),)),
    ("Split w.e.f. 05-09-2023", ()),
]


@pytest.mark.parametrize("description,expected", REAL_DESCRIPTIONS)
def test_real_exchange_descriptions_parse_to_the_published_factors(
    description, expected
):
    parsed = parse_actions(description)
    assert tuple(
        action.action_type for action in parsed.actions
    ) == tuple(kind for kind, _ in expected)
    assert [action.factor for action in parsed.actions] == pytest.approx(
        [factor for _, factor in expected]
    )


def test_a_leading_rupee_amount_was_previously_read_as_the_face_value():
    """The pre-3.1 whole-string search produced 0.2 here, not 5."""

    description = (
        "Interim Dividend Rs 2/- Per Share And Face Value Split "
        "From Rs 10/- To Rs 2/-"
    )
    assert parse_split_factor(description) == 5.0


def test_a_free_text_date_is_not_read_as_a_ratio():
    """The pre-3.1 parser read 12 and 08 out of the date and returned 1.5."""

    assert parse_split_factor(
        "Sub-Division w.e.f. 12-08-2024 From Rs.10/- To Re.1/-"
    ) == 10.0
    # With the date removed there is no face-value change left at all, which
    # is the honest answer rather than 5/9.
    assert parse_split_factor("Split w.e.f. 05-09-2023") is None


def test_an_unusable_description_is_reported_rather_than_guessed():
    assert parse_actions("Consolidation of Shares").review == (
        "consolidation without a readable face-value change"
    )
    assert parse_actions("Bonus issue 28100").review == (
        "bonus without a readable ratio"
    )
    assert parse_actions("BONUS 1:1").review == ""


def test_a_face_value_change_must_move_the_right_way():
    # A "split" that raises the face value, or a "consolidation" that lowers
    # it, means the description was not understood.
    assert parse_actions(
        "Face Value Split From Rs 1/- To Rs 10/-"
    ).review == "split does not reduce the face value"
    assert parse_actions(
        "Consolidation From Rs 10/- To Re 1/-"
    ).review == "consolidation does not raise the face value"


def test_ratio_parsers_and_exchange_normalizers():
    assert parse_split_factor(
        "Face Value Split (Sub-Division) - From Rs 10/- Per Share "
        "To Rs 2/- Per Share"
    ) == 5
    assert parse_bonus_factor("Bonus 1:1") == 2
    nse = normalize_nse_actions([{
        "symbol": "ABC", "series": "EQ", "isin": "INE1",
        "exDate": "02-Jan-2025", "subject": "Bonus 1:1",
    }])
    assert len(nse) == 1 and nse[0].factor == 2

    bse = normalize_bse_actions(pd.DataFrame([{
        "Security Code": "500001", "Security Name": "ABC",
        "Ex Date": "02 Jan 2025",
        "Purpose": "Face Value Split From Rs 10 To Rs 2",
    }]))
    assert len(bse) == 1 and bse[0].factor == 5


def test_one_announcement_yields_one_action_per_adjustment():
    subject = (
        "Bonus 1:1/Face Value Split (Sub-Division) - From Rs 10/- Per Share "
        "To Re 1/- Per Share"
    )
    actions = normalize_nse_actions([{
        "symbol": "SUNILHITEC", "series": "EQ", "isin": "INE1",
        "exDate": "01-Dec-2016", "subject": subject,
    }])
    assert [(action.action_type, action.factor) for action in actions] == [
        ("bonus", 2.0), ("split", 10.0)
    ]
    # Both carry the same description, so the audit trail still shows why.
    assert {action.description for action in actions} == {subject}


def test_a_combined_bonus_and_split_composes_into_one_adjustment(tmp_path):
    """SUNILHITEC, 2016-12-01: NSE published 202.75 then 10.65, a 19x gap.

    The pre-3.1 parser read only the bonus and divided by 2.
    """

    store = SymbolHistoryStore(tmp_path)
    store.upsert("NSE", "EQ", date(2016, 12, 1), _history_rows(
        ("20161130", "20161201"), (202.75, 10.65)
    ))
    actions = normalize_nse_actions([{
        "symbol": "ABC", "series": "EQ", "isin": "INE1",
        "exDate": "01-Dec-2016",
        "subject": "Bonus 1:1/Face Value Split (Sub-Division) - From Rs 10/- "
                   "Per Share To Re 1/- Per Share",
    }])
    summary = CorporateActionEngine(tmp_path).apply(actions)
    assert summary["applied"] == 2 and summary["manual_review"] == 0

    adjusted = pd.read_csv(tmp_path / "NSE" / "SYMBOLS" / "abc.txt")
    previous = float(adjusted.loc[adjusted["DATE"] == 20161130, "CLOSE"].iloc[0])
    assert previous == pytest.approx(202.75 / 20, abs=0.05)


def test_the_audit_key_survives_a_change_of_factor():
    """A parser correction must not look like a new, unapplied action."""

    def action(factor):
        return CorporateAction(
            "NSE", "ABC", "INE1", date(2025, 1, 2), "bonus", factor, "Bonus"
        )

    assert action(2.0).key == action(20.0).key
    assert action(2.0).key == corporate_action_key(
        "nse", "ine1", "2025-01-02", "BONUS"
    )
    # Identity still separates the two adjustments of one announcement.
    assert action(2.0).key != CorporateAction(
        "NSE", "ABC", "INE1", date(2025, 1, 2), "split", 2.0, "Bonus"
    ).key


def test_a_version_two_ledger_is_rekeyed_instead_of_reapplied():
    record = {
        "exchange": "NSE", "symbol": "ABC", "stable_id": "INE1",
        "ex_date": "2016-12-01", "action_type": "bonus", "factor": 2.0,
        "description": "Bonus 1:1 and split", "series": "EQ",
        "status": "applied", "rows_adjusted": 141, "note": "",
        "updated_at": "2026-01-01T00:00:00+05:30",
    }
    old_key = "0" * 64
    legacy = {
        "version": 2,
        "actions": {old_key: dict(record)},
        "transactions": {
            "1" * 64: {
                "status": "committed", "exchange": "NSE", "symbol": "ABC",
                "action_keys": [old_key], "before_sha256": "a" * 64,
                "after_sha256": "b" * 64,
                "final_action_records": {old_key: dict(record)},
            }
        },
    }
    migrated = migrate_corporate_ledger(legacy)
    validate_corporate_ledger(migrated)

    new_key = corporate_action_key("NSE", "INE1", "2016-12-01", "bonus")
    assert migrated["version"] == 3
    assert set(migrated["actions"]) == {new_key}
    assert migrated["actions"][new_key]["status"] == "applied"
    transaction = migrated["transactions"]["1" * 64]
    assert transaction["action_keys"] == [new_key]
    assert set(transaction["final_action_records"]) == {new_key}

    # The corrected reading of the same announcement now finds its record.
    corrected = CorporateAction(
        "NSE", "ABC", "INE1", date(2016, 12, 1), "bonus", 20.0, "Bonus 1:1"
    )
    assert corrected.key == new_key


def test_a_v2_ledger_keeps_the_applied_record_when_keys_collide():
    def record(factor, status, updated):
        return {
            "exchange": "NSE", "symbol": "ABC", "stable_id": "INE1",
            "ex_date": "2016-12-01", "action_type": "bonus", "factor": factor,
            "description": "Bonus", "series": "EQ", "status": status,
            "rows_adjusted": 1, "note": "", "updated_at": updated,
        }

    migrated = migrate_corporate_ledger({
        "version": 2,
        "actions": {
            "0" * 64: record(2.0, "applied", "2026-01-01T00:00:00+05:30"),
            "1" * 64: record(20.0, "manual_review", "2026-06-01T00:00:00+05:30"),
        },
        "transactions": {},
    })
    validate_corporate_ledger(migrated)
    (kept,) = migrated["actions"].values()
    assert kept["status"] == "applied" and kept["factor"] == 2.0


def test_a_new_reading_of_an_applied_action_is_reported_not_reapplied(tmp_path):
    store = SymbolHistoryStore(tmp_path)
    store.upsert("NSE", "EQ", date(2025, 1, 2), _history_rows())
    engine = CorporateActionEngine(tmp_path)
    applied = CorporateAction(
        "NSE", "ABC", "INE1", date(2025, 1, 2), "bonus", 2.0, "Bonus 1:1", "EQ"
    )
    assert engine.apply([applied])["applied"] == 1
    path = tmp_path / "NSE" / "SYMBOLS" / "abc.txt"
    assert float(pd.read_csv(path).loc[0, "CLOSE"]) == 50.0

    corrected = CorporateAction(
        "NSE", "ABC", "INE1", date(2025, 1, 2), "bonus", 20.0, "Bonus 1:1", "EQ"
    )
    summary = engine.apply([corrected])
    assert summary["applied"] == 0 and summary["factor_conflicts"] == 1
    # The history is untouched: dividing it again would be the real damage.
    assert float(pd.read_csv(path).loc[0, "CLOSE"]) == 50.0
    ledger = engine._read_ledger()["actions"][corrected.key]
    assert ledger["status"] == "applied" and "Rebuild this symbol" in ledger["note"]


def test_an_action_waits_until_the_ex_date_bar_exists(tmp_path):
    """A backfill bucket that stops the day before an ex-date must not adjust.

    With no bar on or after the ex-date the continuity check cannot run, and
    the pre-3.1 engine wrote the adjustment anyway.
    """

    store = SymbolHistoryStore(tmp_path)
    store.upsert("NSE", "EQ", date(2016, 11, 30), _history_rows(
        ("20161129", "20161130"), (202.75, 202.75)
    ))
    action = CorporateAction(
        "NSE", "ABC", "INE1", date(2016, 12, 1), "bonus", 2.0, "Bonus 1:1", "EQ"
    )
    engine = CorporateActionEngine(tmp_path)
    summary = engine.apply([action])
    assert summary == {
        "applied": 0, "skipped": 0, "deferred": 1, "manual_review": 0,
        "factor_conflicts": 0, "recovered": 0,
    }
    path = tmp_path / "NSE" / "SYMBOLS" / "abc.txt"
    assert float(pd.read_csv(path).loc[0, "CLOSE"]) == 202.75
    assert engine._read_ledger()["actions"][action.key]["status"] == (
        "awaiting_ex_date"
    )

    # The next run brings the ex-date bar and the deferred action is retried.
    store.upsert("NSE", "EQ", date(2016, 12, 1), _history_rows(
        ("20161201",), (101.4,)
    ))
    adjusted = pd.read_csv(path)
    previous = adjusted.loc[adjusted["DATE"] == 20161129, "CLOSE"].iloc[0]
    assert float(previous) == pytest.approx(202.75 / 2, abs=0.05)


def test_corporate_action_applies_once_and_scales_the_share_counts(tmp_path):
    rows = _history_rows()
    store = SymbolHistoryStore(tmp_path)
    store.upsert("NSE", "EQ", date(2025, 1, 2), rows)
    action = CorporateAction(
        "NSE", "ABC", "INE1", date(2025, 1, 2),
        "bonus", 2.0, "Bonus 1:1", "EQ",
    )
    engine = CorporateActionEngine(tmp_path)
    # A duplicated source record must not compound the same action twice.
    assert engine.apply([action, action])["applied"] == 1
    path = tmp_path / "NSE" / "SYMBOLS" / "abc.txt"
    adjusted = pd.read_csv(path)
    assert float(adjusted.loc[0, "CLOSE"]) == 50.0
    # One share became two, so the pre-ex-date share counts double while the
    # transaction count and the delivery ratio stay as the exchange reported.
    assert int(adjusted.loc[0, "VOLUME"]) == 20
    assert int(adjusted.loc[0, "DELIVERY_QTY"]) == 10
    assert int(adjusted.loc[0, "TOTAL_TRADES"]) == 1
    assert float(adjusted.loc[0, "DELIVERY_PERCENT"]) == 50.0
    assert float(adjusted.loc[0, "QTY_PER_TRADE"]) == 20.0

    assert engine.apply([action])["applied"] == 0
    unchanged = pd.read_csv(path)
    assert float(unchanged.loc[0, "CLOSE"]) == 50.0

    # A late delivery retry replays raw OHLC for the old date.  The audited
    # action must still be reflected while non-price values can be refreshed.
    retry = rows.iloc[[0]].copy()
    retry.loc[:, "DELIVERY_QTY"] = 9
    store.upsert("NSE", "EQ", date(2025, 1, 1), retry)
    retried = pd.read_csv(path)
    old_day = retried.loc[retried["DATE"] == 20250101].iloc[0]
    assert float(old_day["CLOSE"]) == 50.0
    assert int(old_day["DELIVERY_QTY"]) == 18


def test_an_adjustment_keeps_turnover_continuous_across_the_ex_date(tmp_path):
    """MWL, 2026-07-10: NSE published 370.25 then 36.65 on a 10:1 split.

    Price x volume is the money that changed hands, and it cannot jump because
    the share unit changed. The pre-3.2 code adjusted price and left volume
    raw, so every pre-split bar's turnover was divided by the factor.
    """

    rows = _history_rows(
        ("20260709", "20260710"), (370.25, 36.65)
    )
    rows.loc[0, ["VOLUME", "TOTAL_TRADES", "DELIVERY_QTY"]] = [120588, 2336, 40000]
    rows.loc[1, ["VOLUME", "TOTAL_TRADES", "DELIVERY_QTY"]] = [7549418, 18516, 2000000]
    rows["QTY_PER_TRADE"] = (
        rows["VOLUME"] / rows["TOTAL_TRADES"]
    ).round(2)
    store = SymbolHistoryStore(tmp_path)
    store.upsert("NSE", "EQ", date(2026, 7, 10), rows)
    CorporateActionEngine(tmp_path).apply([CorporateAction(
        "NSE", "ABC", "INE1", date(2026, 7, 10), "split", 10.0,
        "Face Value Split (Sub-Division) - From Rs 10/- Per Share To "
        "Re 1/- Per Share", "EQ",
    )])
    adjusted = pd.read_csv(tmp_path / "NSE" / "SYMBOLS" / "abc.txt")
    before = adjusted.loc[adjusted["DATE"] == 20260709].iloc[0]
    assert float(before["CLOSE"]) == 37.02  # 370.25 / 10, at two decimals
    assert int(before["VOLUME"]) == 1205880
    # Turnover survives the change of unit; only the 2dp price rounding moves
    # it, by about a hundredth of a percent. The pre-3.2 code was out by 10x.
    assert float(before["CLOSE"]) * int(before["VOLUME"]) == pytest.approx(
        370.25 * 120588, rel=1e-3
    )
    # QTY_PER_TRADE is VOLUME / TOTAL_TRADES at source, so scaling the volume
    # without the trade count must scale the rate by the same factor.  The
    # source value is already rounded to 2dp, so the factor scales that half
    # a hundredth up with it.
    assert float(before["QTY_PER_TRADE"]) == pytest.approx(
        int(before["VOLUME"]) / int(before["TOTAL_TRADES"]), abs=0.005 * 10
    )


def test_a_consolidation_reduces_the_share_counts(tmp_path):
    rows = _history_rows(("20250101", "20250102"), (10.0, 100.0))
    rows.loc[0, ["VOLUME", "DELIVERY_QTY"]] = [5000, 2500]
    store = SymbolHistoryStore(tmp_path)
    store.upsert("NSE", "EQ", date(2025, 1, 2), rows)
    CorporateActionEngine(tmp_path).apply([CorporateAction(
        "NSE", "ABC", "INE1", date(2025, 1, 2), "consolidation", 0.1,
        "Consolidation Of Equity Shares From Re 1 Per Share To Rs 10 Per Share",
        "EQ",
    )])
    adjusted = pd.read_csv(tmp_path / "NSE" / "SYMBOLS" / "abc.txt")
    assert float(adjusted.loc[0, "CLOSE"]) == 100.0
    assert int(adjusted.loc[0, "VOLUME"]) == 500
    assert int(adjusted.loc[0, "DELIVERY_QTY"]) == 250


def test_a_consolidation_never_rewrites_a_traded_day_as_untraded(tmp_path):
    """4 shares at 10:1 is 0.4 new shares, and rounding that to 0 is a lie.

    On the owner's own BSE tree the 1st-percentile daily volume is 2 shares,
    so this is the ordinary illiquid population a reverse split lands on.
    """

    rows = _history_rows(("20250101", "20250102"), (1.0, 10.0))
    rows.loc[0, ["VOLUME", "TOTAL_TRADES", "DELIVERY_QTY"]] = [4, 2, 4]
    rows.loc[1, ["VOLUME", "TOTAL_TRADES", "DELIVERY_QTY"]] = [900, 20, 400]
    store = SymbolHistoryStore(tmp_path)
    store.upsert("BSE", "EQ", date(2025, 1, 2), rows)
    CorporateActionEngine(tmp_path).apply([CorporateAction(
        "BSE", "ABC", "INE1", date(2025, 1, 2), "consolidation", 0.1,
        "Consolidation Of Equity Shares From Re 1 To Rs 10", "EQ",
    )])
    adjusted = pd.read_csv(tmp_path / "BSE" / "SYMBOLS" / "abc.txt")
    traded = adjusted.loc[adjusted["DATE"] == 20250101].iloc[0]
    assert int(traded["VOLUME"]) == 1
    assert int(traded["DELIVERY_QTY"]) == 1
    # A row claiming zero shares in two trades at 100% delivery is not a
    # rounding nuisance, it is a contradiction.
    assert int(traded["VOLUME"]) > 0 and int(traded["TOTAL_TRADES"]) > 0


def test_a_day_that_did_not_trade_stays_at_zero(tmp_path):
    rows = _history_rows(("20250101", "20250102"), (1.0, 10.0))
    rows.loc[0, ["VOLUME", "TOTAL_TRADES", "DELIVERY_QTY"]] = [0, 0, 0]
    store = SymbolHistoryStore(tmp_path)
    store.upsert("BSE", "EQ", date(2025, 1, 2), rows)
    CorporateActionEngine(tmp_path).apply([CorporateAction(
        "BSE", "ABC", "INE1", date(2025, 1, 2), "consolidation", 0.1,
        "Consolidation Of Equity Shares From Re 1 To Rs 10", "EQ",
    )])
    adjusted = pd.read_csv(tmp_path / "BSE" / "SYMBOLS" / "abc.txt")
    assert int(adjusted.loc[0, "VOLUME"]) == 0


def test_a_repair_is_never_outranked_by_the_row_it_replaces(tmp_path):
    """`_deduplicate` used to keep the higher-volume row per date.

    On an install upgraded from 1.1.0 the stored bar carries the old rule's
    raw volume while a re-download is replayed under the new one. For a
    consolidation the new value is smaller, so the stale row won the rank and
    the repair was discarded with no failure recorded anywhere.
    """

    rows = _history_rows(("20250101", "20250102"), (3.8, 38.0))
    rows.loc[0, ["VOLUME", "DELIVERY_QTY"]] = [300000, 150000]
    store = SymbolHistoryStore(tmp_path)
    store.upsert("NSE", "EQ", date(2025, 1, 2), rows)
    CorporateActionEngine(tmp_path).apply([CorporateAction(
        "NSE", "ABC", "INE1", date(2025, 1, 2), "consolidation", 0.1,
        "Consolidation Of Equity Shares From Re 1 To Rs 10", "EQ",
    )])
    path = tmp_path / "NSE" / "SYMBOLS" / "abc.txt"

    # Put the stored bar back the way version 1.1.0 would have left it:
    # price adjusted, share counts raw.
    legacy = pd.read_csv(path, dtype=str)
    legacy.loc[0, ["VOLUME", "DELIVERY_QTY"]] = ["300000", "150000"]
    legacy.to_csv(path, index=False)

    # A late delivery report now corrects the quantity for the same date.
    repair = rows.iloc[[0]].copy()
    repair.loc[:, "DELIVERY_QTY"] = 222222
    store.upsert("NSE", "EQ", date(2025, 1, 1), repair)

    published = pd.read_csv(path)
    assert int(published.loc[0, "DELIVERY_QTY"]) == 22222
    assert int(published.loc[0, "VOLUME"]) == 30000


def test_adjusted_prices_are_no_longer_snapped_to_a_tick_grid(tmp_path):
    """202.75 / 20 is 10.1375. The pre-3.2 code wrote 10.15."""

    store = SymbolHistoryStore(tmp_path)
    store.upsert("NSE", "EQ", date(2016, 12, 1), _history_rows(
        ("20161130", "20161201"), (202.75, 10.14)
    ))
    CorporateActionEngine(tmp_path).apply([
        CorporateAction("NSE", "ABC", "INE1", date(2016, 12, 1), "bonus",
                        2.0, "Bonus 1:1 and split", "EQ"),
        CorporateAction("NSE", "ABC", "INE1", date(2016, 12, 1), "split",
                        10.0, "Bonus 1:1 and split", "EQ"),
    ])
    adjusted = pd.read_csv(tmp_path / "NSE" / "SYMBOLS" / "abc.txt")
    assert float(adjusted.loc[0, "CLOSE"]) == 10.14
    assert round(float(adjusted.loc[0, "CLOSE"]) % 0.05, 4) != 0


def test_a_blank_delivery_field_stays_blank_through_an_adjustment(tmp_path):
    rows = _history_rows()
    rows["DELIVERY_QTY"] = pd.NA
    rows["DELIVERY_PERCENT"] = pd.NA
    store = SymbolHistoryStore(tmp_path)
    store.upsert("NSE", "EQ", date(2025, 1, 2), rows)
    CorporateActionEngine(tmp_path).apply([CorporateAction(
        "NSE", "ABC", "INE1", date(2025, 1, 2), "bonus", 2.0, "Bonus 1:1", "EQ"
    )])
    adjusted = (
        tmp_path / "NSE" / "SYMBOLS" / "abc.txt"
    ).read_text().splitlines()[1]
    # Delivery is legitimately absent for many dates; scaling must not turn a
    # blank into a number, and a share count must not acquire a decimal point.
    assert adjusted.endswith(",,")
    assert adjusted.split(",")[5] == "20"


def test_the_raw_replay_agrees_with_the_engine_column_for_column(tmp_path):
    """Two paths adjust: the engine, and the replay for a re-downloaded row.

    A disagreement is not an error anywhere. `_deduplicate` picks the survivor
    by volume rank, so whichever path scaled the volume simply wins and the
    history drifts in silence.
    """

    rows = _history_rows(("20250101", "20250102"), (137.77, 45.9))
    rows.loc[0, ["VOLUME", "TOTAL_TRADES", "DELIVERY_QTY"]] = [12345, 67, 4321]
    store = SymbolHistoryStore(tmp_path)
    store.upsert("NSE", "EQ", date(2025, 1, 2), rows)
    CorporateActionEngine(tmp_path).apply([CorporateAction(
        "NSE", "ABC", "INE1", date(2025, 1, 2), "split", 3.0,
        "Face Value Split From Rs 3 To Re 1", "EQ",
    )])
    published = pd.read_csv(
        tmp_path / "NSE" / "SYMBOLS" / "abc.txt", dtype=str
    )
    engine_row = published.loc[published["DATE"] == "20250101"].iloc[0]

    replayed = store._apply_recorded_actions(
        "NSE", "ABC", store._history_rows(rows.iloc[[0]]).iloc[0],
        store._read_applied_actions(),
    )
    for column in (
        "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME", "TOTAL_TRADES",
        "QTY_PER_TRADE", "DELIVERY_QTY", "DELIVERY_PERCENT",
    ):
        assert float(replayed[column]) == float(engine_row[column]), column
    assert int(replayed["VOLUME"]) == 12345 * 3


def test_a_consolidation_replay_does_not_revert_the_adjustment(tmp_path):
    """The dangerous direction: a consolidation shrinks the adjusted volume.

    `_deduplicate` keeps the higher-volume row per date, so an unscaled raw
    replay would outrank the adjusted row and quietly undo it.
    """

    rows = _history_rows(("20250101", "20250102"), (10.0, 100.0))
    rows.loc[0, ["VOLUME", "DELIVERY_QTY"]] = [5000, 2500]
    store = SymbolHistoryStore(tmp_path)
    store.upsert("NSE", "EQ", date(2025, 1, 2), rows)
    CorporateActionEngine(tmp_path).apply([CorporateAction(
        "NSE", "ABC", "INE1", date(2025, 1, 2), "consolidation", 0.1,
        "Consolidation Of Equity Shares From Re 1 To Rs 10", "EQ",
    )])
    store.upsert("NSE", "EQ", date(2025, 1, 1), rows.iloc[[0]].copy())

    history = pd.read_csv(tmp_path / "NSE" / "SYMBOLS" / "abc.txt")
    replayed = history.loc[history["DATE"] == 20250101].iloc[0]
    assert float(replayed["CLOSE"]) == 100.0
    assert int(replayed["VOLUME"]) == 500


def test_continuity_failure_does_not_replace_symbol_file(tmp_path):
    rows = _history_rows()
    rows.loc[1, ["OPEN", "HIGH", "LOW", "CLOSE"]] = 200
    store = SymbolHistoryStore(tmp_path)
    store.upsert("NSE", "EQ", date(2025, 1, 2), rows)
    action = CorporateAction(
        "NSE", "ABC", "INE1", date(2025, 1, 2),
        "bonus", 2.0, "Bonus 1:1", "EQ",
    )
    summary = CorporateActionEngine(tmp_path).apply([action])
    assert summary["manual_review"] == 1
    result = pd.read_csv(tmp_path / "NSE" / "SYMBOLS" / "abc.txt")
    assert float(result.loc[0, "CLOSE"]) == 100.0


def test_the_shipped_ledger_document_is_version_three(tmp_path):
    engine = CorporateActionEngine(tmp_path)
    engine._write_ledger(engine._read_ledger())
    document = json.loads(
        (tmp_path / ".state" / "corporate_actions.json").read_text()
    )
    assert document["version"] == 3
