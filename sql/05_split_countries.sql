-- =====================================================================
-- 05_split_countries.sql  —  STAGING -> TABLE_<COUNTRY>
-- (brief deliverable #3: per-country split with "latest record wins")
-- ---------------------------------------------------------------------
-- Definition of "latest":
--   ORDER BY src_file_ts DESC, src_row_number DESC
--   i.e. the most recently *produced* file wins, and inside one file the
--   later line wins. We deliberately do NOT use last_flight_date: it is
--   optional, describes travel not data currency, and a member who moves
--   country without flying would never be moved.
--
-- Algorithm per run (incremental, driven by a stream on staging):
--   1. Collapse the batch to 1 row per member (latest wins inside batch).
--   2. Compare to MEMBER_COUNTRY_XREF and drop anything OLDER than what
--      is already applied  -> safe against late / replayed files.
--   3. Members whose country changed: DELETE from the old country table,
--      log the move.
--   4. MERGE into the new country table (insert or update if hash changed).
--   5. Upsert MEMBER_COUNTRY_XREF.
-- All of it in ONE transaction so a member is never in 0 or 2 tables.
-- =====================================================================

USE SCHEMA SKYPOINTS.CORE;

CREATE STREAM IF NOT EXISTS SKYPOINTS.STAGING.STRM_STG_MEMBER_PROFILE
    ON TABLE SKYPOINTS.STAGING.STG_MEMBER_PROFILE APPEND_ONLY = TRUE;

CREATE OR REPLACE PROCEDURE SP_SPLIT_MEMBERS_BY_COUNTRY()
RETURNS VARIANT
LANGUAGE SQL
EXECUTE AS CALLER
AS
$$
DECLARE
    moved     NUMBER DEFAULT 0;
    applied   NUMBER DEFAULT 0;
    stmt      VARCHAR;
    tbl_cur   CURSOR FOR SELECT DISTINCT target_table FROM SKYPOINTS.STAGING.REF_COUNTRY_ALIAS;
BEGIN
    CALL SP_ENSURE_COUNTRY_TABLES();

    BEGIN TRANSACTION;

    -- consume the stream with DML so its offset advances on COMMIT
    CREATE OR REPLACE TEMPORARY TABLE WRK_STG_DELTA LIKE SKYPOINTS.STAGING.STG_MEMBER_PROFILE;
    INSERT INTO WRK_STG_DELTA
        SELECT * EXCLUDE (METADATA$ACTION, METADATA$ISUPDATE, METADATA$ROW_ID)
        FROM SKYPOINTS.STAGING.STRM_STG_MEMBER_PROFILE;

    -- 1 + 2 : latest record per member, newer than what is already applied
    CREATE OR REPLACE TEMPORARY TABLE WRK_LATEST AS
    SELECT s.*, r.target_table,
           x.country_code AS prev_country_code,
           x.target_table AS prev_target_table
    FROM   WRK_STG_DELTA s
    JOIN   (SELECT DISTINCT country_code, target_table FROM SKYPOINTS.STAGING.REF_COUNTRY_ALIAS) r
           ON r.country_code = s.country_code
    LEFT JOIN MEMBER_COUNTRY_XREF x
           ON x.member_id = s.member_id
    WHERE  x.member_id IS NULL
       OR  s.src_file_ts > x.src_file_ts
       OR  (s.src_file_ts = x.src_file_ts AND s.src_row_number > x.src_row_number)
    QUALIFY ROW_NUMBER() OVER (PARTITION BY s.member_id
                               ORDER BY s.src_file_ts DESC, s.src_row_number DESC) = 1;

    -- 3 : movers — remove from the table they used to live in
    INSERT INTO MEMBER_COUNTRY_MOVES (member_id, from_country_code, to_country_code, moved_on_file_ts)
    SELECT member_id, prev_country_code, country_code, src_file_ts
    FROM   WRK_LATEST
    WHERE  prev_country_code IS NOT NULL AND prev_country_code <> country_code;
    moved := SQLROWCOUNT;

    FOR t IN tbl_cur DO
        stmt := 'DELETE FROM SKYPOINTS.CORE.' || t.target_table || ' tgt USING WRK_LATEST w ' ||
                'WHERE tgt.member_id = w.member_id ' ||
                'AND w.prev_target_table = ''' || t.target_table || ''' ' ||
                'AND w.target_table <> w.prev_target_table';
        EXECUTE IMMEDIATE stmt;
    END FOR;

    -- 4 : upsert into the member's current country table
    FOR t IN tbl_cur DO
        stmt := 'MERGE INTO SKYPOINTS.CORE.' || t.target_table || ' tgt USING ( ' ||
                '  SELECT * FROM WRK_LATEST WHERE target_table = ''' || t.target_table || ''') src ' ||
                'ON tgt.member_id = src.member_id ' ||
                'WHEN MATCHED AND tgt.record_hash <> src.record_hash THEN UPDATE SET ' ||
                '  member_name = src.member_name, enrollment_date = src.enrollment_date, ' ||
                '  last_flight_date = src.last_flight_date, tier_code = src.tier_code, ' ||
                '  agent_name = src.agent_name, state = src.state, country_code = src.country_code, ' ||
                '  post_code = src.post_code, dob = src.dob, is_active = src.is_active, ' ||
                '  age = src.age, stale_member = src.stale_member, days_since_flight = src.days_since_flight, ' ||
                '  record_hash = src.record_hash, src_file_name = src.src_file_name, ' ||
                '  src_file_ts = src.src_file_ts, updated_at = CURRENT_TIMESTAMP() ' ||
                'WHEN NOT MATCHED THEN INSERT (member_id, member_name, enrollment_date, last_flight_date, ' ||
                '  tier_code, agent_name, state, country_code, post_code, dob, is_active, age, stale_member, ' ||
                '  days_since_flight, record_hash, src_file_name, src_file_ts) VALUES (' ||
                '  src.member_id, src.member_name, src.enrollment_date, src.last_flight_date, src.tier_code, ' ||
                '  src.agent_name, src.state, src.country_code, src.post_code, src.dob, src.is_active, ' ||
                '  src.age, src.stale_member, src.days_since_flight, src.record_hash, src.src_file_name, ' ||
                '  src.src_file_ts)';
        EXECUTE IMMEDIATE stmt;
    END FOR;

    -- 5 : remember where everyone lives now
    MERGE INTO MEMBER_COUNTRY_XREF x
    USING WRK_LATEST w ON x.member_id = w.member_id
    WHEN MATCHED THEN UPDATE SET
        country_code = w.country_code, target_table = w.target_table,
        src_file_ts = w.src_file_ts, src_row_number = w.src_row_number, updated_at = CURRENT_TIMESTAMP()
    WHEN NOT MATCHED THEN INSERT (member_id, country_code, target_table, src_file_ts, src_row_number)
        VALUES (w.member_id, w.country_code, w.target_table, w.src_file_ts, w.src_row_number);
    applied := SQLROWCOUNT;

    COMMIT;
    RETURN OBJECT_CONSTRUCT('members_applied', applied, 'members_moved', moved);
EXCEPTION
    WHEN OTHER THEN
        ROLLBACK;
        RAISE;
END;
$$;

-- ---------------------------------------------------------------------
-- Orchestration: chain the tasks so split runs right after staging load.
-- ---------------------------------------------------------------------
CREATE OR REPLACE TASK SKYPOINTS.STAGING.TSK_LOAD_STAGING
    WAREHOUSE = WH_SKYPOINTS_ETL
    SCHEDULE  = '15 MINUTE'
    WHEN SYSTEM$STREAM_HAS_DATA('SKYPOINTS.RAW.STRM_RAW_MEMBER_PROFILE')
AS
    EXECUTE IMMEDIATE FROM @SKYPOINTS.RAW.STG_SKYPOINTS_LANDING/sql/04_load_staging.sql;

CREATE OR REPLACE TASK SKYPOINTS.CORE.TSK_SPLIT_COUNTRIES
    WAREHOUSE = WH_SKYPOINTS_ETL
    AFTER SKYPOINTS.STAGING.TSK_LOAD_STAGING
    WHEN SYSTEM$STREAM_HAS_DATA('SKYPOINTS.STAGING.STRM_STG_MEMBER_PROFILE')
AS
    CALL SKYPOINTS.CORE.SP_SPLIT_MEMBERS_BY_COUNTRY();

-- ---------------------------------------------------------------------
-- Consumer view: one logical "all members" table with AGE / STALE
-- recomputed at read time (they are time-dependent; see 04 notes).
-- Regenerate when a new country table is created.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW V_MEMBER_CURRENT AS
WITH all_members AS (
    SELECT * FROM TABLE_USA         UNION ALL
    SELECT * FROM TABLE_INDIA       UNION ALL
    SELECT * FROM TABLE_PHILIPPINES UNION ALL
    SELECT * FROM TABLE_CANADA      UNION ALL
    SELECT * FROM TABLE_AUSTRALIA
)
SELECT
    m.* EXCLUDE (age, stale_member, days_since_flight),
    DATEDIFF('year', dob, CURRENT_DATE())
      - IFF(DATEADD('year', DATEDIFF('year', dob, CURRENT_DATE()), dob) > CURRENT_DATE(), 1, 0)  AS age,
    DATEDIFF('day', COALESCE(last_flight_date, enrollment_date), CURRENT_DATE())                AS days_since_flight,
    DATEDIFF('day', COALESCE(last_flight_date, enrollment_date), CURRENT_DATE()) > 90          AS stale_member
FROM all_members m;
