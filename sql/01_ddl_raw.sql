-- 01_ddl_raw.sql: LANDING / RAW (Snowflake)
-- All columns VARCHAR so malformed values never fail COPY.
-- Typing and rejects happen in staging.

CREATE DATABASE IF NOT EXISTS SKYPOINTS;
CREATE SCHEMA   IF NOT EXISTS SKYPOINTS.RAW;
CREATE SCHEMA   IF NOT EXISTS SKYPOINTS.STAGING;
CREATE SCHEMA   IF NOT EXISTS SKYPOINTS.CORE;     -- per-country targets
CREATE SCHEMA   IF NOT EXISTS SKYPOINTS.AUDIT;    -- rejects, load log, DQ results

USE SCHEMA SKYPOINTS.RAW;

-- Leading '|' makes record type (H/D/T) the second field.
CREATE FILE FORMAT IF NOT EXISTS FF_MEMBER_PIPE
    TYPE                          = CSV
    FIELD_DELIMITER               = '|'
    SKIP_HEADER                   = 0
    FIELD_OPTIONALLY_ENCLOSED_BY  = NONE
    TRIM_SPACE                    = TRUE
    EMPTY_FIELD_AS_NULL           = TRUE
    NULL_IF                       = ('NULL', 'null', 'N/A', 'NA', 'NONE')
    ERROR_ON_COLUMN_COUNT_MISMATCH = FALSE
    ENCODING                      = 'UTF8';

CREATE FILE FORMAT IF NOT EXISTS FF_REDEMPTION_JSON
    TYPE              = JSON
    STRIP_OUTER_ARRAY = TRUE;

-- Naming: SKYPOINTS_MBR_<YYYYMMDD>_<HHMMSSTT>.dat
--         SKYPOINTS_RDM_<YYYYMMDD>_<HHMMSSTT>.json
CREATE STAGE IF NOT EXISTS STG_SKYPOINTS_LANDING
    URL = 's3://skypoints-landing/inbound/'
    STORAGE_INTEGRATION = SI_SKYPOINTS_S3;

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
    post_code          VARCHAR,             -- in spec, missing from sample header
    dob                VARCHAR,
    is_active          VARCHAR,
    src_file_name      VARCHAR      NOT NULL,
    src_file_ts        TIMESTAMP_NTZ,       -- from filename; drives latest-record-wins
    src_row_number     NUMBER       NOT NULL,
    raw_line           VARCHAR,
    load_id            VARCHAR      NOT NULL,
    loaded_at          TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP()
)
CLUSTER BY (TO_DATE(src_file_ts))
COMMENT = 'Append-only landing copy of the member flat file. All VARCHAR.';

CREATE TABLE IF NOT EXISTS RAW_REDEMPTION_FEED (
    payload            VARIANT      NOT NULL,
    src_file_name      VARCHAR      NOT NULL,
    src_file_ts        TIMESTAMP_NTZ,
    src_row_number     NUMBER,
    load_id            VARCHAR      NOT NULL,
    loaded_at          TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP()
)
CLUSTER BY (TO_DATE(src_file_ts))
COMMENT = 'Append-only landing copy of redemption JSON documents.';

-- Auto-ingest; re-delivered files are skipped by COPY history.
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
            NULL,
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

CREATE STREAM IF NOT EXISTS STRM_RAW_MEMBER_PROFILE  ON TABLE RAW_MEMBER_PROFILE  APPEND_ONLY = TRUE;
CREATE STREAM IF NOT EXISTS STRM_RAW_REDEMPTION_FEED ON TABLE RAW_REDEMPTION_FEED APPEND_ONLY = TRUE;
