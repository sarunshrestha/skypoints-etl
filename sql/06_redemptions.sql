-- 06_redemptions.sql: JSON redemptions -> flat fact + member join
-- LATERAL FLATTEN: one row per txn, parent member_id / feed_date carried down.

USE SCHEMA SKYPOINTS.STAGING;

BEGIN TRANSACTION;

-- consume the stream with DML so its offset advances on COMMIT
CREATE OR REPLACE TEMPORARY TABLE WRK_RDM_DELTA LIKE SKYPOINTS.RAW.RAW_REDEMPTION_FEED;
INSERT INTO WRK_RDM_DELTA (payload, src_file_name, src_file_ts, src_row_number, load_id, loaded_at)
SELECT payload, src_file_name, src_file_ts, src_row_number, load_id, loaded_at
FROM   SKYPOINTS.RAW.STRM_RAW_REDEMPTION_FEED;

CREATE OR REPLACE TEMPORARY TABLE WRK_REDEMPTION_FLAT AS
SELECT
    f.payload:member_id::VARCHAR                                 AS member_id,
    TRY_TO_DATE(f.payload:feed_date::VARCHAR, 'YYYYMMDD')        AS feed_date,
    r.value:txn_id::VARCHAR                                      AS txn_id,
    TRY_TO_DATE(r.value:txn_date::VARCHAR, 'YYYYMMDD')           AS txn_date,
    r.value:partner::VARCHAR                                     AS partner,
    TRY_TO_NUMBER(r.value:miles_redeemed::VARCHAR)               AS miles_redeemed,
    UPPER(r.value:status::VARCHAR)                               AS status,
    r.value                                                      AS raw_txn,
    r.value:txn_date::VARCHAR                                    AS raw_txn_date,
    r.value:miles_redeemed::VARCHAR                              AS raw_miles,
    f.src_file_name, f.src_file_ts, f.load_id
FROM WRK_RDM_DELTA f,
     LATERAL FLATTEN(INPUT => f.payload:redemptions, OUTER => TRUE) r;

CREATE OR REPLACE TEMPORARY TABLE WRK_REDEMPTION_CHECKED AS
SELECT w.*,
    ARRAY_COMPACT(ARRAY_CONSTRUCT(
        IFF(w.member_id IS NULL,  'MANDATORY_MISSING:member_id', NULL),
        IFF(w.raw_txn   IS NULL,  'EMPTY_REDEMPTIONS_ARRAY', NULL),
        IFF(w.raw_txn IS NOT NULL AND w.txn_id IS NULL, 'MANDATORY_MISSING:txn_id', NULL),
        IFF(w.raw_txn_date IS NOT NULL AND w.txn_date IS NULL, 'INVALID_DATE:txn_date=' || w.raw_txn_date, NULL),
        IFF(w.raw_miles IS NOT NULL AND w.miles_redeemed IS NULL, 'INVALID_NUMBER:miles_redeemed=' || w.raw_miles, NULL),
        IFF(w.miles_redeemed <= 0, 'NON_POSITIVE_MILES', NULL)
    )) AS reject_reasons,
    ARRAY_COMPACT(ARRAY_CONSTRUCT(
        IFF(w.status NOT IN ('COMPLETED','PENDING','CANCELLED','REVERSED'), 'UNKNOWN_STATUS:' || w.status, NULL),
        IFF(w.txn_date > w.feed_date, 'TXN_AFTER_FEED_DATE', NULL)
    )) AS dq_warnings
FROM WRK_REDEMPTION_FLAT w;

INSERT INTO SKYPOINTS.AUDIT.REDEMPTION_REJECTS (src_file_name, reject_reasons, raw_record, load_id)
SELECT src_file_name, reject_reasons,
       OBJECT_CONSTRUCT_KEEP_NULL('member_id', member_id, 'txn', raw_txn), load_id
FROM   WRK_REDEMPTION_CHECKED
WHERE  ARRAY_SIZE(reject_reasons) > 0;

INSERT INTO STG_REDEMPTION_TXN
      (txn_id, member_id, feed_date, txn_date, partner, miles_redeemed, status,
       dq_warnings, src_file_name, src_file_ts, load_id)
SELECT txn_id, member_id, feed_date, txn_date, partner, miles_redeemed, status,
       dq_warnings, src_file_name, src_file_ts, load_id
FROM   WRK_REDEMPTION_CHECKED
WHERE  ARRAY_SIZE(reject_reasons) = 0;

-- txn_id is the key; latest feed_date wins (e.g. PENDING -> COMPLETED)
MERGE INTO SKYPOINTS.CORE.FACT_REDEMPTION t
USING (
    SELECT * FROM WRK_REDEMPTION_CHECKED
    WHERE  ARRAY_SIZE(reject_reasons) = 0
    QUALIFY ROW_NUMBER() OVER (PARTITION BY txn_id ORDER BY feed_date DESC, src_file_ts DESC) = 1
) s
ON t.txn_id = s.txn_id
WHEN MATCHED AND s.feed_date >= t.feed_date THEN UPDATE SET
    member_id = s.member_id, txn_date = s.txn_date, partner = s.partner,
    miles_redeemed = s.miles_redeemed, status = s.status, feed_date = s.feed_date,
    src_file_name = s.src_file_name, updated_at = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (txn_id, member_id, txn_date, partner, miles_redeemed, status, feed_date, src_file_name)
    VALUES (s.txn_id, s.member_id, s.txn_date, s.partner, s.miles_redeemed, s.status, s.feed_date, s.src_file_name);

COMMIT;

USE SCHEMA SKYPOINTS.CORE;

CREATE OR REPLACE VIEW V_REDEMPTION_ENRICHED AS
SELECT
    f.txn_id, f.txn_date, f.partner, f.miles_redeemed, f.status, f.feed_date,
    f.member_id,
    m.member_name, m.tier_code, m.country_code, m.state, m.is_active, m.stale_member,
    (m.member_id IS NULL) AS is_orphan_redemption
FROM FACT_REDEMPTION f
LEFT JOIN V_MEMBER_CURRENT m
       ON m.member_id = f.member_id;
