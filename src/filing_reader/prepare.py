"""Download and prepare the four allowed 10-Ks. No model calls."""

import argparse
import hashlib
import json
import re
from urllib.parse import urljoin, urlparse

from edgar import Company
from edgar.httprequests import download_file
from lxml import etree, html

from setup_sec_identity import setup_sec_identity
from filing_reader.storage import DATA, VERSION, database, initialize, save_filing

COMPANIES = {"AAPL": "Apple", "MSFT": "Microsoft"}
YEARS = (2024, 2025)
NS = {"link": "http://www.xbrl.org/2003/linkbase", "xs": "http://www.w3.org/2001/XMLSchema"}
XLINK = "{http://www.w3.org/1999/xlink}"


def read_labels(content, source_url):
    """Resolve label arcs to concepts; preserve role and source for each text."""
    root = etree.fromstring(content.encode(), etree.XMLParser(huge_tree=True, encoding="utf-8"))
    result = {}
    for group in root.iter("{" + NS["link"] + "}labelLink"):
        targets = {}
        texts = {}
        for locator in group.findall("link:loc", NS):
            tag_id = locator.get(XLINK + "href").split("#")[-1]
            targets.setdefault(locator.get(XLINK + "label"), []).append(tag_id.replace("_", ":", 1))
        for label in group.findall("link:label", NS):
            texts.setdefault(label.get(XLINK + "label"), []).append({
                "text": "".join(label.itertext()),
                "role": label.get(XLINK + "role", "").rsplit("/", 1)[-1],
                "language": label.get("{http://www.w3.org/XML/1998/namespace}lang", ""),
                "source": source_url + ("#" + label.get("id") if label.get("id") else ""),
            })
        for arc in group.findall("link:labelArc", NS):
            for tag in targets.get(arc.get(XLINK + "from"), []):
                result.setdefault(tag, []).extend(texts.get(arc.get(XLINK + "to"), []))
    # Schema annotations are kept distinct from formal documentation labels.
    for declaration in root.findall("xs:element", NS):
        tag = declaration.attrib["id"].replace("_", ":", 1)
        for note in declaration.findall(".//xs:documentation", NS):
            result.setdefault(tag, []).append({"text": "".join(note.itertext()),
                "role": "schema-documentation", "language": "",
                "source": source_url + "#" + declaration.attrib["id"]})
    return result


def prepare_html(content, source_url, labels):
    """Keep HTML layout, index inline facts and passages, and remove active content."""
    document = html.fromstring(content.encode(), parser=html.HTMLParser(encoding="utf-8"))
    contexts, units = read_contexts_and_units(document)
    facts = mark_facts(document, source_url, labels, contexts, units)
    passages = mark_passages(document, source_url)
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
                    context["dimensions"].append({"axis": child.get("dimension"), "member": child.text_content()})
            contexts[node.get("id")] = context
        if name == "unit" and node.get("id"):
            units[node.get("id")] = node.text_content().strip()
    return contexts, units


def mark_facts(document, source_url, labels, contexts, units):
    facts = {}
    for node in list(document.iter()):
        if str(node.tag).lower() != "ix:nonfraction":
            continue
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
        node.set("title", "Explore definition and related disclosures")
        title = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", tag.split(":")[-1])
        tag_labels = labels.get(tag, [])
        english = [item for item in tag_labels if item["language"].lower().startswith("en") or not item["language"]]
        for item in english:
            if item["role"] == "label":
                title = item["text"]
                break
        definitions = [item for item in english if item["role"] in ("documentation", "schema-documentation")]
        row = next((parent for parent in node.iterancestors() if parent.tag == "tr"), None)
        facts[fact_id] = {
            "id": fact_id, "tag": tag, "title": title, "display_value": node.text_content(),
            "scale": node.get("scale", "0"), "sign": node.get("sign", ""),
            "format": node.get("format"), "unit": units.get(node.get("unitref"), node.get("unitref", "")),
            "context": contexts.get(node.get("contextref"), {}),
            "definitions": definitions,
            "row_text": row.text_content() if row is not None else "",
            "source": source_url + ("#" + original_id if original_id else ""),
        }
    return facts


