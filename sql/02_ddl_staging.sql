-- =====================================================================
-- 02_ddl_staging.sql  —  STAGING layer (Snowflake)
-- ---------------------------------------------------------------------
-- Typed, cleansed, validated copy of each landed detail record, plus the
-- derived columns asked for in the brief (AGE, STALE_MEMBER).
-- Rows that fail a blocking check go to AUDIT.MEMBER_REJECTS instead.
-- =====================================================================

USE SCHEMA SKYPOINTS.STAGING;

CREATE TABLE IF NOT EXISTS STG_MEMBER_PROFILE (
    member_id            VARCHAR(18)   NOT NULL,   -- business key (see README: spec marks Name as key; we disagree)
    member_name          VARCHAR(255)  NOT NULL,
    enrollment_date      DATE          NOT NULL,
    last_flight_date     DATE,
    tier_code            VARCHAR(5),
    agent_name           VARCHAR(255),
    state                VARCHAR(5),
    country_code_raw     VARCHAR(5),               -- exactly what the source sent (PHIL, AU, ...)
    country_code         VARCHAR(3),               -- ISO-3166 alpha-3 after normalisation (PHL, AUS, ...)
    post_code            VARCHAR(10),              -- VARCHAR, not INT: leading zeros and alpha codes (CAN "M5V") exist
    dob                  DATE,
    is_active            VARCHAR(1),
    -- derived (brief deliverable #2)
    age                  NUMBER(3),                -- whole years as of AS_OF_DATE
    stale_member         BOOLEAN,                  -- days since last flight > 90 as of AS_OF_DATE
    days_since_flight    NUMBER(6),
    as_of_date           DATE          NOT NULL,   -- date the derived values were computed for
    -- soft data-quality warnings (row is kept, but flagged)
    dq_warnings          ARRAY,
    -- lineage / ordering for "latest record wins"
    src_file_name        VARCHAR       NOT NULL,
    src_file_ts          TIMESTAMP_NTZ NOT NULL,
    src_row_number       NUMBER        NOT NULL,
    record_hash          VARCHAR(64)   NOT NULL,   -- SHA2 of business columns, for change detection
    load_id              VARCHAR       NOT NULL,
    staged_at            TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP()
)
CLUSTER BY (country_code, TO_DATE(src_file_ts))
COMMENT = 'Typed + validated member records with derived AGE and STALE_MEMBER.';

-- Flattened redemption transactions (brief deliverable #4) ----------------
CREATE TABLE IF NOT EXISTS STG_REDEMPTION_TXN (
    txn_id               VARCHAR(32)   NOT NULL,
    member_id            VARCHAR(18)   NOT NULL,
    feed_date            DATE,
    txn_date             DATE,
    partner              VARCHAR(100),
    miles_redeemed       NUMBER(12,0),
    status               VARCHAR(20),
    dq_warnings          ARRAY,
    src_file_name        VARCHAR       NOT NULL,
    src_file_ts          TIMESTAMP_NTZ,
    load_id              VARCHAR       NOT NULL,
    staged_at            TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP()
)
CLUSTER BY (member_id, txn_date)
COMMENT = 'One row per redemption transaction, flattened from the JSON feed.';

-- Country code reference -------------------------------------------------
-- The sample mixes ISO alpha-3 (USA, IND, CAN) with non-standard codes
-- (PHIL, AU). A governed mapping table beats hard-coded CASE statements:
-- ops can add an alias without a code deploy.
CREATE TABLE IF NOT EXISTS REF_COUNTRY_ALIAS (
    alias          VARCHAR(10)  NOT NULL PRIMARY KEY,
    country_code   VARCHAR(3)   NOT NULL,          -- ISO alpha-3
    country_name   VARCHAR(100) NOT NULL,
    target_table   VARCHAR(100) NOT NULL           -- e.g. TABLE_INDIA
);

MERGE INTO REF_COUNTRY_ALIAS t
USING (
    SELECT * FROM VALUES
        ('USA','USA','United States','TABLE_USA'),
        ('US' ,'USA','United States','TABLE_USA'),
        ('IND','IND','India','TABLE_INDIA'),
        ('IN' ,'IND','India','TABLE_INDIA'),
        ('PHL','PHL','Philippines','TABLE_PHILIPPINES'),
        ('PH' ,'PHL','Philippines','TABLE_PHILIPPINES'),
        ('PHIL','PHL','Philippines','TABLE_PHILIPPINES'),
        ('CAN','CAN','Canada','TABLE_CANADA'),
        ('CA' ,'CAN','Canada','TABLE_CANADA'),
        ('AUS','AUS','Australia','TABLE_AUSTRALIA'),
        ('AU' ,'AUS','Australia','TABLE_AUSTRALIA')
    AS s(alias, country_code, country_name, target_table)
) s
ON t.alias = s.alias
WHEN NOT MATCHED THEN INSERT VALUES (s.alias, s.country_code, s.country_name, s.target_table);

-- Tier reference (used by the validation layer) ---------------------------
CREATE TABLE IF NOT EXISTS REF_TIER (
    tier_code   VARCHAR(5) NOT NULL PRIMARY KEY,
    tier_name   VARCHAR(50) NOT NULL
);
MERGE INTO REF_TIER t
USING (SELECT * FROM VALUES ('SLV','Silver'),('GLD','Gold'),('PLT','Platinum') AS s(tier_code, tier_name)) s
ON t.tier_code = s.tier_code
WHEN NOT MATCHED THEN INSERT VALUES (s.tier_code, s.tier_name);

-- Rejects + DQ audit ------------------------------------------------------
USE SCHEMA SKYPOINTS.AUDIT;

CREATE TABLE IF NOT EXISTS MEMBER_REJECTS (
    src_file_name    VARCHAR,
    src_row_number   NUMBER,
    member_id        VARCHAR,
    reject_reasons   ARRAY,           -- e.g. ["MANDATORY_MISSING:enrollment_date","INVALID_DATE:enrollment_date=2021-13-13"]
    raw_record       VARIANT,         -- the whole raw row as an object, for replay after fix
    load_id          VARCHAR,
    rejected_at      TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP()
);

CREATE TABLE IF NOT EXISTS REDEMPTION_REJECTS (
    src_file_name    VARCHAR,
    reject_reasons   ARRAY,
    raw_record       VARIANT,
    load_id          VARCHAR,
    rejected_at      TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP()
);

CREATE TABLE IF NOT EXISTS DQ_RESULTS (
    load_id          VARCHAR,
    check_name       VARCHAR,
    severity         VARCHAR(10),     -- BLOCKING | WARNING
    failed_rows      NUMBER,
    total_rows       NUMBER,
    details          VARIANT,
    checked_at       TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP()
);
