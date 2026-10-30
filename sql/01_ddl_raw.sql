-- =====================================================================
-- 01_ddl_raw.sql  —  LANDING / RAW layer (Snowflake)
-- ---------------------------------------------------------------------
-- Principle: land the bytes exactly as received. Every column is VARCHAR
-- so a malformed value (e.g. "2021-13-13", "3051985") never fails the
-- COPY. Typing, cleansing and rejection happen in STAGING, where we can
-- record *why* a row was rejected instead of losing it.
-- =====================================================================

CREATE DATABASE IF NOT EXISTS SKYPOINTS;
CREATE SCHEMA   IF NOT EXISTS SKYPOINTS.RAW;
CREATE SCHEMA   IF NOT EXISTS SKYPOINTS.STAGING;
CREATE SCHEMA   IF NOT EXISTS SKYPOINTS.CORE;     -- per-country targets
CREATE SCHEMA   IF NOT EXISTS SKYPOINTS.AUDIT;    -- rejects, load log, DQ results

USE SCHEMA SKYPOINTS.RAW;

-- ---------------------------------------------------------------------
-- File formats
-- ---------------------------------------------------------------------
-- Detail rows look like:  |D|Elena|223457|20101012|...|A
-- The leading '|' produces an empty first field, so the record type
-- ('H' / 'D' / 'T') is the SECOND field.
CREATE FILE FORMAT IF NOT EXISTS FF_MEMBER_PIPE
    TYPE                          = CSV
    FIELD_DELIMITER               = '|'
    SKIP_HEADER                   = 0          -- header row is filtered by record type, not position
    FIELD_OPTIONALLY_ENCLOSED_BY  = NONE
    TRIM_SPACE                    = TRUE
    EMPTY_FIELD_AS_NULL           = TRUE
    ERROR_ON_COLUMN_COUNT_MISMATCH = FALSE     -- short/long rows are caught by DQ, not by COPY
    ENCODING                      = 'UTF8';

CREATE FILE FORMAT IF NOT EXISTS FF_REDEMPTION_JSON
    TYPE              = JSON
    STRIP_OUTER_ARRAY = TRUE;                  -- accepts one object per file OR an array of objects

-- External stage on the landing bucket. Naming spec (see README):
--   SKYPOINTS_MBR_<YYYYMMDD>_<HHMMSSTT>.dat    member profile flat file
--   SKYPOINTS_RDM_<YYYYMMDD>_<HHMMSSTT>.json   partner redemption feed
CREATE STAGE IF NOT EXISTS STG_SKYPOINTS_LANDING
    URL = 's3://skypoints-landing/inbound/'
    STORAGE_INTEGRATION = SI_SKYPOINTS_S3;

-- ---------------------------------------------------------------------
-- RAW_MEMBER_PROFILE : one row per physical line of the flat file
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS RAW_MEMBER_PROFILE (
    record_type        VARCHAR(1),          -- H | D | T
    member_name        VARCHAR,
    member_id          VARCHAR,
    enrollment_date    VARCHAR,
    last_flight_date   VARCHAR,
    tier_code          VARCHAR,
    agent_name         VARCHAR,
    state              VARCHAR,
    country            VARCHAR,
    post_code          VARCHAR,             -- in the spec (pos 9) but absent from the sample header
    dob                VARCHAR,
    is_active          VARCHAR,
    -- lineage
    src_file_name      VARCHAR      NOT NULL,
    src_file_ts        TIMESTAMP_NTZ,       -- parsed from the file name; drives "latest record wins"
    src_row_number     NUMBER       NOT NULL,
    raw_line           VARCHAR,             -- full original line, kept for forensic replay
    load_id            VARCHAR      NOT NULL,
    loaded_at          TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP()
)
CLUSTER BY (TO_DATE(src_file_ts))
COMMENT = 'Landing copy of the daily member flat file. All VARCHAR; never updated, only appended.';

