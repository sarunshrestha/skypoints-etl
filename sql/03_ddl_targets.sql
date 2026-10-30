-- =====================================================================
-- 03_ddl_targets.sql  —  CORE layer: per-country target tables (Snowflake)
-- ---------------------------------------------------------------------
-- The brief asks for Table_India, Table_USA, ... .  All country tables
-- share ONE template so their structure can never drift; new countries
-- are created automatically by SP_ENSURE_COUNTRY_TABLES the first time a
-- new code appears in staging (no manual DDL per country).
--
-- Alongside them, MEMBER_COUNTRY_XREF records which country table each
-- member currently lives in. That is what makes "member moved countries"
-- cheap: we look up one row instead of scanning every country table.
-- =====================================================================

USE SCHEMA SKYPOINTS.CORE;

CREATE TABLE IF NOT EXISTS TEMPLATE_COUNTRY_MEMBER (
    member_id            VARCHAR(18)   NOT NULL,
    member_name          VARCHAR(255)  NOT NULL,
    enrollment_date      DATE          NOT NULL,
    last_flight_date     DATE,
    tier_code            VARCHAR(5),
    agent_name           VARCHAR(255),
    state                VARCHAR(5),
    country_code         VARCHAR(3)    NOT NULL,
    post_code            VARCHAR(10),
    dob                  DATE,
    is_active            VARCHAR(1),
    age                  NUMBER(3),
    stale_member         BOOLEAN,
    days_since_flight    NUMBER(6),
    record_hash          VARCHAR(64)   NOT NULL,
    src_file_name        VARCHAR       NOT NULL,
    src_file_ts          TIMESTAMP_NTZ NOT NULL,
    created_at           TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    updated_at           TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT pk_member PRIMARY KEY (member_id)       -- informational in Snowflake; enforced by MERGE + DQ check
)
COMMENT = 'Structure template for every TABLE_<COUNTRY>. Never loaded directly.';

-- The five countries in the sample (others are created on demand) --------
CREATE TABLE IF NOT EXISTS TABLE_USA          LIKE TEMPLATE_COUNTRY_MEMBER;
CREATE TABLE IF NOT EXISTS TABLE_INDIA        LIKE TEMPLATE_COUNTRY_MEMBER;
CREATE TABLE IF NOT EXISTS TABLE_PHILIPPINES  LIKE TEMPLATE_COUNTRY_MEMBER;
CREATE TABLE IF NOT EXISTS TABLE_CANADA       LIKE TEMPLATE_COUNTRY_MEMBER;
CREATE TABLE IF NOT EXISTS TABLE_AUSTRALIA    LIKE TEMPLATE_COUNTRY_MEMBER;

-- For lookups by member_id on billion-row tables, search optimisation
-- turns point lookups/MERGE probes into micro-partition pruning.
ALTER TABLE TABLE_USA         ADD SEARCH OPTIMIZATION ON EQUALITY(member_id);
ALTER TABLE TABLE_INDIA       ADD SEARCH OPTIMIZATION ON EQUALITY(member_id);
ALTER TABLE TABLE_PHILIPPINES ADD SEARCH OPTIMIZATION ON EQUALITY(member_id);
ALTER TABLE TABLE_CANADA      ADD SEARCH OPTIMIZATION ON EQUALITY(member_id);
ALTER TABLE TABLE_AUSTRALIA   ADD SEARCH OPTIMIZATION ON EQUALITY(member_id);

-- Where is each member *now*? -------------------------------------------
CREATE TABLE IF NOT EXISTS MEMBER_COUNTRY_XREF (
    member_id        VARCHAR(18)   NOT NULL PRIMARY KEY,
    country_code     VARCHAR(3)    NOT NULL,
    target_table     VARCHAR(100)  NOT NULL,
    src_file_ts      TIMESTAMP_NTZ NOT NULL,     -- timestamp of the record that placed them here
    src_row_number   NUMBER        NOT NULL,
    updated_at       TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP()
)
CLUSTER BY (member_id);

-- History of country moves (useful for lounge-access audits & analytics)
CREATE TABLE IF NOT EXISTS MEMBER_COUNTRY_MOVES (
    member_id          VARCHAR(18)   NOT NULL,
    from_country_code  VARCHAR(3),
    to_country_code    VARCHAR(3)    NOT NULL,
    moved_on_file_ts   TIMESTAMP_NTZ NOT NULL,
    recorded_at        TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP()
);

-- Final redemption fact table (deduplicated on txn_id) -------------------
CREATE TABLE IF NOT EXISTS FACT_REDEMPTION (
    txn_id           VARCHAR(32)   NOT NULL PRIMARY KEY,
    member_id        VARCHAR(18)   NOT NULL,
    txn_date         DATE,
    partner          VARCHAR(100),
    miles_redeemed   NUMBER(12,0),
    status           VARCHAR(20),
    feed_date        DATE,
    src_file_name    VARCHAR,
    updated_at       TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP()
)
CLUSTER BY (txn_date, member_id);

-- ---------------------------------------------------------------------
-- Create a country table the first time we see a new country.
-- ---------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE SP_ENSURE_COUNTRY_TABLES()
RETURNS VARCHAR
LANGUAGE SQL
AS
$$
DECLARE
    created NUMBER DEFAULT 0;
    c CURSOR FOR
        SELECT DISTINCT r.target_table
        FROM   SKYPOINTS.STAGING.REF_COUNTRY_ALIAS r
        WHERE  NOT EXISTS (
                 SELECT 1 FROM SKYPOINTS.INFORMATION_SCHEMA.TABLES t
                 WHERE  t.table_schema = 'CORE' AND t.table_name = r.target_table);
BEGIN
    FOR rec IN c DO
        EXECUTE IMMEDIATE 'CREATE TABLE IF NOT EXISTS SKYPOINTS.CORE.' || rec.target_table ||
                          ' LIKE SKYPOINTS.CORE.TEMPLATE_COUNTRY_MEMBER';
        created := created + 1;
    END FOR;
    RETURN 'created ' || created || ' country table(s)';
END;
$$;
