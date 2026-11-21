
import argparse
import hashlib
import json
import re
from datetime import date, timedelta
from urllib.parse import quote, urljoin, urlparse

from edgar import Company
from lxml import etree, html

from setup_sec_identity import setup_sec_identity
from filing_reader.storage import VERSION, database, initialize, load_filings, save_filing

COMPANIES = {
    "AAPL": {"name": "Apple", "cik": 320193},
    "MSFT": {"name": "Microsoft", "cik": 789019},
}
YEARS = [2024, 2025]
PREPARED_YEARS = YEARS.copy()
PREPARED_YEARS.append(min(YEARS) - 1)


def prepare_html(document, source_url):
    """Annotate the parsed filing document and collect the data behind its clicks."""
    contexts, units = read_contexts_and_units(document)
    facts = mark_facts(document, source_url, contexts, units)
    passages = mark_passages(document, source_url)
    footnotes = {}
    for note in document.iter("ix:footnote"):
        footnotes[note.get("id")] = note

    facts_by_anchor = {}
    for fact in facts.values():
        if "#" in fact["source"]:
            anchor = fact["source"].split("#", 1)[1]
            facts_by_anchor[anchor] = fact
    note_passages = {}
    for relation in document.iter("ix:relationship"):
        if relation.get("arcrole") != "http://www.xbrl.org/2003/arcrole/fact-footnote":
            continue
        fact_refs = relation.get("fromrefs", "").split()
        note_refs = relation.get("torefs", "").split()
        for fact_ref in fact_refs:
            if fact_ref not in facts_by_anchor:
                continue
            for note_ref in note_refs:
                if note_ref not in note_passages:
                    note = footnotes[note_ref]
                    passage_id = "reader-passage-" + str(len(passages))
                    visible = not any(parent.tag in ("ix:hidden", "ix:header") for parent in note.iterancestors())
                    if visible:
                        note.addprevious(html.Element("span", id=passage_id))
                    passages.append({"id": passage_id, "heading": "Linked footnote",
                                     "text": note.text_content(), "source": source_url + "#" + note_ref,
                                     "local": visible})
                    note_passages[note_ref] = passage_id
                fact = facts_by_anchor[fact_ref]
                if "footnotes" not in fact:
                    fact["footnotes"] = []
                refs = fact["footnotes"]
                if note_passages[note_ref] not in refs:
                    refs.append(note_passages[note_ref])
    rendered = render_reader_html(document, source_url)
    return rendered, facts, passages


def read_contexts_and_units(document):
    contexts = {}
    units = {}
    for node in document.iter():
        name = str(node.tag).split(":")[-1].lower()
        if name == "context" and node.get("id"):
            context = {"dimensions": []}
            for child in node.iter():
                child_name = str(child.tag).split(":")[-1].lower()
                if child_name in ("instant", "startdate", "enddate", "identifier"):
                    context[child_name] = child.text_content().strip()
                if child_name in ("explicitmember", "typedmember"):
                    member = child.text_content().strip()
                    if child_name == "typedmember":
                        member = etree.tostring(child[0], encoding="unicode", with_tail=False)
                    context["dimensions"].append({"axis": child.get("dimension"), "member": member})
            contexts[node.get("id")] = context
        if name == "unit" and node.get("id"):
            units[node.get("id")] = node.text_content().strip()
    return contexts, units


def mark_facts(document, source_url, contexts, units):
    facts = {}
    for node in list(document.iter("ix:nonfraction")):
        if any(str(parent.tag).lower() in ("ix:hidden", "ix:header") for parent in node.iterancestors()):
            continue
        fact_id = "reader-fact-" + str(len(facts))
        tag = node.get("name", "")
        original_id = node.get("id")
        # A separate span preserves original anchors and makes the selected fact addressable.
        marker = html.Element("span", id=fact_id)
        node.addprevious(marker)
        marker.append(node)
        node.set("data-reader-fact", fact_id)
        node.set("tabindex", "0")
        node.set("role", "button")
        node.set("title", "Explore comparison and related disclosures")
        title = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", tag.split(":")[-1])
        row = node.getparent()
        while row is not None and row.tag != "tr":
            row = row.getparent()
        row_label = ""
        if row is not None:
            for cell in row.xpath("./td | ./th"):
                if any(child.tag == "ix:nonfraction" for child in cell.iterdescendants()):
                    break
                row_label = row_label or " ".join(cell.text_content().split())
        fact_source = source_url
        if original_id:
            fact_source += "#" + original_id

        facts[fact_id] = {
            "id": fact_id, "tag": tag, "title": title, "display_value": node.text_content(),
            "scale": node.get("scale", "0"), "sign": node.get("sign", ""),
            "format": node.get("format"), "unit": units.get(node.get("unitref"), node.get("unitref", "")),
            "context": contexts.get(node.get("contextref"), {}),
            "row_label": row_label,
            "source": fact_source,
        }
    return facts


