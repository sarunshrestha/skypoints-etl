"""SkyPoints loyalty ETL — reference implementation of the Snowflake pipeline.

The SQL in ``sql/`` is what runs at scale. This package implements the same
rules in plain Python so they can be unit-tested, demoed locally, and used as
an executable specification for the SQL.
"""
