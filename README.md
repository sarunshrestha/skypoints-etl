# SkyPoints ETL

Incubyte assessment. Member flat file + redemption JSON into per-country tables.

SQL under `sql/` is the Snowflake design. Python under `src/skypoints/` runs the same rules locally for tests and demo.

## Run

```bash
pip install -r requirements-dev.txt
python -m pytest -q

PYTHONPATH=src python -m skypoints.pipeline \
  --input data/sample --legacy data/legacy --out out --as-of 2024-01-15
```

`./demo.sh` loads day 1 (sample + legacy), then day 2 (Ravi IND→CAN, RX10092 PENDING→COMPLETED).

## Decisions

- **Key:** `member_id`, not Member_Name. Spec marks name as key; legacy has two different Mikes. Confirm with business.
- **Stale_Member:** days since last flight > 90. If never flew, use enrollment date (flight is optional).
- **Latest record wins:** filename timestamp, then line number - not flight date, not arrival time.
- **Country moves:** delete from old table + merge into new + xref update in one transaction.
- **Filename:** assumed `SKYPOINTS_<MBR|RDM>_<YYYYMMDD>_<HHMMSSTT>.dat|json` (brief mentions a spec, does not include it).
- **Post_Code:** VARCHAR (spec says INT; leading zeros / alphanumeric codes exist).

## Sample / legacy quirks

PHIL/AU aliases, DOB as MMDDYYYY with leading-zero loss (`3051985`), short member ids, Agent_Name dropped in the intermediate table, AUS month-13 enrollment, USA integer dates, legacy per-file `ID` values (namespaced `IND-1` / `USA-1` / `AUS-1`).

## Layout

| Path | Role |
|---|---|
| `sql/01-03` | DDL: raw, staging, country targets |
| `sql/04` | Staging load (Age, Stale_Member, rejects) |
| `sql/05` | Country split + moves |
| `sql/06` | Redemptions flatten + member join |
| `sql/07` | Validations |
| `data/sample`, `data/legacy` | Brief inputs |
| `data/day2` | Incremental feed for move / status update |

## AI usage

AI was used to draft DDL, repetitive SQL, and test scaffolding. Design decisions (business key, latest-wins ordering, reject vs warn, move handling) and review of the sample/legacy data issues were done by me before finalizing.