def mark_passages(document, source_url, *, local=True):
    # Passages retain source anchors, including a local anchor when the filing has none.
    passages = []
    heading = "Filing disclosure"
    in_mdna = False
    for node in document.xpath("//h1 | //h2 | //h3 | //h4 | //p | //li | //div[not(.//div or .//p or .//table or .//li)]"):
        if any(parent.tag in ("table", "ix:hidden", "ix:header") for parent in node.iterancestors()):
            continue
        passage_text = " ".join(node.text_content().split())
        if len(passage_text) < 170 and not node.xpath(".//a"):
            if re.match(r"Item (?:2|7)\.\s+Management", passage_text, re.I):
                in_mdna = True
            elif re.match(r"Item \d+[A-Z]?\.", passage_text, re.I):
                in_mdna = False
            if node.tag.startswith("h") or node.find("b") is not None or node.find("strong") is not None:
                heading = passage_text
        if len(passage_text) < 30:
            continue
        passage_id = "reader-passage-" + str(len(passages))
        old_id = node.get("id")
        if local:
            node.addprevious(html.Element("span", id=passage_id))
        passage_source = source_url
        if old_id:
            passage_source += "#" + old_id
        passages.append({
            "id": passage_id,
            "heading": heading,
            "text": passage_text,
            "source": passage_source,
            "local": local,
            "mdna": in_mdna,
        })
    return passages


def render_reader_html(document, source_url, *, interactive=True):
    for node in list(document.xpath("//script | //iframe | //object | //embed | //form | //base | //meta | //link")):
        node.drop_tree()
    for node in document.iter():
        for attribute in list(node.attrib):
            if attribute.lower().startswith("on") or attribute.lower() in ("srcdoc", "formaction"):
                del node.attrib[attribute]
        for attribute in ("href", "src"):
            value = node.get(attribute)
            if value and not value.startswith("#"):
                absolute = urljoin(source_url, value)
                if urlparse(absolute).scheme not in ("http", "https"):
                    del node.attrib[attribute]
                else:
                    node.set(attribute, absolute)
        if node.tag == "a" and node.get("href", "").startswith("http"):
            node.set("target", "_blank")
            node.set("rel", "noopener noreferrer")
    head = document.find("head")
    if head is None:
        head = html.Element("head")
        document.insert(0, head)
    style = html.Element("style")
    style.text = '''[data-reader-fact]{cursor:pointer;border-bottom:1px dotted #287a74}
    [data-reader-fact]:hover,[data-reader-fact]:focus{background:#d9f2ed;outline:2px solid #287a74}
    .reader-selected{background:#d9f2ed;outline:2px solid #287a74}
    :target{scroll-margin-top:36px}'''
    head.append(style)
    if interactive:
        script = html.Element("script", src="/static/filing.js")
        document.append(script)
    return html.tostring(document, encoding="unicode", method="html")


def current_report_passages(reports, period_end, filed, documents):
    """Collect nearby 8-K disclosures, including earnings exhibits."""
    latest = filed + timedelta(days=30)
    passages = []
    for report in reports:
        if not period_end < report.filing_date <= latest:
            continue
        for attachment in report.attachments:
            if attachment.document_type != "8-K" and not attachment.document_type.startswith("EX-99"):
                continue
            if not attachment.is_html():
                continue
            parser = html.HTMLParser(encoding="utf-8")
            document = html.fromstring(attachment.content.encode(), parser=parser)
            document_id = str(len(documents))
            for passage in mark_passages(document, attachment.url):
                passage["anchor"] = passage["id"]
                passage["local"] = False
                passage["document_id"] = document_id
                passage["form"] = "8-K"
                passage["exhibit"] = attachment.document_type
                passage["id"] = "current-report-" + str(len(passages))
                passage["filed"] = report.filing_date.isoformat()
                if "#" not in passage["source"]:
                    excerpt = " ".join(passage["text"].split()[:8])
                    passage["source"] += "#:~:text=" + quote(excerpt, safe="").replace("-", "%2D")
                passages.append(passage)
            rendered = render_reader_html(document, attachment.url, interactive=False)
            documents[document_id] = rendered
    return passages


