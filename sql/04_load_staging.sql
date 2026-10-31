-- =====================================================================
-- 04_load_staging.sql  —  RAW -> STAGING with AGE and STALE_MEMBER
-- (brief deliverable #2)
-- ---------------------------------------------------------------------
-- Runs as a Snowflake TASK every N minutes. Reads ONLY new rows from the
-- stream, so cost scales with the day's delta, not table size.
--
--   1. Type every field with TRY_* functions (never fail the batch).
--   2. Normalise country codes through REF_COUNTRY_ALIAS.
--   3. Compute AGE and STALE_MEMBER as of :AS_OF_DATE.
--   4. Collect blocking reasons -> AUDIT.MEMBER_REJECTS
--      everything else          -> STAGING.STG_MEMBER_PROFILE
-- =====================================================================

USE SCHEMA SKYPOINTS.STAGING;

-- Pin the "as of" date once per run so every row in a batch is computed
-- against the same day (and re-runs for a past date are reproducible).
SET AS_OF_DATE = CURRENT_DATE();

BEGIN TRANSACTION;

-- ---------------------------------------------------------------------
-- 1) Materialise the delta (this consumes the stream on COMMIT)
-- ---------------------------------------------------------------------
CREATE OR REPLACE TEMPORARY TABLE WRK_MEMBER_TYPED AS
WITH src AS (
    SELECT *
    FROM   SKYPOINTS.RAW.STRM_RAW_MEMBER_PROFILE
    WHERE  record_type = 'D'                              -- drop header / trailer lines
),
typed AS (
    SELECT
        NULLIF(TRIM(member_id), '')                                  AS member_id,
        NULLIF(TRIM(member_name), '')                                AS member_name,
        TRY_TO_DATE(enrollment_date, 'YYYYMMDD')                     AS enrollment_date,
        TRY_TO_DATE(last_flight_date, 'YYYYMMDD')                    AS last_flight_date,
        UPPER(NULLIF(TRIM(tier_code), ''))                           AS tier_code,
        NULLIF(TRIM(agent_name), '')                                 AS agent_name,
        UPPER(NULLIF(TRIM(state), ''))                               AS state,
        UPPER(NULLIF(TRIM(country), ''))                             AS country_code_raw,
        NULLIF(TRIM(post_code), '')                                  AS post_code,
        -- DOB arrives as MMDDYYYY (different from the other dates!).
        -- If something upstream cast it to a number the leading zero is
        -- lost (03051985 -> 3051985, as seen in the brief's staging
        -- table). LPAD restores it; a 7-digit value is only ever a
        -- single-digit month, so this is unambiguous.
        TRY_TO_DATE(LPAD(TRIM(dob), 8, '0'), 'MMDDYYYY')             AS dob,
        UPPER(NULLIF(TRIM(is_active), ''))                           AS is_active,
        -- keep raw strings so reasons can quote the bad value
        enrollment_date  AS raw_enrollment_date,
        last_flight_date AS raw_last_flight_date,
        dob              AS raw_dob,
        src_file_name, src_file_ts, src_row_number, load_id,
        OBJECT_CONSTRUCT_KEEP_NULL(
            'member_name', member_name, 'member_id', member_id,
            'enrollment_date', enrollment_date, 'last_flight_date', last_flight_date,
            'tier_code', tier_code, 'agent_name', agent_name, 'state', state,
            'country', country, 'post_code', post_code, 'dob', dob, 'is_active', is_active
        ) AS raw_record
    FROM src
)
SELECT
    t.*,
    r.country_code,
    r.target_table,
    (rt.tier_code IS NOT NULL)                                       AS tier_is_known,
    -- ---------- derived columns (deliverable #2) ----------
    -- Age in completed years: subtract one if this year's birthday is
    -- still ahead of AS_OF_DATE. (DATEDIFF('year') alone counts calendar
    -- boundaries and would make someone born on 31-Dec one year old on 1-Jan.)
    IFF(t.dob IS NULL, NULL,
        DATEDIFF('year', t.dob, $AS_OF_DATE)
        - IFF(DATEADD('year', DATEDIFF('year', t.dob, $AS_OF_DATE), t.dob) > $AS_OF_DATE, 1, 0)
    )                                                                AS age,
    -- A member who has never flown is measured from enrollment: a member
    -- who joined 2 years ago and never flew is clearly stale.
    DATEDIFF('day', COALESCE(t.last_flight_date, t.enrollment_date), $AS_OF_DATE)
                                                                     AS days_since_flight,
    days_since_flight > 90                                           AS stale_member,
    $AS_OF_DATE::DATE                                                AS as_of_date,
    SHA2(CONCAT_WS('|',
        COALESCE(t.member_name,''), COALESCE(t.member_id,''),
        COALESCE(TO_VARCHAR(t.enrollment_date),''), COALESCE(TO_VARCHAR(t.last_flight_date),''),
        COALESCE(t.tier_code,''), COALESCE(t.agent_name,''), COALESCE(t.state,''),
        COALESCE(r.country_code,''), COALESCE(t.post_code,''), COALESCE(TO_VARCHAR(t.dob),''),
        COALESCE(t.is_active,'')), 256)                              AS record_hash
FROM typed t
LEFT JOIN REF_COUNTRY_ALIAS r
       ON r.alias = t.country_code_raw
LEFT JOIN REF_TIER rt
       ON rt.tier_code = t.tier_code;

-- ---------------------------------------------------------------------
-- 2) Evaluate row-level rules (detail in 07_validations.sql)
--    BLOCKING  -> row rejected
--    WARNING   -> row kept, warning stored in DQ_WARNINGS
-- ---------------------------------------------------------------------
CREATE OR REPLACE TEMPORARY TABLE WRK_MEMBER_CHECKED AS
SELECT
    w.*,
    ARRAY_COMPACT(ARRAY_CONSTRUCT(
        IFF(w.member_id   IS NULL, 'MANDATORY_MISSING:member_id',   NULL),
        IFF(w.member_name IS NULL, 'MANDATORY_MISSING:member_name', NULL),
        IFF(w.raw_enrollment_date IS NULL, 'MANDATORY_MISSING:enrollment_date', NULL),
        IFF(w.raw_enrollment_date IS NOT NULL AND w.enrollment_date IS NULL,
            'INVALID_DATE:enrollment_date=' || w.raw_enrollment_date, NULL),
        IFF(w.raw_last_flight_date IS NOT NULL AND w.last_flight_date IS NULL,
            'INVALID_DATE:last_flight_date=' || w.raw_last_flight_date, NULL),
        IFF(w.raw_dob IS NOT NULL AND w.dob IS NULL,
            'INVALID_DATE:dob=' || w.raw_dob, NULL),
        IFF(LENGTH(w.member_id)   > 18,  'LENGTH_EXCEEDED:member_id',   NULL),
        IFF(LENGTH(w.member_name) > 255, 'LENGTH_EXCEEDED:member_name', NULL),
        IFF(w.member_id IS NOT NULL AND NOT REGEXP_LIKE(w.member_id, '^[A-Za-z0-9-]+$'),
            'INVALID_FORMAT:member_id', NULL),
        -- we cannot route a member to a country table we cannot identify
        IFF(w.country_code IS NULL,
            'UNKNOWN_COUNTRY:' || COALESCE(w.country_code_raw, '<null>'), NULL)
    )) AS reject_reasons,
    ARRAY_COMPACT(ARRAY_CONSTRUCT(
        IFF(w.country_code IS NOT NULL AND w.country_code <> w.country_code_raw,
            'COUNTRY_CODE_NORMALISED:' || w.country_code_raw || '->' || w.country_code, NULL),
        IFF(w.tier_code IS NOT NULL AND NOT w.tier_is_known,
            'UNKNOWN_TIER:' || w.tier_code, NULL),
        IFF(w.is_active IS NOT NULL AND w.is_active NOT IN ('A','I'),
            'UNKNOWN_ACTIVE_FLAG:' || w.is_active, NULL),
        IFF(w.last_flight_date < w.enrollment_date, 'FLIGHT_BEFORE_ENROLLMENT', NULL),
        IFF(w.dob >= w.enrollment_date,            'DOB_NOT_BEFORE_ENROLLMENT', NULL),
        IFF(w.last_flight_date > $AS_OF_DATE,      'FLIGHT_DATE_IN_FUTURE', NULL),
        IFF(w.enrollment_date  > $AS_OF_DATE,      'ENROLLMENT_DATE_IN_FUTURE', NULL),
        IFF(w.age NOT BETWEEN 0 AND 120,           'AGE_OUT_OF_RANGE:' || w.age, NULL),
        IFF(LENGTH(w.raw_dob) = 7,                 'DOB_LEADING_ZERO_RESTORED', NULL),
        IFF(REGEXP_LIKE(w.member_id, '^[0-9]+$') AND LENGTH(w.member_id) < 6, 'MEMBER_ID_SHORTER_THAN_EXPECTED', NULL),
        IFF(LENGTH(w.state) > 5,                   'LENGTH_EXCEEDED:state', NULL)
    )) AS dq_warnings