-- ---------------------------------------------------------------------
-- RAW_REDEMPTION_FEED : one row per JSON document
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS RAW_REDEMPTION_FEED (
    payload            VARIANT      NOT NULL,
    src_file_name      VARCHAR      NOT NULL,
    src_file_ts        TIMESTAMP_NTZ,
    src_row_number     NUMBER,
    load_id            VARCHAR      NOT NULL,
    loaded_at          TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP()
)
CLUSTER BY (TO_DATE(src_file_ts))
COMMENT = 'Landing copy of partner redemption JSON documents, untouched.';

-- ---------------------------------------------------------------------
-- Ingestion.  At "billions of rows per day" use Snowpipe (auto-ingest on
-- S3 event notifications) so files load continuously in parallel, rather
-- than one big batch COPY. COPY tracks loaded files for 64 days, so a
-- re-delivered file is skipped automatically (idempotent).
-- ---------------------------------------------------------------------
CREATE PIPE IF NOT EXISTS PIPE_MEMBER_PROFILE AUTO_INGEST = TRUE AS
COPY INTO RAW_MEMBER_PROFILE (
    record_type, member_name, member_id, enrollment_date, last_flight_date,
    tier_code, agent_name, state, country, dob, is_active,
    src_file_name, src_file_ts, src_row_number, raw_line, load_id)
FROM (
    SELECT  $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
            METADATA$FILENAME,
            TRY_TO_TIMESTAMP_NTZ(
                REGEXP_SUBSTR(METADATA$FILENAME, '_(\\d{8}_\\d{6})\\d{2}\\.', 1, 1, 'e', 1),
                'YYYYMMDD_HH24MISS'),
            METADATA$FILE_ROW_NUMBER,
            NULL,                                   -- populated by a raw-line pipe if forensic copy is needed
            METADATA$FILENAME || ':' || TO_VARCHAR(METADATA$START_SCAN_TIME, 'YYYYMMDDHH24MISSFF3')
    FROM @STG_SKYPOINTS_LANDING
)
PATTERN     = '.*SKYPOINTS_MBR_[0-9]{8}_[0-9]{8}\\.dat'
FILE_FORMAT = (FORMAT_NAME = 'FF_MEMBER_PIPE')
ON_ERROR    = CONTINUE;

CREATE PIPE IF NOT EXISTS PIPE_REDEMPTION_FEED AUTO_INGEST = TRUE AS
COPY INTO RAW_REDEMPTION_FEED (payload, src_file_name, src_file_ts, src_row_number, load_id)
FROM (
    SELECT  $1,
            METADATA$FILENAME,
            TRY_TO_TIMESTAMP_NTZ(
                REGEXP_SUBSTR(METADATA$FILENAME, '_(\\d{8}_\\d{6})\\d{2}\\.', 1, 1, 'e', 1),
                'YYYYMMDD_HH24MISS'),
            METADATA$FILE_ROW_NUMBER,
            METADATA$FILENAME || ':' || TO_VARCHAR(METADATA$START_SCAN_TIME, 'YYYYMMDDHH24MISSFF3')
    FROM @STG_SKYPOINTS_LANDING
)
PATTERN     = '.*SKYPOINTS_RDM_[0-9]{8}_[0-9]{8}\\.json'
FILE_FORMAT = (FORMAT_NAME = 'FF_REDEMPTION_JSON')
ON_ERROR    = CONTINUE;

-- Streams let downstream tasks process only newly landed rows
-- (incremental, no full rescans of a multi-billion-row table).
CREATE STREAM IF NOT EXISTS STRM_RAW_MEMBER_PROFILE  ON TABLE RAW_MEMBER_PROFILE  APPEND_ONLY = TRUE;
CREATE STREAM IF NOT EXISTS STRM_RAW_REDEMPTION_FEED ON TABLE RAW_REDEMPTION_FEED APPEND_ONLY = TRUE;