def prepare_amendments(filing, amendments, documents):
    result = []
    for amendment in sorted(amendments, key=lambda item: (item.filing_date, item.accession_no)):
        if amendment.form != filing.form + "/A" or amendment.report_date != filing.report_date:
            continue
        attachment = amendment.attachments.primary_html_document
        parser = html.HTMLParser(encoding="utf-8")
        document = html.fromstring(attachment.content.encode(), parser=parser)
        contexts, units = read_contexts_and_units(document)
        facts = mark_facts(document, attachment.url, contexts, units)
        for element in document.xpath("//*[@data-reader-fact]"):
            for attribute in ("data-reader-fact", "tabindex", "role", "title"):
                del element.attrib[attribute]
        document_id = str(len(documents))
        rendered = render_reader_html(document, attachment.url, interactive=False)
        documents[document_id] = rendered
        result.append({
            "form": amendment.form,
            "filed": amendment.filing_date.isoformat(),
            "source": attachment.url,
            "document_id": document_id,
            "facts": facts,
        })
    return result


def prepare_filing(ticker, filing, current_reports, amendments, saved):
    matching_accessions = [
        amendment.accession_no for amendment in amendments
        if amendment.form == filing.form + "/A" and amendment.report_date == filing.report_date
    ]
    if (saved and saved.get("version") == VERSION
            and saved.get("amendment_accessions") == matching_accessions):
        return None

    attachment = filing.attachments.primary_html_document
    content = attachment.content
    source_url = attachment.url
    document = html.fromstring(content.encode(), parser=html.HTMLParser(encoding="utf-8"))
    focus = {}
    for node in document.iter("ix:nonnumeric"):
        name = node.get("name")
        if name in ("dei:DocumentFiscalYearFocus", "dei:DocumentFiscalPeriodFocus"):
            focus[name] = node.text_content().strip()
    year = int(focus["dei:DocumentFiscalYearFocus"])
    period = focus["dei:DocumentFiscalPeriodFocus"]
    if year not in PREPARED_YEARS:
        return None
    filing_id = f"{ticker}-{year}"
    if period != "FY":
        filing_id += f"-{period}"
    rendered, facts, passages = prepare_html(document, source_url)
    documents = {}
    related = []
    if year in YEARS:
        period_end = date.fromisoformat(str(filing.report_date))
        related = current_report_passages(current_reports, period_end, filing.filing_date, documents)
    revisions = prepare_amendments(filing, amendments, documents)
    payload = {"id": filing_id, "ticker": ticker, "company": COMPANIES[ticker]["name"], "year": year,
        "form": filing.form, "fiscal_period": period, "version": VERSION,
        "period_end": str(filing.report_date), "filed": str(filing.filing_date),
        "accession": filing.accession_no, "source": source_url, "facts": facts,
        "passages": passages, "current_reports": related,
        "amendments": revisions, "amendment_accessions": matching_accessions,
        "source_documents": list(documents),
        "revision": hashlib.sha256((VERSION + content + json.dumps(related, sort_keys=True)).encode()).hexdigest()}
    documents["main"] = rendered
    return payload, documents


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    setup_sec_identity()
    engine = database()
    initialize(engine)
    saved_filings = {item["accession"]: item for item in load_filings(engine)}
    for ticker, company in COMPANIES.items():
        entity = Company(company["cik"])
        amendments = list(entity.get_filings(form=["10-K/A", "10-Q/A"], amendments=True))
        filings = list(entity.get_filings(
            form=["10-K", "10-Q", "8-K"], amendments=False,
            date=f"{min(PREPARED_YEARS) - 1}-01-01:{max(YEARS) + 1}-04-30",
        ))
        current_reports = []
        for filing in filings:
            if filing.form == "8-K":
                current_reports.append(filing)
        for filing in filings:
            if filing.form not in ("10-K", "10-Q"):
                continue
            saved = saved_filings.get(filing.accession_no)
            prepared = prepare_filing(ticker, filing, current_reports, amendments, saved)
            if prepared is None:
                continue
            payload, documents = prepared
            save_filing(engine, payload, documents)
            print(f"Prepared {payload['id']}: {len(payload['facts'])} facts, {len(payload['passages'])} passages", flush=True)


if __name__ == "__main__":
    main()
