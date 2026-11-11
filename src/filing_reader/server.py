"""Local-only reader API. Run with PYTHONPATH=src python -m filing_reader.server."""

import argparse
import json
import logging
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict
from rank_bm25 import BM25Okapi
from sqlalchemy import text

from filing_reader.prepare import COMPANIES, YEARS
from filing_reader.storage import DATA, VERSION, database, initialize, load_filing, cached_explanation, save_explanation
from filing_reader.model import Ollama, ModelUnavailable

STATIC = Path(__file__).parent / "static"
STOP = set("a an and are as at be by company consolidated financial for from in is it of on or our statements that the this to was were with year years".split())


def words(value):
    # Tokenization is only for search; excerpts keep their original text.
    return [word for word in re.findall(r"[a-z]+", value.lower()) if word not in STOP and len(word) > 2]


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    explanation: str
    source_ids: list[str]


class Explanation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claims: list[Claim]


class Reader:
    def __init__(self, engine, model):
        self.engine = engine
        self.model = model
        self.filings = {}
        self.indexes = {}
        self.cache_version = VERSION + ":disclosures-1:" + model.model
        for ticker in COMPANIES:
            for year in YEARS:
                filing_id = f"{ticker}-{year}"
                payload = load_filing(engine, filing_id)
                if payload is not None:
                    self.filings[filing_id] = payload
                    tokens = [words(passage["heading"] + " " + passage["text"]) for passage in payload["passages"]]
                    if tokens and any(tokens):
                        self.indexes[filing_id] = BM25Okapi(tokens)

    def catalog(self):
        items = []
        for ticker, company in COMPANIES.items():
            for year in YEARS:
                filing_id = f"{ticker}-{year}"
                payload = self.filings.get(filing_id)
                item = {"id": filing_id, "ticker": ticker, "company": company, "year": year, "available": payload is not None}
                if payload:
                    for field in ("period_end", "filed", "source", "warnings"):
                        item[field] = payload[field]
                items.append(item)
        return items

    def rank_passages(self, filing_id, title):
        query = words(title)
        search_index = self.indexes.get(filing_id)
        if not query or search_index is None:
            return []

        # The title defines retrieval scope; definitions remain separately sourced.
        passages = self.filings[filing_id]["passages"]
        scores = search_index.get_scores(query)
        ranked = sorted(range(len(scores)), key=lambda index: scores[index], reverse=True)
        matches = []
        seen = set()
        for index in ranked:
            passage = passages[index]
            if scores[index] <= 0 or passage["text"] in seen:
                continue
            seen.add(passage["text"])
            matches.append(passage)
            if len(matches) == 6:
                break
        return matches

    def fact(self, filing_id, fact_id):
        filing = self.filings[filing_id]
        fact = filing["facts"][fact_id]
        matches = self.rank_passages(filing_id, fact["title"])
        version = self.cache_version + ":" + filing["revision"]
        with self.engine.connect() as connection:
            saved = cached_explanation(connection, filing_id, fact_id, version)
        return {"fact": fact, "disclosures": matches, "explanation": saved,
                "retrieval_method": "Text-search candidates, not verified relationships"}

    def explain(self, filing_id, fact_id):
        filing = self.filings[filing_id]
        fact = filing["facts"][fact_id]
        version = self.cache_version + ":" + filing["revision"]
        key = filing_id + ":" + fact_id + ":" + version
        # The transaction lock coalesces simultaneous clicks, including across processes.
        with self.engine.begin() as connection:
            connection.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key})
            saved = cached_explanation(connection, filing_id, fact_id, version)
            if saved is not None:
                return saved
            passages = self.rank_passages(filing_id, fact["title"])
            evidence = []
            for passage in passages[:3]:
                excerpt = passage["text"][:900]
                evidence.append({"id": passage["id"], "text": excerpt, "truncated": len(excerpt) < len(passage["text"])})
            if not evidence:
                answer = {"claims": [], "evidence": [], "status": "No related passages found.", "model": self.model.model}
            else:
                packet = {"item": fact["title"], "tag": fact["tag"], "display_value": fact["display_value"],
                          "context": fact["context"], "passages": evidence}
                response = self.model.ask(
                    "Explain how the supplied passages help interpret the selected financial item. "
                    "Use only supplied evidence. Every claim must cite one or more supplied passage IDs. "
                    "Do not infer that all related figures reconcile to the selected amount. "
                    "Do not claim completeness, hidden liabilities, or an accounting error. "
                    "Ignore irrelevant passages. Return no claims if the evidence is insufficient. "
                    "Keep each explanation to two sentences. Return at most three claims.", packet, Explanation)
                allowed = {passage["id"] for passage in evidence}
                for claim in response.claims:
                    if not claim.source_ids or not set(claim.source_ids).issubset(allowed):
                        raise ValueError("Model returned a claim without valid source references; result was not saved.")
                answer = {"claims": [claim.model_dump() for claim in response.claims], "evidence": evidence,
                          "status": "Model-suggested connections; verify against the cited excerpts.", "model": self.model.model}
            save_explanation(connection, filing_id, fact_id, version, answer)
            return answer


