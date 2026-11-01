-- 07_validations.sql: DQ checks
-- File / row (also in 04) / table. Results -> AUDIT.DQ_RESULTS.

SET LOAD_ID = '<load_id of the run being validated>';

-- A. FILE
INSERT INTO SKYPOINTS.AUDIT.DQ_RESULTS (load_id, check_name, severity, failed_rows, total_rows, details)
SELECT $LOAD_ID, 'FILE_NAME_TIMESTAMP_PARSABLE', 'BLOCKING',
       COUNT_IF(src_file_ts IS NULL), COUNT(*),
       ARRAY_AGG(DISTINCT IFF(src_file_ts IS NULL, src_file_name, NULL))
FROM   SKYPOINTS.RAW.RAW_MEMBER_PROFILE WHERE load_id = $LOAD_ID;

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

INSERT INTO SKYPOINTS.AUDIT.DQ_RESULTS (load_id, check_name, severity, failed_rows, total_rows, details)
SELECT $LOAD_ID, 'UNKNOWN_RECORD_TYPE', 'WARNING',
       COUNT_IF(COALESCE(record_type,'') NOT IN ('H','D','T')), COUNT(*), NULL
FROM   SKYPOINTS.RAW.RAW_MEMBER_PROFILE WHERE load_id = $LOAD_ID;

-- raw D-rows should equal staged + rejected (prefer a |T|<count> trailer from source)
INSERT INTO SKYPOINTS.AUDIT.DQ_RESULTS (load_id, check_name, severity, failed_rows, total_rows, details)
WITH r AS (SELECT COUNT_IF(record_type = 'D') n FROM SKYPOINTS.RAW.RAW_MEMBER_PROFILE WHERE load_id = $LOAD_ID),
     s AS (SELECT COUNT(*) n FROM SKYPOINTS.STAGING.STG_MEMBER_PROFILE WHERE load_id = $LOAD_ID),
     j AS (SELECT COUNT(*) n FROM SKYPOINTS.AUDIT.MEMBER_REJECTS     WHERE load_id = $LOAD_ID)
SELECT $LOAD_ID, 'ROW_COUNT_RECONCILIATION', 'BLOCKING',
       ABS(r.n - s.n - j.n), r.n,
       OBJECT_CONSTRUCT('raw_detail', r.n, 'staged', s.n, 'rejected', j.n)
FROM r, s, j;

-- B. ROW rule summary (rules applied in 04)
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

-- C. TABLE
INSERT INTO SKYPOINTS.AUDIT.DQ_RESULTS (load_id, check_name, severity, failed_rows, total_rows, details)
SELECT $LOAD_ID, 'DUPLICATE_MEMBER_ID_IN_FILE', 'WARNING', COUNT(*), NULL,
       ARRAY_AGG(OBJECT_CONSTRUCT('member_id', member_id, 'file', src_file_name, 'rows', n)) WITHIN GROUP (ORDER BY n DESC)
FROM (
    SELECT member_id, src_file_name, COUNT(*) n
    FROM   SKYPOINTS.STAGING.STG_MEMBER_PROFILE
    WHERE  load_id = $LOAD_ID
    GROUP BY 1, 2 HAVING COUNT(*) > 1
);

INSERT INTO SKYPOINTS.AUDIT.DQ_RESULTS (load_id, check_name, severity, failed_rows, total_rows, details)
SELECT $LOAD_ID, 'MEMBER_ID_WITH_MULTIPLE_NAMES', 'WARNING', COUNT(*), NULL,
       ARRAY_AGG(member_id)
FROM (
    SELECT member_id FROM SKYPOINTS.STAGING.STG_MEMBER_PROFILE
    WHERE  load_id = $LOAD_ID
    GROUP BY 1 HAVING COUNT(DISTINCT UPPER(member_name)) > 1
);

-- Spec uses name as key; names collide. We key on member_id.
INSERT INTO SKYPOINTS.AUDIT.DQ_RESULTS (load_id, check_name, severity, failed_rows, total_rows, details)
SELECT $LOAD_ID, 'SPEC_KEY_MEMBER_NAME_NOT_UNIQUE', 'WARNING', COUNT(*), NULL, NULL
FROM (
    SELECT UPPER(member_name) FROM SKYPOINTS.STAGING.STG_MEMBER_PROFILE
    WHERE  load_id = $LOAD_ID
    GROUP BY 1 HAVING COUNT(DISTINCT member_id) > 1
);

INSERT INTO SKYPOINTS.AUDIT.DQ_RESULTS (load_id, check_name, severity, failed_rows, total_rows, details)
SELECT $LOAD_ID, 'MEMBER_IN_MULTIPLE_COUNTRY_TABLES', 'BLOCKING', COUNT(*), NULL, ARRAY_AGG(member_id)
FROM (
    SELECT member_id FROM SKYPOINTS.CORE.V_MEMBER_CURRENT
    GROUP BY 1 HAVING COUNT(*) > 1
);

INSERT INTO SKYPOINTS.AUDIT.DQ_RESULTS (load_id, check_name, severity, failed_rows, total_rows, details)
SELECT $LOAD_ID, 'WRONG_COUNTRY_IN_TABLE', 'BLOCKING', SUM(n), NULL, OBJECT_AGG(t, n::VARIANT)
FROM (
    SELECT 'TABLE_USA' t,         COUNT_IF(country_code <> 'USA') n FROM SKYPOINTS.CORE.TABLE_USA         UNION ALL
    SELECT 'TABLE_INDIA',         COUNT_IF(country_code <> 'IND')   FROM SKYPOINTS.CORE.TABLE_INDIA       UNION ALL
    SELECT 'TABLE_PHILIPPINES',   COUNT_IF(country_code <> 'PHL')   FROM SKYPOINTS.CORE.TABLE_PHILIPPINES UNION ALL
    SELECT 'TABLE_CANADA',        COUNT_IF(country_code <> 'CAN')   FROM SKYPOINTS.CORE.TABLE_CANADA      UNION ALL
    SELECT 'TABLE_AUSTRALIA',     COUNT_IF(country_code <> 'AUS')   FROM SKYPOINTS.CORE.TABLE_AUSTRALIA
);

INSERT INTO SKYPOINTS.AUDIT.DQ_RESULTS (load_id, check_name, severity, failed_rows, total_rows, details)
SELECT $LOAD_ID, 'XREF_OUT_OF_SYNC', 'BLOCKING', COUNT(*), NULL, NULL
FROM SKYPOINTS.CORE.MEMBER_COUNTRY_XREF x
FULL OUTER JOIN SKYPOINTS.CORE.V_MEMBER_CURRENT m ON m.member_id = x.member_id
WHERE x.member_id IS NULL OR m.member_id IS NULL OR x.country_code <> m.country_code;

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

EXECUTE IMMEDIATE $$
DECLARE
    dq_failed EXCEPTION (-20001, 'Blocking data-quality checks failed; see AUDIT.DQ_RESULTS');
    n NUMBER;
BEGIN
    SELECT COUNT(*) INTO :n
    FROM   SKYPOINTS.AUDIT.DQ_RESULTS
    WHERE  load_id = $LOAD_ID AND severity = 'BLOCKING' AND failed_rows > 0;
    IF (n > 0) THEN
        RAISE dq_failed;
    END IF;
    RETURN 'DQ PASSED';
END;
$$;
