"""Local-only reader API. Run with PYTHONPATH=src python -m filing_reader.server."""

import argparse
import json
import logging
import os
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from edgar.documents.strategies.ixbrl_transforms import apply_transform
from pydantic import BaseModel, ConfigDict, Field, create_model
from rank_bm25 import BM25Okapi
from sqlalchemy import text

from filing_reader.storage import VERSION, database, initialize, load_document, load_filings, cached_explanation, save_explanation
from filing_reader.model import Ollama, ModelUnavailable

STATIC = Path(__file__).parent / "static"
WORKSPACE_FILINGS = {"AAPL-2025", "AAPL-2025-Q3", "MSFT-2025", "MSFT-2025-Q3"}
STOP = set("a an and are as at be by company consolidated financial for from in is it of on or our statements that the this to was were with year years".split())


def words(value):
    # Tokenization is only for search; excerpts keep their original text.
    tokens = []
    for word in re.findall(r"[a-z]+", value.lower()):
        if word not in STOP and len(word) > 2:
            tokens.append(word)
    return tokens


def numeric_amount(fact):
    value = fact["display_value"].strip()
    try:
        if fact["format"]:
            value = apply_transform(fact["format"], value)
        amount = Decimal(value)
        if not amount.is_finite():
            return None
        with localcontext() as context:
            context.prec = max(context.prec, len(amount.as_tuple().digits))
            amount = amount.scaleb(int(fact["scale"]))
    except (InvalidOperation, ValueError):
        return None
    if fact["sign"] == "-":
        amount = amount.copy_negate()

    return amount


def format_amount(fact, amount):
    if amount is None:
        return None
    unit = fact["unit"]
    unit_name = unit.rsplit(":", 1)[-1]
    unit_formats = {
        "iso4217:USD": ("$", ""),
        "iso4217:EUR": ("€", ""),
        "iso4217:USDxbrli:shares": ("$", " per share"),
        "xbrli:shares": ("", " shares"),
        "xbrli:pure": ("", ""),
    }
    if unit in unit_formats:
        prefix, suffix = unit_formats[unit]
    else:
        prefix = ""
        suffix = ""
        if unit_name:
            suffix = f" {unit_name}"
    sign = ""
    if amount.is_signed() and amount:
        sign = "-"
    return f"{sign}{prefix}{amount.copy_abs():,f}{suffix}"


def concept_key(fact):
    context = fact["context"]
    dimensions = []
    for item in context.get("dimensions", []):
        dimensions.append((item["axis"], item["member"]))
    dimensions.sort()
    return fact["tag"], fact["unit"], context.get("identifier"), tuple(dimensions)


def fact_period(fact):
    context = fact["context"]
    return context.get("startdate"), context.get("instant") or context.get("enddate")


def fact_evidence(fact, amount):
    label = fact["row_label"] or fact["title"]
    period = " to ".join(value for value in fact_period(fact) if value)
    dimensions = ", ".join(member["member"] for member in fact["context"].get("dimensions", []))
    return {
        "id": fact["id"], "heading": label,
        "text": f'Filing label: {label}. Amount: {amount}. Period: {period}. '
                f'Scope: {dimensions or "no tagged dimensions"}. XBRL concept: {fact["tag"]}.',
        "source": fact["source"], "local": True,
    }


