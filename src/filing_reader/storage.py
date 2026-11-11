"""Persistent filing preparation and explanation cache in PostgreSQL."""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "filing_reader"
VERSION = "reader-1"


def database():
    load_dotenv(ROOT / ".env")
    url = os.getenv("db_connection_string")
    if not url:
        raise RuntimeError("Set db_connection_string in .env to a PostgreSQL URL.")
    return create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 5})


def initialize(engine):
    # Dedicated tables preserve the existing financial-comparison project's data.
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS reader_filings (
                id TEXT PRIMARY KEY,
                payload JSONB NOT NULL
            )
        """))
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS reader_explanations (
                filing_id TEXT REFERENCES reader_filings(id),
                fact_id TEXT NOT NULL,
                version TEXT NOT NULL,
                payload JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (filing_id, fact_id, version)
            )
        """))


def save_filing(engine, payload):
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO reader_filings(id, payload) VALUES (:id, CAST(:payload AS JSONB))
            ON CONFLICT(id) DO UPDATE SET payload = EXCLUDED.payload
        """), {"id": payload["id"], "payload": json.dumps(payload)})


def load_filing(engine, filing_id):
    with engine.connect() as connection:
        return connection.execute(text("SELECT payload FROM reader_filings WHERE id=:id"),
                                  {"id": filing_id}).scalar_one_or_none()


def cached_explanation(connection, filing_id, fact_id, version):
    return connection.execute(text("""
        SELECT payload FROM reader_explanations
        WHERE filing_id=:filing AND fact_id=:fact AND version=:version
    """), {"filing": filing_id, "fact": fact_id, "version": version}).scalar_one_or_none()


def save_explanation(connection, filing_id, fact_id, version, payload):
    connection.execute(text("""
        INSERT INTO reader_explanations(filing_id, fact_id, version, payload)
        VALUES (:filing, :fact, :version, CAST(:payload AS JSONB))
        ON CONFLICT(filing_id, fact_id, version) DO NOTHING
    """), {"filing": filing_id, "fact": fact_id, "version": version,
           "payload": json.dumps(payload)})
