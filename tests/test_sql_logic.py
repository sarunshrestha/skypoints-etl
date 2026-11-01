"""Execute the *Snowflake* expressions from sql/ against DuckDB (via sqlglot
transpilation) and check they agree with the Python reference.

This doesn't replace running on Snowflake, but it catches the classic bugs
(off-by-one age, wrong tie-break in latest-wins) without a warehouse.
"""
from datetime import date

import pytest

duckdb = pytest.importorskip("duckdb")
sqlglot = pytest.importorskip("sqlglot")

from skypoints.dates import age_on  # noqa: E402

# Same expressions as sql/04_load_staging.sql (column names simplified)
AGE_EXPR = """
    DATEDIFF('year', dob, as_of)
    - IFF(DATEADD('year', DATEDIFF('year', dob, as_of), dob) > as_of, 1, 0)
"""
STALE_EXPR = "DATEDIFF('day', COALESCE(last_flight_date, enrollment_date), as_of) > 90"

# Shape of WRK_LATEST in sql/05_split_countries.sql
LATEST_SQL = """
SELECT member_id, country_code
FROM   stg
QUALIFY ROW_NUMBER() OVER (PARTITION BY member_id
                           ORDER BY src_file_ts DESC, src_row_number DESC) = 1
ORDER BY member_id
"""


def run_sf(con, sql):
    return con.execute(sqlglot.transpile(sql, read="snowflake", write="duckdb")[0]).fetchall()


@pytest.fixture
def con():
    return duckdb.connect()


@pytest.mark.parametrize("dob, as_of", [
    (date(1985, 3, 5), date(2024, 3, 4)),
    (date(1985, 3, 5), date(2024, 3, 5)),
    (date(2000, 12, 31), date(2001, 1, 1)),
    (date(1998, 12, 1), date(2024, 1, 15)),
])
def test_snowflake_age_expression_matches_python(con, dob, as_of):
    (got,), = run_sf(con, f"SELECT {AGE_EXPR} FROM (SELECT DATE '{dob}' AS dob, DATE '{as_of}' AS as_of)")
    assert got == age_on(dob, as_of)


def test_snowflake_stale_expression(con):
    rows = run_sf(con, f"""
        SELECT {STALE_EXPR} FROM (SELECT * FROM VALUES
            (DATE '2024-01-01', DATE '2020-01-01', DATE '2024-03-31'),   -- 90 days
            (DATE '2024-01-01', DATE '2020-01-01', DATE '2024-04-01'),   -- 91 days
            (NULL,              DATE '2024-01-01', DATE '2024-04-01'))   -- never flew
        AS t(last_flight_date, enrollment_date, as_of)""")
    assert [r[0] for r in rows] == [False, True, True]


def test_latest_record_wins_query(con):
    con.execute("""CREATE TABLE stg AS SELECT * FROM (VALUES
        ('7', 'IND', TIMESTAMP '2024-01-01', 2),
        ('7', 'USA', TIMESTAMP '2024-02-01', 2),
        ('7', 'CAN', TIMESTAMP '2024-02-01', 5),   -- same file, later line -> wins
        ('8', 'AUS', TIMESTAMP '2024-01-01', 3)
    ) t(member_id, country_code, src_file_ts, src_row_number)""")
    assert run_sf(con, LATEST_SQL) == [("7", "CAN"), ("8", "AUS")]