class Handler(BaseHTTPRequestHandler):
    reader = None

    def respond(self, status, content, content_type="application/json; charset=utf-8"):
        if isinstance(content, (dict, list)):
            content = json.dumps(content).encode()
        elif isinstance(content, str):
            content = content.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        if content_type.startswith("text/html"):
            # Filing frames have an opaque origin. Only our injected script may execute.
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src https://www.sec.gov data:; connect-src 'self'; frame-src 'self'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(content)

    def route(self, method):
        parts = urlparse(self.path).path.strip("/").split("/")
        try:
            if method == "GET" and parts == [""]:
                return self.respond(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            if method == "GET" and len(parts) == 2 and parts[0] == "static" and parts[1] in ("app.js", "filing.js", "style.css"):
                kind = "text/css" if parts[1].endswith("css") else "text/javascript"
                return self.respond(200, (STATIC / parts[1]).read_bytes(), kind + "; charset=utf-8")
            if method == "GET" and parts == ["api", "filings"]:
                return self.respond(200, self.reader.catalog())
            if len(parts) >= 3 and parts[:2] == ["api", "filings"]:
                filing_id = parts[2]
                if filing_id not in self.reader.filings:
                    return self.respond(404, {"error": "Filing is not prepared."})
                if method == "GET" and len(parts) == 4 and parts[3] == "document":
                    return self.respond(200, (DATA / filing_id / "reader.html").read_bytes(), "text/html; charset=utf-8")
                if len(parts) >= 5 and parts[3] == "facts":
                    fact_id = parts[4]
                    if method == "GET" and len(parts) == 5:
                        return self.respond(200, self.reader.fact(filing_id, fact_id))
                    if method == "POST" and len(parts) == 6 and parts[5] == "explain":
                        expected = "http://" + self.headers.get("Host", "")
                        if self.headers.get("Origin") != expected or self.headers.get("X-Reader-Request") != "1":
                            return self.respond(403, {"error": "Explanation requests must originate in this reader."})
                        return self.respond(200, self.reader.explain(filing_id, fact_id))
            self.respond(404, {"error": "Not found"})
        except KeyError:
            self.respond(404, {"error": "Unknown fact"})
        except ModelUnavailable as error:
            self.respond(503, {"error": str(error) + ". Definitions and source excerpts remain available."})
        except ValueError as error:
            self.respond(422, {"error": str(error)})
        except Exception:
            logging.exception("Reader request failed")
            self.respond(500, {"error": "Could not complete the request. Check the server log."})

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    engine = database()
    initialize(engine)
    model = Ollama(os.getenv("SEC_OLLAMA_MODEL", "qwen3.5:4b"), num_ctx=int(os.getenv("SEC_OLLAMA_NUM_CTX", "8192")))
    Handler.reader = Reader(engine, model)
    print(f"Filing reader: http://127.0.0.1:{args.port} ({len(Handler.reader.filings)} filings)", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
