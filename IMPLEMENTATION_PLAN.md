# NSE/BSE Downloader 1.1.0 — અમલીકરણ પ્લાન

## હેતુ

એક date-aware PySide6 downloader બનાવવો જે જૂના અને નવા NSE/BSE URL અને source
column namesને એક canonical outputમાં ફેરવે. Daily segment-wise ભાવકોપી ઉપરાંત
NSE/BSE cash-marketના દરેક symbol માટે `reliance.txt` જેવી historical text file
બનાવવી અને તેમાં split, consolidation તથા bonus corporate actions લાગુ કરવા.

## મુખ્ય નિર્ણયો

- યુઝરને દરેક exchange/segment/date માટે માત્ર એક final daily ભાવકોપી મળશે.
- Delivery report અલગ official fileમાંથી આવે તો પણ તે internal input રહેશે; અલગ
  user-facing delivery file બનાવવામાં નહીં આવે.
- Daily ભાવકોપી official, unadjusted market record તરીકે જાળવવામાં આવશે.
- Corporate-action adjustment માત્ર symbol-wise historical filesમાં લાગુ થશે.
- જૂના/નવા source format બદલાય તો પણ final column order બદલાશે નહીં.
- `Mark Python`ની corporate-action parsing, action discovery અને pending-delivery
  વિચારધારા પુનઃઉપયોગ થશે; તેની storage-specific DuckDB code સીધી નકલ નહીં થાય.

## Canonical daily output

### NSE/BSE Equity અને SME

```text
SYMBOL,DATE,OPEN,HIGH,LOW,CLOSE,VOLUME,DELIVERY_QTY,DELIVERY_PERCENT
```

- NSE delivery join key: normalized `SYMBOL + SERIES`.
- BSE delivery join key: `FinInstrmId + SCRIP CODE`.
- NSE SME symbolsમાં હાલની setting પ્રમાણે `_SME` suffix રહેશે.
- BSE SME series પણ BSE EQ flowમાં જ process થશે.
- Delivery report મોડો મળે તો base ભાવકોપી discard નહીં થાય. Dateને delivery
  pending તરીકે નોંધવામાં આવશે અને પછીની runમાં final file atomically update થશે.

### NSE Futures

```text
SYMBOL,DATE,OPEN,HIGH,LOW,CLOSE,VOLUME,OPEN_INTEREST,CHANGE_IN_OI
```

- નવા UDiFF fields: `OpnIntrst`, `ChngInOpnIntrst`.
- જૂના fields: `OPEN_INT`, `CHG_IN_OI`.
- OI માટે અલગ download જરૂરી નથી.
- હાલની Roman expiry suffix logic જાળવવામાં આવશે.

### Index

Indexની હાલની સાત કૉલમ જાળવવી. Index rows EQ fileમાં append થાય ત્યારે
`DELIVERY_QTY` અને `DELIVERY_PERCENT` ખાલી રાખવા.

## Symbol-wise historical text files

પ્રથમ તબક્કામાં NSE/BSE cash equity અને SME symbols માટે files બનાવવી:

```text
~/NSE_BSE_Data/NSE/SYMBOLS/reliance.txt
~/NSE_BSE_Data/NSE/SYMBOLS/example_sme.txt
~/NSE_BSE_Data/BSE/SYMBOLS/reliance.txt
```

દરેક file comma-delimited text હશે અને એક header ધરાવશે:

```text
DATE,OPEN,HIGH,LOW,CLOSE,VOLUME,SERIES,TOTAL_TRADES,QTY_PER_TRADE,DELIVERY_QTY,DELIVERY_PERCENT
```

નિયમો:

- File name lowercase અને filesystem-safe બનાવવો.
- એક symbol/date માટે માત્ર એક row રાખવી અને rows તારીખ પ્રમાણે ascending રાખવી.
- Daily rerun idempotent હોવી જોઈએ; duplicate row append ન થવી જોઈએ.
- Stable ISIN/security codeથી symbol rename શોધવો.
- Rename વખતે જૂની અને નવી history merge કરવી; date collisionમાં validated primary
  ticker row અને પછી વધારે volume ધરાવતી row પસંદ કરવી.
- File update `.tmp` file અને atomic replace વડે કરવી.
- Delivery report મોડો મળ્યા પછી સંબંધિત symbol/date row પણ update કરવી.
- FO અને Index માટે corporate-action-adjusted symbol histories આ releaseના scopeમાં
  નથી. FO daily outputમાં OI રહેશે.

## Corporate-action engine

### Source અને scope

- NSE: `Mark Python` જેવી equity અને SME corporate-action feed.
- BSE: corporate-action CSV API અને security code/ISIN mapping.
- પ્રથમ releaseમાં:
  - Split
  - Share consolidation/reverse split
  - Equity bonus
- Dividend, rights, merger, demerger અને symbol delisting automatic price adjustmentના
  scope બહાર રહેશે; તે log/reportમાં દેખાડવા.

### Adjustment rule

- માત્ર `DATE < EX_DATE` rows adjust કરવી.
- `OPEN`, `HIGH`, `LOW`, `CLOSE`ને parsed factorથી divide કરવા.
- `Mark Python` compatibility માટે priceને શરૂઆતમાં nearest `0.05` tick પર round કરવો.
- Ex-date અને પછીની rows બદલવી નહીં.
- પ્રથમ implementationમાં `VOLUME`, `DELIVERY_QTY`, `DELIVERY_PERCENT` અને FO OI
  બદલવા નહીં; આ `Mark Python`ની હાલની adjustment semantics સાથે સુસંગત રહેશે.