def mark_passages(document, source_url):
    # Passages retain source anchors, including a local anchor when the filing has none.
    passages = []
    heading = "Filing disclosure"
    for node in list(document.iter()):
        if node.tag in ("h1", "h2", "h3", "h4", "p", "div"):
            words = node.text_content().strip()
            if 5 < len(words) < 170 and (node.tag.startswith("h") or node.find("b") is not None or node.find("strong") is not None):
                heading = words
        if node.tag not in ("p", "div", "table"):
            continue
        if node.tag == "div" and node.xpath(".//div | .//p | .//table"):
            continue
        if any(parent.tag == "table" or str(parent.tag).lower() in ("ix:hidden", "ix:header") for parent in node.iterancestors()):
            continue
        if node.tag == "table":
            lines = []
            for row in node.xpath(".//tr"):
                lines.append(" | ".join(cell.text_content() for cell in row.xpath("./td | ./th")))
            passage_text = "\n".join(lines)
        else:
            passage_text = node.text_content()
        if len(passage_text.strip()) < 80:
            continue
        passage_id = "reader-passage-" + str(len(passages))
        old_id = node.get("id")
        marker = html.Element("span", id=passage_id)
        node.addprevious(marker)
        passages.append({"id": passage_id, "heading": heading, "text": passage_text,
                         "source": source_url + ("#" + old_id if old_id else "")})
    return passages


def render_reader_html(document, source_url):
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
    script = html.Element("script", src="/static/filing.js")
    document.append(script)
    return html.tostring(document, encoding="unicode", method="html")


def prepare_filing(ticker, year):
    """Prepare one supported original 10-K; reuse the saved result on subsequent runs."""
    destination = DATA / f"{ticker}-{year}"
    destination.mkdir(parents=True, exist_ok=True)
    existing = destination / "filing.json"
    if existing.exists():
        return json.loads(existing.read_text())
    filing = None
    for candidate in Company(ticker).get_filings(form="10-K", amendments=False):
        if str(candidate.period_of_report).startswith(str(year)):
            filing = candidate
            break
    if filing is None:
        raise ValueError(f"No original {year} 10-K found for {ticker}")
    attachment = filing.attachments.primary_html_document
    content = attachment.content
    source_url = attachment.url
    labels = {}
    warnings = []
    # Both custom label locations are supported; official definitions retain their URLs.
    for item in filing.attachments.query("document_type in ['EX-101.SCH', 'EX-101.LAB']"):
        for tag, entries in read_labels(item.content, item.url).items():
            labels.setdefault(tag, []).extend(entries)
    namespaces = re.findall(r'xmlns:us-gaap\s*=\s*[\"\']([^\"\']+)', content)
    if namespaces:
        taxonomy_year = namespaces[0].rstrip("/").split("/")[-1]
        for kind in ("lab", "doc"):
            url = f"https://xbrl.fasb.org/us-gaap/{taxonomy_year}/elts/us-gaap-{kind}-{taxonomy_year}.xml"
            cache = DATA / f"us-gaap-{kind}-{taxonomy_year}.xml"
            try:
                if not cache.exists():
                    cache.write_text(download_file(url, as_text=True))
                for tag, entries in read_labels(cache.read_text(), url).items():
                    labels.setdefault(tag, []).extend(entries)
            except Exception as error:
                warnings.append(f"Could not load {kind} taxonomy labels: {type(error).__name__}. Definitions may be unavailable.")
    rendered, facts, passages = prepare_html(content, source_url, labels)
    payload = {"id": f"{ticker}-{year}", "ticker": ticker, "company": COMPANIES[ticker], "year": year,
        "period_end": str(filing.period_of_report), "filed": str(filing.filing_date),
        "accession": filing.accession_no, "source": source_url, "facts": facts,
        "passages": passages, "warnings": warnings,
        "revision": hashlib.sha256((VERSION + content + json.dumps(labels, sort_keys=True)).encode()).hexdigest()}
    (destination / "original.html").write_text(content)
    (destination / "reader.html").write_text(rendered)
    existing.write_text(json.dumps(payload))
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files-only", action="store_true", help="Download/prepare without connecting to PostgreSQL")
    args = parser.parse_args()
    setup_sec_identity()
    engine = None if args.files_only else database()
    if engine is not None:
        initialize(engine)
    for ticker in COMPANIES:
        for year in YEARS:
            payload = prepare_filing(ticker, year)
            if engine is not None:
                save_filing(engine, payload)
            print(f"Prepared {payload['id']}: {len(payload['facts'])} facts, {len(payload['passages'])} passages", flush=True)
            for warning in payload["warnings"]:
                print(warning, flush=True)


if __name__ == "__main__":
    main()