FROM WRK_MEMBER_TYPED w;

-- ---------------------------------------------------------------------
-- 3) Route
-- ---------------------------------------------------------------------
INSERT INTO SKYPOINTS.AUDIT.MEMBER_REJECTS
       (src_file_name, src_row_number, member_id, reject_reasons, raw_record, load_id)
SELECT  src_file_name, src_row_number, member_id, reject_reasons, raw_record, load_id
FROM    WRK_MEMBER_CHECKED
WHERE   ARRAY_SIZE(reject_reasons) > 0;

INSERT INTO STG_MEMBER_PROFILE (
    member_id, member_name, enrollment_date, last_flight_date, tier_code, agent_name,
    state, country_code_raw, country_code, post_code, dob, is_active,
    age, stale_member, days_since_flight, as_of_date, dq_warnings,
    src_file_name, src_file_ts, src_row_number, record_hash, load_id)
SELECT
    member_id, member_name, enrollment_date, last_flight_date, tier_code, agent_name,
    state, country_code_raw, country_code, post_code, dob, is_active,
    age, stale_member, days_since_flight, as_of_date, dq_warnings,
    src_file_name, src_file_ts, src_row_number, record_hash, load_id
FROM WRK_MEMBER_CHECKED
WHERE ARRAY_SIZE(reject_reasons) = 0;

COMMIT;

-- ---------------------------------------------------------------------
-- NOTE on AGE / STALE_MEMBER being time-dependent
-- These values are correct on AS_OF_DATE and drift every day. Staging
-- stores them (as the brief asks) together with AS_OF_DATE so nobody
-- mistakes them for timeless facts. For consumers we ALSO expose them as
-- computed-at-read columns in CORE.V_MEMBER_CURRENT (05_split_countries.sql),
-- so dashboards never read a stale "stale flag".
-- ---------------------------------------------------------------------