def reported_amount(filing, question):
    """Only answer simple requests for a single consolidated amount in this report."""
    request = re.fullmatch(
        r"what (?:was|were) (?:the )?(total net sales|total revenue|net income|revenue)"
        r"(?: (this year|this quarter|for the period covered by this filing|(?:for |in )?fiscal \d{4}))?[?.]?",
        question, re.IGNORECASE,
    )
    if not request:
        return None
    metric, period = request.groups()
    period = (period or "").lower()
    annual = filing.get("fiscal_period", "FY") == "FY"
    if period == "this quarter" and annual:
        return None
    if period == "this year" and not annual:
        return None
    if "fiscal" in period and (not annual or not period.endswith(f' {filing.get("year")}')):
        return None
    if metric.lower() == "net income":
        labels = {"net income", "net income (loss)", "net income loss"}
        tags = {"us-gaap:NetIncomeLoss", "us-gaap:ProfitLoss"}
    else:
        labels = {"total net sales", "net sales", "total revenue", "revenue"}
        tags = {"us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax", "us-gaap:SalesRevenueNet",
                "us-gaap:Revenues"}
    end = filing["period_end"]
    end_date = date.fromisoformat(end)
    min_days, max_days = (350, 378) if annual else (75, 105)
    matches = {}
    for fact in filing["facts"].values():
        if fact["tag"] not in tags or fact["row_label"].strip().lower() not in labels:
            continue
        if fact["context"].get("dimensions") or fact["unit"] not in {"iso4217:USD", "iso4217:EUR"}:
            continue
        start, last = fact_period(fact)
        if not start or last != end or not min_days <= (end_date - date.fromisoformat(start)).days <= max_days:
            continue
        amount = numeric_amount(fact)
        if amount is not None:
            matches.setdefault((start, fact["unit"], fact["context"].get("identifier"), amount), fact)
    if len(matches) == 1:
        (_, _, _, amount), fact = next(iter(matches.items()))
        return fact, format_amount(fact, amount)
    return None