- એક જ દિવસે એક symbol પર એકથી વધુ valid actions હોય તો factors deterministic
  ક્રમમાં compose કરવા.

### Idempotency અને audit

બંને exchanges માટે action ledger રાખવો. Unique action keyમાં આ values લેવા:

```text
EXCHANGE + ISIN/SECURITY_CODE + EX_DATE + ACTION_TYPE + FACTOR
```

Ledgerમાં source description, parsed factor, applied timestamp, adjusted rows,
status અને warning/error નોંધવા. અગાઉ લાગુ action ફરીથી priceને adjust ન કરે.

### Rebuild strategy

- Raw/canonical daily filesને source of truth ગણવા.
- Symbol files incremental append કરી શકાય, પરંતુ repair/rebuild હંમેશાં raw daily
  files + action ledger પરથી શક્ય હોવું જોઈએ.
- નવી historical corporate action મળે ત્યારે માત્ર અસરગ્રસ્ત symbol file rebuild કરવી.
- Action parsing નિષ્ફળ જાય અથવા post-adjustment continuity validation fail થાય તો
  original symbol file ન બદલવી અને GUI/logમાં manual-review warning બતાવવી.

## `Mark Python`માંથી પુનઃઉપયોગ

લેવા જેવું:

- NSE split/bonus/consolidation factor parsers.
- BSE split/bonus factor parsers અને corporate-action API normalization.
- NSE corporate-action refresh/cache flow.
- BSE action ledgerની idempotency વિચારધારા.
- Pre-ex-date OHLC adjustment અને post-adjustment continuity check.
- Pending delivery dates અને atomic file rewrite pattern.
- Symbol rename માટે stable ISIN mapping.
- જૂના symbol-wise filesના regression scenarios.

સુધારીને લેવું:

- NSE delivery merge symbol-only નહીં, `SYMBOL + SERIES`થી કરવું.
- BSE delivery 404ને holiday માનીને આખી date skip ન કરવી.
- Corporate-action ledger NSE અને BSE બંને માટે એકસરખો બનાવવો.
- DuckDB-specific persistence પર નિર્ભર ન રહેવું.
- Symbol files માટે `.csv` બદલે requested `.txt` extension વાપરવી.
- Column-count સમાન હોય ત્યારે positional rename કરતી append logic દૂર કરી explicit
  canonical column mapping વાપરવી.

## Config અને GUI

નવી settings:

```yaml
download_options:
  include_delivery_data: true
  include_fo_open_interest: true
  generate_symbol_files: true
  apply_corporate_actions: true
  legacy_seven_column_output: false
```

GUI statusમાં દરેક date માટે આ સ્થિતિ દર્શાવવી:

- Price downloaded
- Delivery merged
- Delivery pending
- Symbol files updated
- Corporate actions applied
- Manual review required

GUIમાં automatic/latest mode સાથે optional calendar start/end range અને Exchange,
Date, Options, Progress તથા Status માટે remembered collapsible sections રહેશે.

## Backward compatibility અને update notification

- આ output schema change હોવાથી version `1.1.0` રાખવું.
- એક transition release માટે `legacy_seven_column_output` વિકલ્પ રાખવો.
- Existing user files overwrite કરતાં પહેલાં schema/version ઓળખવી.
- Appની હાલની update notificationમાં આ મુદ્દા સ્પષ્ટ બતાવવા:
  - NSE/BSE delivery quantity અને percentage
  - NSE FO open interest અને change in OI
  - Date-aware old/new URL support
  - Symbol-wise adjusted history files
  - Corporate-action audit અને rebuild support
  - Legacy 7-column compatibility option

## અમલીકરણના તબક્કા

1. નવી feature branch અને canonical schema/source resolver.
2. જૂના/નવા NSE/BSE URLs માટે date-aware adapters.
3. NSE FO OI mapping અને NSE/BSE delivery merge.
4. Pending-delivery state, retry અને atomic daily-file update.
5. Symbol history writer, duplicate handling અને ISIN-based rename.
6. NSE/BSE corporate-action adapters, parsers અને shared ledger.
7. Adjusted symbol rebuild તથા continuity validation.
8. GUI settings/status અને `1.1.0` update notification.
9. Migration/compatibility support અને end-to-end release testing.

## ફરજિયાત tests

- દરેક URL-eraની fixture એકસરખા canonical columns/order આપે.
- Current અને legacy FO input OI values સાચા આપે.
- NSE delivery `SYMBOL + SERIES` join collision test.
- BSE `FinInstrmId + SCRIP CODE` delivery join test.
- Delivery unavailable હોય ત્યારે ભાવકોપી અને checkpoint ન ખોવાય.
- Pending delivery મળ્યા પછી daily તથા symbol file બંને update થાય.
- Symbol rerun duplicate date ન બનાવે.
- NSE/BSE symbol rename સંપૂર્ણ history જાળવે.
- Split, consolidation અને bonus માત્ર pre-ex-date OHLCને એક જ વાર adjust કરે.
- Invalid factor અથવા continuity failure original fileને અસ્પર્શિત રાખે.
- App crash/interruption છતાં atomic writeને કારણે valid `.txt` file રહે.
- `mark_screener` Miniforge environmentમાં focused અને end-to-end tests pass થાય.
