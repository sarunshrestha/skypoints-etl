-- =====================================================================
-- 07_validations.sql  —  Data-quality checks (brief deliverable #5)
-- ---------------------------------------------------------------------
-- Three levels:
--   A. FILE level   — is this the file we expected, and is it complete?
--   B. ROW level    — implemented inline in 04 (rejects + warnings);
--                     catalogued below for reference.
--   C. TABLE level  — post-load invariants on staging and targets.
-- Every check writes one row to AUDIT.DQ_RESULTS so trends are visible
-- (e.g. "UNKNOWN_COUNTRY went from 0 to 40k overnight").
--
-- Severity:
--   BLOCKING : the row (or file) must not reach the target tables.
--   WARNING  : the row loads, but the issue is recorded and alerted on.
-- =====================================================================

SET LOAD_ID = '<load_id of the run being validated>';

-- ---------------------------------------------------------------------
-- A. FILE-LEVEL CHECKS
-- ---------------------------------------------------------------------

-- A1. File name matches the spec and carries a parsable timestamp.
INSERT INTO SKYPOINTS.AUDIT.DQ_RESULTS (load_id, check_name, severity, failed_rows, total_rows, details)
SELECT $LOAD_ID, 'FILE_NAME_TIMESTAMP_PARSABLE', 'BLOCKING',
       COUNT_IF(src_file_ts IS NULL), COUNT(*),
       ARRAY_AGG(DISTINCT IFF(src_file_ts IS NULL, src_file_name, NULL))
FROM   SKYPOINTS.RAW.RAW_MEMBER_PROFILE WHERE load_id = $LOAD_ID;

-- A2. Exactly one header row, and it matches the agreed layout.
--     (Detects a source silently adding/removing/re-ordering columns —
--      e.g. Post Code is in the spec but missing from the sample header.)
INSERT INTO SKYPOINTS.AUDIT.DQ_RESULTS (load_id, check_name, severity, failed_rows, total_rows, details)
SELECT $LOAD_ID, 'HEADER_PRESENT_AND_MATCHES_LAYOUT', 'BLOCKING',
       IFF(COUNT_IF(record_type = 'H') = 1
           AND COUNT_IF(record_type = 'H'
                        AND member_name = 'Member_Name' AND member_id = 'Member_Id'
                        AND enrollment_date = 'Enrollment_Date' AND last_flight_date = 'Last_Flight_Date'
                        AND tier_code = 'Tier_Code' AND agent_name = 'Agent_Name' AND state = 'State'
                        AND country = 'Country' AND dob = 'DOB' AND is_active = 'Is_Active') = 1, 0, 1),
       COUNT_IF(record_type = 'H'), NULL
FROM   SKYPOINTS.RAW.RAW_MEMBER_PROFILE WHERE load_id = $LOAD_ID;

-- A3. Unknown record types (anything other than H / D / T).
INSERT INTO SKYPOINTS.AUDIT.DQ_RESULTS (load_id, check_name, severity, failed_rows, total_rows, details)
SELECT $LOAD_ID, 'UNKNOWN_RECORD_TYPE', 'WARNING',
       COUNT_IF(COALESCE(record_type,'') NOT IN ('H','D','T')), COUNT(*), NULL
FROM   SKYPOINTS.RAW.RAW_MEMBER_PROFILE WHERE load_id = $LOAD_ID;

-- A4. Completeness / reconciliation.
--     RECOMMENDATION to the source team: add a trailer  |T|<detail_count>
--     Without it we cannot prove a file was not truncated in transit.
--     Until then we reconcile raw D-rows = staged + rejected.
INSERT INTO SKYPOINTS.AUDIT.DQ_RESULTS (load_id, check_name, severity, failed_rows, total_rows, details)
WITH r AS (SELECT COUNT_IF(record_type = 'D') n FROM SKYPOINTS.RAW.RAW_MEMBER_PROFILE WHERE load_id = $LOAD_ID),
     s AS (SELECT COUNT(*) n FROM SKYPOINTS.STAGING.STG_MEMBER_PROFILE WHERE load_id = $LOAD_ID),
     j AS (SELECT COUNT(*) n FROM SKYPOINTS.AUDIT.MEMBER_REJECTS     WHERE load_id = $LOAD_ID)
SELECT $LOAD_ID, 'ROW_COUNT_RECONCILIATION', 'BLOCKING',
       ABS(r.n - s.n - j.n), r.n,
       OBJECT_CONSTRUCT('raw_detail', r.n, 'staged', s.n, 'rejected', j.n)
FROM r, s, j;

-- A5. Volume anomaly: today's detail count vs trailing 7-day average.
INSERT INTO SKYPOINTS.AUDIT.DQ_RESULTS (load_id, check_name, severity, failed_rows, total_rows, details)
WITH daily AS (
    SELECT TO_DATE(src_file_ts) d, COUNT_IF(record_type = 'D') n
    FROM   SKYPOINTS.RAW.RAW_MEMBER_PROFILE
    WHERE  src_file_ts >= DATEADD('day', -8, CURRENT_DATE())
    GROUP BY 1
)
SELECT $LOAD_ID, 'VOLUME_WITHIN_50PCT_OF_7D_AVG', 'WARNING',
       IFF(ABS(t.n - a.avg_n) > 0.5 * a.avg_n, 1, 0), t.n,
       OBJECT_CONSTRUCT('today', t.n, 'avg_7d', a.avg_n)
FROM  (SELECT n FROM daily WHERE d = CURRENT_DATE()) t,
      (SELECT AVG(n) avg_n FROM daily WHERE d < CURRENT_DATE()) a;

-- ---------------------------------------------------------------------
-- B. ROW-LEVEL RULES  (implemented in 04_load_staging.sql)
-- ---------------------------------------------------------------------
--  BLOCKING (row -> AUDIT.MEMBER_REJECTS)
--   MANDATORY_MISSING     member_name, member_id, enrollment_date  (spec: Mandatory = Y)
--   INVALID_DATE          enrollment_date / last_flight_date (YYYYMMDD), dob (MMDDYYYY)
--   LENGTH_EXCEEDED       member_id > 18, member_name > 255
--   INVALID_FORMAT        member_id not numeric
--   UNKNOWN_COUNTRY       country not in REF_COUNTRY_ALIAS -> cannot be routed
--  WARNING (row loads, flag in DQ_WARNINGS)
--   COUNTRY_CODE_NORMALISED   PHIL->PHL, AU->AUS     (seen in sample)
--   DOB_LEADING_ZERO_RESTORED 3051985 -> 03051985    (seen in sample staging)
--   MEMBER_ID_SHORTER_THAN_EXPECTED  22345, 2256     (seen in sample)
--   UNKNOWN_TIER, UNKNOWN_ACTIVE_FLAG
--   FLIGHT_BEFORE_ENROLLMENT, DOB_NOT_BEFORE_ENROLLMENT
--   FLIGHT_DATE_IN_FUTURE, ENROLLMENT_DATE_IN_FUTURE, AGE_OUT_OF_RANGE

-- Row-rule summary into DQ_RESULTS (one row per reason)
INSERT INTO SKYPOINTS.AUDIT.DQ_RESULTS (load_id, check_name, severity, failed_rows, total_rows, details)
SELECT $LOAD_ID, SPLIT_PART(f.value::VARCHAR, ':', 1), 'BLOCKING', COUNT(*), NULL, NULL
FROM   SKYPOINTS.AUDIT.MEMBER_REJECTS r, LATERAL FLATTEN(r.reject_reasons) f
WHERE  r.load_id = $LOAD_ID
GROUP BY 2
UNION ALL
SELECT $LOAD_ID, SPLIT_PART(f.value::VARCHAR, ':', 1), 'WARNING', COUNT(*), NULL, NULL
FROM   SKYPOINTS.STAGING.STG_MEMBER_PROFILE s, LATERAL FLATTEN(s.dq_warnings) f
WHERE  s.load_id = $LOAD_ID
GROUP BY 2;

-- ---------------------------------------------------------------------
-- C. TABLE-LEVEL INVARIANTS
-- ---------------------------------------------------------------------

-- C1. Key uniqueness INSIDE one file. Two rows for the same member in one
--     file are legal (latest line wins) but worth knowing about.
INSERT INTO SKYPOINTS.AUDIT.DQ_RESULTS (load_id, check_name, severity, failed_rows, total_rows, details)
SELECT $LOAD_ID, 'DUPLICATE_MEMBER_ID_IN_FILE', 'WARNING', COUNT(*), NULL,
       ARRAY_AGG(OBJECT_CONSTRUCT('member_id', member_id, 'file', src_file_name, 'rows', n)) WITHIN GROUP (ORDER BY n DESC)
FROM (
    SELECT member_id, src_file_name, COUNT(*) n
    FROM   SKYPOINTS.STAGING.STG_MEMBER_PROFILE
    WHERE  load_id = $LOAD_ID
    GROUP BY 1, 2 HAVING COUNT(*) > 1
);

-- C2. Same member_id, DIFFERENT name inside one load: likely an id
--     collision (two people sharing an id) rather than a real update.
INSERT INTO SKYPOINTS.AUDIT.DQ_RESULTS (load_id, check_name, severity, failed_rows, total_rows, details)
SELECT $LOAD_ID, 'MEMBER_ID_WITH_MULTIPLE_NAMES', 'WARNING', COUNT(*), NULL,
       ARRAY_AGG(member_id)
FROM (
    SELECT member_id FROM SKYPOINTS.STAGING.STG_MEMBER_PROFILE
    WHERE  load_id = $LOAD_ID
    GROUP BY 1 HAVING COUNT(DISTINCT UPPER(member_name)) > 1
);

-- C3. The spec declares Member_Name as the key column. We use member_id
--     instead (names are not unique); this check makes the cost of the
--     spec's choice visible: how many names map to >1 member_id?
INSERT INTO SKYPOINTS.AUDIT.DQ_RESULTS (load_id, check_name, severity, failed_rows, total_rows, details)
SELECT $LOAD_ID, 'SPEC_KEY_MEMBER_NAME_NOT_UNIQUE', 'WARNING', COUNT(*), NULL, NULL
FROM (
    SELECT UPPER(member_name) FROM SKYPOINTS.STAGING.STG_MEMBER_PROFILE
    WHERE  load_id = $LOAD_ID
    GROUP BY 1 HAVING COUNT(DISTINCT member_id) > 1
);

-- C4. A member must live in EXACTLY ONE country table (move logic works).
INSERT INTO SKYPOINTS.AUDIT.DQ_RESULTS (load_id, check_name, severity, failed_rows, total_rows, details)
SELECT $LOAD_ID, 'MEMBER_IN_MULTIPLE_COUNTRY_TABLES', 'BLOCKING', COUNT(*), NULL, ARRAY_AGG(member_id)
FROM (
    SELECT member_id FROM SKYPOINTS.CORE.V_MEMBER_CURRENT
    GROUP BY 1 HAVING COUNT(*) > 1
);

-- C5. Every row in a country table belongs to that country.
INSERT INTO SKYPOINTS.AUDIT.DQ_RESULTS (load_id, check_name, severity, failed_rows, total_rows, details)
SELECT $LOAD_ID, 'WRONG_COUNTRY_IN_TABLE', 'BLOCKING', SUM(n), NULL, OBJECT_AGG(t, n::VARIANT)
FROM (
    SELECT 'TABLE_USA' t,         COUNT_IF(country_code <> 'USA') n FROM SKYPOINTS.CORE.TABLE_USA         UNION ALL
    SELECT 'TABLE_INDIA',         COUNT_IF(country_code <> 'IND')   FROM SKYPOINTS.CORE.TABLE_INDIA       UNION ALL
    SELECT 'TABLE_PHILIPPINES',   COUNT_IF(country_code <> 'PHL')   FROM SKYPOINTS.CORE.TABLE_PHILIPPINES UNION ALL
    SELECT 'TABLE_CANADA',        COUNT_IF(country_code <> 'CAN')   FROM SKYPOINTS.CORE.TABLE_CANADA      UNION ALL
    SELECT 'TABLE_AUSTRALIA',     COUNT_IF(country_code <> 'AUS')   FROM SKYPOINTS.CORE.TABLE_AUSTRALIA
);

-- C6. XREF and country tables agree (same member set).
INSERT INTO SKYPOINTS.AUDIT.DQ_RESULTS (load_id, check_name, severity, failed_rows, total_rows, details)
SELECT $LOAD_ID, 'XREF_OUT_OF_SYNC', 'BLOCKING', COUNT(*), NULL, NULL
FROM SKYPOINTS.CORE.MEMBER_COUNTRY_XREF x
FULL OUTER JOIN SKYPOINTS.CORE.V_MEMBER_CURRENT m ON m.member_id = x.member_id
WHERE x.member_id IS NULL OR m.member_id IS NULL OR x.country_code <> m.country_code;

-- C7. Redemptions: txn_id unique in fact, orphans (no member profile).
INSERT INTO SKYPOINTS.AUDIT.DQ_RESULTS (load_id, check_name, severity, failed_rows, total_rows, details)
SELECT $LOAD_ID, 'ORPHAN_REDEMPTIONS', 'WARNING',
       COUNT_IF(is_orphan_redemption), COUNT(*), NULL
FROM SKYPOINTS.CORE.V_REDEMPTION_ENRICHED;

INSERT INTO SKYPOINTS.AUDIT.DQ_RESULTS (load_id, check_name, severity, failed_rows, total_rows, details)
SELECT $LOAD_ID, 'SAME_TXN_DIFFERENT_MEMBER', 'BLOCKING', COUNT(*), NULL, ARRAY_AGG(txn_id)
FROM (
    SELECT txn_id FROM SKYPOINTS.STAGING.STG_REDEMPTION_TXN
    GROUP BY 1 HAVING COUNT(DISTINCT member_id) > 1
);

-- C8. Suspicious uniformity: a column where every row has the same value
--     usually means a default was injected upstream (in the sample, all
--     five members share DOB 03051985 and dates 20101012/20121013).
INSERT INTO SKYPOINTS.AUDIT.DQ_RESULTS (load_id, check_name, severity, failed_rows, total_rows, details)
SELECT $LOAD_ID, 'SUSPICIOUS_CONSTANT_COLUMN', 'WARNING',
       (IFF(COUNT(DISTINCT dob) = 1 AND COUNT(*) > 1, 1, 0)
      + IFF(COUNT(DISTINCT enrollment_date) = 1 AND COUNT(*) > 1, 1, 0)
      + IFF(COUNT(DISTINCT last_flight_date) = 1 AND COUNT(*) > 1, 1, 0)),
       COUNT(*),
       OBJECT_CONSTRUCT('distinct_dob', COUNT(DISTINCT dob),
                        'distinct_enrollment', COUNT(DISTINCT enrollment_date),
                        'distinct_flight', COUNT(DISTINCT last_flight_date))
FROM SKYPOINTS.STAGING.STG_MEMBER_PROFILE WHERE load_id = $LOAD_ID;

-- C9. Silent column loss between layers. The brief's intermediate table
--     shows Agent_Name blank for 4 of 5 rows although every source row
--     had "Sam". Compare non-null rates raw vs staging per column.
INSERT INTO SKYPOINTS.AUDIT.DQ_RESULTS (load_id, check_name, severity, failed_rows, total_rows, details)
WITH r AS (SELECT COUNT_IF(NULLIF(TRIM(agent_name),'') IS NOT NULL) n FROM SKYPOINTS.RAW.RAW_MEMBER_PROFILE
           WHERE load_id = $LOAD_ID AND record_type = 'D'),
     s AS (SELECT COUNT_IF(agent_name IS NOT NULL) n FROM SKYPOINTS.STAGING.STG_MEMBER_PROFILE
           WHERE load_id = $LOAD_ID),
     j AS (SELECT COUNT_IF(raw_record:agent_name IS NOT NULL AND raw_record:agent_name::VARCHAR <> '') n
           FROM SKYPOINTS.AUDIT.MEMBER_REJECTS WHERE load_id = $LOAD_ID)
SELECT $LOAD_ID, 'COLUMN_VALUES_LOST_RAW_TO_STAGING:agent_name', 'BLOCKING',
       r.n - s.n - j.n, r.n, OBJECT_CONSTRUCT('raw', r.n, 'staged', s.n, 'rejected', j.n)
FROM r, s, j;

-- ---------------------------------------------------------------------
-- Gate: fail the task (and alert) if any BLOCKING check failed.
-- ---------------------------------------------------------------------
EXECUTE IMMEDIATE $$
DECLARE
    dq_failed EXCEPTION (-20001, 'Blocking data-quality checks failed; see AUDIT.DQ_RESULTS');
    n NUMBER;
BEGIN
    SELECT COUNT(*) INTO :n
    FROM   SKYPOINTS.AUDIT.DQ_RESULTS
    WHERE  load_id = $LOAD_ID AND severity = 'BLOCKING' AND failed_rows > 0;
    IF (n > 0) THEN
        RAISE dq_failed;      -- task fails -> error integration alerts on-call
    END IF;
    RETURN 'DQ PASSED';
END;
$$;