def matching_value(facts, end, start=None, *, duration=None):
    matches = {}
    target_end = date.fromisoformat(end)
    for fact in facts:
        first, last = fact_period(fact)
        if not last:
            continue
        if duration is None:
            if (first, last) != (start, end):
                continue
        elif not first or abs((date.fromisoformat(last) - target_end).days) > 7:
            continue
        elif abs((date.fromisoformat(last) - date.fromisoformat(first)).days - duration) > 7:
            continue
        amount = numeric_amount(fact)
        if amount is not None:
            matches[(first, last, amount)] = fact
    if len(matches) == 1:
        (first, last, amount), fact = matches.popitem()
        return amount, first, last, [fact["source"]]


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
        self.filings = {filing["id"]: filing for filing in load_filings(engine)}
        self.indexes = {}
        self.concepts = {}
        self.periods = {}
        self.cache_version = VERSION + ":" + model.model
        for filing_id, filing in self.filings.items():
            self.periods[(filing["ticker"], filing["year"], filing.get("fiscal_period", "FY"))] = filing_id
            groups = {}
            for fact in filing["facts"].values():
                key = concept_key(fact)
                if key not in groups:
                    groups[key] = []
                groups[key].append(fact)
            self.concepts[filing_id] = groups
            for kind in ("passages", "earnings", "current_reports"):
                tokens = []
                for passage in filing.get(kind, []):
                    search_text = passage["heading"] + " " + passage["text"]
                    tokens.append(words(search_text))
                if any(tokens):
                    self.indexes[filing_id, kind] = BM25Okapi(tokens)
            passages_by_id = {}
            for passage in filing["passages"]:
                passages_by_id[passage["id"]] = passage
            filing["passages_by_id"] = passages_by_id

    def catalog(self):
        fields = ("id", "ticker", "company", "year", "period_end", "filed", "source", "accession")
        catalog = []
        for filing in self.filings.values():
            if filing["id"] not in WORKSPACE_FILINGS:
                continue
            entry = {}
            for field in fields:
                entry[field] = filing[field]
            entry["amendments"] = []
            for amendment in filing.get("amendments", []):
                entry["amendments"].append({
                    field: amendment[field] for field in ("form", "filed", "source", "document_id")
                })
            entry["form"] = filing.get("form", "10-K")
            entry["fiscal_period"] = filing.get("fiscal_period", "FY")
            catalog.append(entry)
        return catalog

    def comparison(self, filing_id, fact):
        filing = self.filings[filing_id]
        start, end = fact_period(fact)
        if end != filing["period_end"]:
            return None
        amount = numeric_amount(fact)
        if amount is None:
            return None
        year = filing["year"]
        period = filing.get("fiscal_period", "FY")
        duration = None
        if start:
            start_date = date.fromisoformat(start)
            end_date = date.fromisoformat(end)
            duration = (end_date - start_date).days
        if period == "FY" and duration is not None and not 350 <= duration <= 378:
            return None
        if period != "FY" and duration is not None and not (
            75 <= duration <= 105
            or period == "Q2" and 165 <= duration <= 195
            or period == "Q3" and 255 <= duration <= 285
        ):
            return None
        sequential = period != "FY" and (duration is None or duration <= 105)
        baseline_year = year - 1
        baseline_period = period
        if sequential:
            previous_period = {"Q1": "Q3", "Q2": "Q1", "Q3": "Q2"}
            baseline_period = previous_period[period]
            if period != "Q1":
                baseline_year = year
        baseline_id = self.periods.get((filing["ticker"], baseline_year, baseline_period))
        key = concept_key(fact)
        current_facts = self.concepts[filing_id][key]
        baseline_facts = self.concepts.get(baseline_id, {}).get(key, [])

        if baseline_id:
            prior_end = self.filings[baseline_id]["period_end"]
        elif sequential and start and period != "Q1":
            prior_end = (date.fromisoformat(start) - timedelta(days=1)).isoformat()
        elif not sequential and start:
            # A comparative column can be present even when the prior report isn't loaded.
            prior_end = (date.fromisoformat(end) - timedelta(days=365)).isoformat()
        else:
            return None

        if sequential and period != "Q1" and start and date.fromisoformat(prior_end) + timedelta(days=1) != date.fromisoformat(start):
            return None
        baseline = matching_value(current_facts, prior_end, duration=duration)
        if baseline is None:
            baseline = matching_value(baseline_facts, prior_end, duration=duration)
        if baseline is None:
            return None
        prior_amount, prior_start, prior_end, sources = baseline
        change = amount - prior_amount
        label = prior_end
        if prior_start:
            label = f"{prior_start} to {prior_end}"
        basis = "prior year"
        if sequential:
            basis = f"FY{baseline_year} {baseline_period}"
        percentage = None
        if prior_amount > 0:
            percentage = 100 * change / prior_amount
            percentage = f"{percentage:+,.1f}%"

        amount_change = format_amount(fact, change)
        if change > 0:
            amount_change = "+" + amount_change
        return {"baseline": label, "baseline_amount": format_amount(fact, prior_amount),
                "sources": sources,
                "basis": basis,
                "amount_change": amount_change,
                "percent": percentage,
                "period_end": prior_end}

    def rank_passages(self, filing_id, title, kind="passages", *, mdna_only=False):
        query = words(title)
        search_index = self.indexes.get((filing_id, kind))
        if not query or search_index is None:
            return []

        passages = self.filings[filing_id][kind]
        scores = search_index.get_scores(query)
        best_score = 0
        best_passage = None
        for passage, score in zip(passages, scores):
            if score <= best_score:
                continue
            if passage["heading"] == "Linked footnote" or passage.get("table"):
                continue
            if mdna_only and not passage.get("mdna"):
                continue
            best_score = score
            best_passage = passage
        if best_passage is None:
            return []
        return [best_passage]

    def ask(self, filing_id, question, selection="", fact_id=None):
        filing = self.filings[filing_id]
        if not isinstance(question, str) or not question.strip() or len(question) > 600:
            raise ValueError("Enter a question of up to 600 characters.")
        if not isinstance(selection, str) or len(selection) > 600:
            raise ValueError("Select up to 600 characters.")
        answer = {"claims": [], "evidence": [], "filing_id": filing_id,
                  "accession": filing["accession"], "question": question,
                  "selection": selection or None, "fact_id": fact_id}
        normalized_question = " ".join(question.strip().split())
        # A generic earnings question can refer to either the top or bottom line.
        if fact_id is None and not selection.strip():
            ambiguous = re.fullmatch(
                r"how much did [\w .&'’-]+? (?:make|earn)"
                r"(?: (this year|last year|this quarter|last quarter|in (?:fiscal )?\d{4}))?[?.]?",
                normalized_question, re.IGNORECASE,
            )
            if ambiguous:
                period = ambiguous.group(1) or "for the period covered by this filing"
                answer["clarification"] = "Do you mean revenue (total sales) or profit (net income)?"
                answer["choices"] = [
                    {"label": "Revenue", "question": f"What were total net sales {period}?"},
                    {"label": "Net income", "question": f"What was net income {period}?"},
                ]
                return answer
            direct = reported_amount(filing, normalized_question)
            if direct:
                fact, amount = direct
                evidence = fact_evidence(fact, amount)
                start, end = fact_period(fact)
                answer["claims"] = [{
                    "explanation": f'{evidence["heading"]}: {amount} for {start} to {end}.',
                    "source_ids": [fact["id"]],
                }]
                answer["evidence"] = [evidence]
                return answer
        fact = filing["facts"][fact_id] if fact_id is not None else None
        query = question + " " + selection
        if fact:
            query += " " + fact["title"] + " " + fact["row_label"]
        evidence = []
        if fact:
            for ref in fact.get("footnotes", [])[:2]:
                evidence.append(filing["passages_by_id"][ref])
        if selection.strip():
            selected = " ".join(selection.lower().split())
            matching = [
                passage for passage in filing["passages"]
                if selected in " ".join(passage["text"].lower().split())
            ]
            if matching:
                passage = min(matching, key=lambda item: len(item["text"]))
                # Keep the selected section in the model excerpt even for long passages.
                offset = passage["text"].lower().find(selection.lower())
                evidence.append({**passage, "excerpt_start": max(0, offset - 48)})
        index = self.indexes.get((filing_id, "passages"))
        if index is not None:
            ranked = sorted(zip(index.get_scores(words(query)), filing["passages"]),
                            key=lambda pair: pair[0], reverse=True)
            for score, passage in ranked:
                if score <= 0 or len(evidence) >= 4:
                    break
                if all(item["id"] != passage["id"] for item in evidence):
                    evidence.append(passage)
        # Include exact tagged facts as evidence for questions about financial tables.
        query_words = set(words(query))
        candidates = [fact] if fact else []
        if not fact and not selection:
            def fact_score(item):
                return len(query_words.intersection(words(item["title"] + " " + item["row_label"])))
            candidates = sorted(filing["facts"].values(),
                                key=lambda item: (fact_score(item), fact_period(item)[1] == filing["period_end"]),
                                reverse=True)[:3]
            candidates = [item for item in candidates if fact_score(item)]
        for item in candidates:
            amount = format_amount(item, numeric_amount(item))
            if amount is None:
                continue
            evidence.append(fact_evidence(item, amount))
        evidence_by_id = {item["id"]: item for item in evidence}
        claims = []
        if evidence:
            packet = {
                "question": question,
                "filing_period": filing.get("fiscal_period", "FY"),
                "filing_period_end": filing["period_end"],
                "selected_text_for_search": selection,
                "passages": [
                    {"id": item["id"], "text": item["text"][item.get("excerpt_start", 0):][:280]}
                    for item in evidence_by_id.values()
                ],
            }
            source_id = Literal[tuple(evidence_by_id)]
            cited_claim = create_model(
                "CitedClaim", __base__=Claim,
                source_ids=(list[source_id], Field(min_length=1)),
            )
            cited_answer = create_model(
                "CitedAnswer", __base__=Explanation,
                claims=(list[cited_claim], Field(max_length=3)),
                clarification=(str | None, Field(default=None, max_length=220)),
            )
            response = self.model.ask(
                "Answer the question in at most three short claims, using only supplied passages. "
                "Cite source IDs for each claim. Selected text is a search hint, not evidence. "
                "Use the filing label to identify a tagged figure and respect its period and scope. "
                "If the question has multiple plausible meanings that would change the answer, "
                "return no claims and ask one focused clarification question. "
                "If the question is clear but the evidence does not establish an answer, return no "
                "claims and no clarification. Never cite unrelated evidence or a claim that says "
                "the supplied information is missing. Answer only what was asked.",
                packet, cited_answer,
            )
            if response.clarification and response.clarification.strip():
                answer["clarification"] = response.clarification.strip()
                return answer
            for claim in response.claims[:3]:
                if not claim.source_ids or not set(claim.source_ids).issubset(evidence_by_id):
                    raise ValueError("The answer contained an invalid citation. Try rephrasing the question.")
                claims.append(claim.model_dump())
        cited_ids = {ref for claim in claims for ref in claim["source_ids"]}
        answer["claims"] = claims
        answer["evidence"] = [item for item in evidence if item["id"] in cited_ids]
        return answer

    def explanation_version(self, filing, comparison):
        return self.cache_version + ":" + filing["revision"] + ":" + json.dumps(comparison, sort_keys=True)

    def corrected_values(self, filing, fact, original_amount):
        corrections = []
        if original_amount is None:
            return corrections
        start, end = fact_period(fact)
        if not end:
            return corrections
        key = concept_key(fact)
        previous_amount = original_amount
        for amendment in filing.get("amendments", []):
            candidates = []
            for candidate in amendment["facts"].values():
                if concept_key(candidate) == key:
                    candidates.append(candidate)
            match = matching_value(candidates, end, start)
            if match is None:
                continue
            amount, _, _, sources = match
            if amount == previous_amount:
                continue
            corrections.append({
                "amount": format_amount(fact, amount),
                "filed": amendment["filed"],
                "form": amendment["form"],
                "document_id": amendment["document_id"],
                "source": sources[0],
            })
            previous_amount = amount
        return corrections

    def fact(self, filing_id, fact_id):
        filing = self.filings[filing_id]
        fact = filing["facts"][fact_id]
        comparison = self.comparison(filing_id, fact)
        version = self.explanation_version(filing, comparison)
        with self.engine.connect() as connection:
            saved = cached_explanation(connection, filing_id, fact_id, version)
        amount = numeric_amount(fact)
        footnotes = []
        for ref in fact.get("footnotes", []):
            footnotes.append(filing["passages_by_id"][ref])
        return {
            "fact": fact,
            "formatted_amount": format_amount(fact, amount),
            "comparison": comparison,
            "explanation": saved,
            "footnotes": footnotes,
            "corrections": self.corrected_values(filing, fact, amount),
        }

    def explain(self, filing_id, fact_id):
        filing = self.filings[filing_id]
        fact = filing["facts"][fact_id]
        comparison = self.comparison(filing_id, fact)
        version = self.explanation_version(filing, comparison)
        key = filing_id + ":" + fact_id + ":" + version
        # The transaction lock coalesces simultaneous clicks, including across processes.
        with self.engine.begin() as connection:
            connection.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key})
            saved = cached_explanation(connection, filing_id, fact_id, version)
            if saved is not None:
                return saved
            dimension_names = []
            for member in fact["context"].get("dimensions", []):
                dimension_names.append(member["member"].split(":")[-1])
            dimensions = " ".join(dimension_names)
            label = fact.get("row_label") or fact["title"]
            query = label
            if dimensions:
                query += " " + re.sub(r"(?<=[a-z])(?=[A-Z])", " ", dimensions)
            linked = [filing["passages_by_id"][ref] for ref in fact.get("footnotes", [])]
            passages = linked[:1] + self.rank_passages(filing_id, query)
            passages += self.rank_passages(filing_id, query, mdna_only=True)
            report_passages = self.rank_passages(filing_id, query, "current_reports")
            if not report_passages:
                report_passages = self.rank_passages(filing_id, query, "earnings")
            passages += report_passages
            evidence_by_id = {}
            for passage in passages:
                excerpt = passage.copy()
                excerpt.setdefault("form", filing.get("form"))
                excerpt.setdefault("filed", filing.get("filed"))
                evidence_by_id[passage["id"]] = excerpt
            evidence = list(evidence_by_id.values())

            claims = []
            if evidence:
                comparison_context = None
                if comparison:
                    comparison_context = {}
                    for field in ("baseline", "basis", "baseline_amount", "amount_change", "percent"):
                        comparison_context[field] = comparison[field]

                model_passages = []
                for item in evidence:
                    model_passages.append({
                        "id": item["id"],
                        "text": item["text"][:475],
                        "filed": item.get("filed"),
                    })
                packet = {
                    "item": label,
                    "tag": fact["tag"],
                    "display_value": fact["display_value"],
                    "context": fact["context"],
                    "filing_date": filing.get("filed"),
                    "comparison": comparison_context,
                    "passages": model_passages,
                }
                response = self.model.ask(
                    "Write at most two short, distinct details that change how a reader understands "
                    "the selected figure. Use only supplied passages and cite their IDs. Match the metric "
                    "and reporting scope. Relevant details include what the amount includes, commitments, "
                    "and developments that affect its interpretation. Distinguish later events and plans "
                    "from reported results, and include the date of a later development when stated. Attribute a change to a cause only if the passage explains "
                    "the exact periods being compared. Skip generic definitions and unrelated details. "
                    "Return no claims when the evidence is insufficient.",
                    packet, Explanation)
                for claim in response.claims[:2]:
                    if not claim.source_ids or not set(claim.source_ids).issubset(evidence_by_id):
                        raise ValueError("Model returned a claim without valid source references; result was not saved.")
                    claims.append(claim.model_dump())
            answer = {"claims": claims, "evidence": evidence, "model": self.model.model}
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
                kind = "text/javascript"
                if parts[1].endswith("css"):
                    kind = "text/css"
                return self.respond(200, (STATIC / parts[1]).read_bytes(), kind + "; charset=utf-8")
            if method == "GET" and parts == ["api", "filings"]:
                return self.respond(200, self.reader.catalog())
            if len(parts) >= 3 and parts[:2] == ["api", "filings"]:
                filing_id = parts[2]
                if filing_id not in self.reader.filings:
                    return self.respond(404, {"error": "Filing is not prepared."})
                document_id = None
                if parts[3:] == ["document"]:
                    document_id = "main"
                elif len(parts) == 5 and parts[3] == "sources":
                    filing = self.reader.filings[filing_id]
                    if parts[4] in filing.get("source_documents", []):
                        document_id = parts[4]
                if method == "GET" and document_id is not None:
                    content = load_document(self.reader.engine, filing_id, document_id)
                    if content is None:
                        return self.respond(404, {"error": "Document is not prepared."})
                    return self.respond(200, content, "text/html; charset=utf-8")
                if method == "POST" and parts[3:] == ["ask"]:
                    expected = "http://" + self.headers.get("Host", "")
                    if self.headers.get("Origin") != expected or self.headers.get("X-Reader-Request") != "1":
                        return self.respond(403, {"error": "Question requests must originate in this reader."})
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 8192:
                        raise ValueError("Invalid question length.")
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict):
                        raise ValueError("Invalid question.")
                    fact_id = body.get("fact_id")
                    if fact_id is not None and (not isinstance(fact_id, str) or not re.fullmatch(r"reader-fact-\d+", fact_id)):
                        raise ValueError("Invalid fact ID.")
                    return self.respond(200, self.reader.ask(filing_id, body.get("question"), body.get("selection", ""), fact_id))
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
            self.respond(503, {"error": str(error) + ". The reported value and linked notes are available above."})
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
