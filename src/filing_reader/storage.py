"""Persistent filing preparation and explanation cache in PostgreSQL."""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
VERSION = "reader-7"


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

        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS reader_documents (
                filing_id TEXT NOT NULL REFERENCES reader_filings(id),
                document_id TEXT NOT NULL,
                html TEXT NOT NULL,
                PRIMARY KEY (filing_id, document_id)
            )
        """))


def save_filing(engine, payload, documents):
    query = text("""
        INSERT INTO reader_filings(id, payload) VALUES (:id, CAST(:payload AS JSONB))
        ON CONFLICT(id) DO UPDATE SET payload = EXCLUDED.payload
    """)
    parameters = {"id": payload["id"], "payload": json.dumps(payload)}
    with engine.begin() as connection:
        connection.execute(query, parameters)
        connection.execute(
            text("DELETE FROM reader_documents WHERE filing_id = :filing_id"),
            {"filing_id": payload["id"]},
        )
        insert_document = text("""
            INSERT INTO reader_documents (filing_id, document_id, html)
            VALUES (:filing_id, :document_id, :html)
        """)
        rows = [
            {"filing_id": payload["id"], "document_id": document_id, "html": content}
            for document_id, content in documents.items()
        ]
        connection.execute(insert_document, rows)


def load_document(engine, filing_id, document_id):
    query = text("""
        SELECT html FROM reader_documents
        WHERE filing_id = :filing_id AND document_id = :document_id
    """)
    with engine.connect() as connection:
        return connection.execute(query, {
            "filing_id": filing_id,
            "document_id": document_id,
        }).scalar_one_or_none()


def load_filings(engine):
    query = text("""
        SELECT filing.payload
        FROM reader_filings AS filing
        JOIN reader_documents AS document
          ON document.filing_id = filing.id AND document.document_id = 'main'
        ORDER BY filing.id
    """)
    with engine.connect() as connection:
        rows = connection.execute(query)
        return rows.scalars().all()


def cached_explanation(connection, filing_id, fact_id, version):
    query = text("""
        SELECT payload FROM reader_explanations
        WHERE filing_id=:filing AND fact_id=:fact AND version=:version
    """)
    parameters = {"filing": filing_id, "fact": fact_id, "version": version}
    result = connection.execute(query, parameters)
    return result.scalar_one_or_none()


def save_explanation(connection, filing_id, fact_id, version, payload):
    query = text("""
        INSERT INTO reader_explanations(filing_id, fact_id, version, payload)
        VALUES (:filing, :fact, :version, CAST(:payload AS JSONB))
        ON CONFLICT(filing_id, fact_id, version) DO NOTHING
    """)
    parameters = {
        "filing": filing_id,
        "fact": fact_id,
        "version": version,
        "payload": json.dumps(payload),
    }
    connection.execute(query, parameters)
