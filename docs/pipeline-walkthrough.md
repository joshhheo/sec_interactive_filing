# Footnote: read the current pipeline alongside the code

This guide describes the working tree, including its staged and unstaged source changes. [HANDOFF.md](../HANDOFF.md) explains the product and its history; where they differ, the source is the reference for current behavior. The corpus counts in the handoff (16 browsable reports and 8 supporting reports) describe its recorded database state. They were not re-counted for this guide. No preparation, live database verification, or live Ollama inference was run.

Open each file beside its notes. Line spans are inclusive; blank lines separate blocks and have no runtime effect. Closing braces, brackets, and parentheses end the construct described by the preceding line. The walkthrough groups closely related statements so you can see what state each block receives and hands off.

## Start with one figure

```text
Offline: EDGAR → filing HTML → facts + passages + annotated HTML → PostgreSQL
Startup: PostgreSQL → Reader → concept groups, fiscal-period lookup, BM25 indexes
Click:   filing iframe → parent page → GET fact → amount + comparison + notes + corrections
Context: no cached answer → POST explain → rank evidence → Ollama if evidence exists
                           → validate citation IDs → cache → show claims and sources
```

Use the fabricated test fixture in [tests/test_filing_reader.py](../tests/test_filing_reader.py#L13): the visible number is `350`, its tag is `us-gaap:Assets`, and it refers to context `c` and USD unit `u` with scale `6`. Preparation gives it the local ID `reader-fact-0`, keeps the original SEC-style fragment `#original`, and resolves `c` to an instant of 2025-12-31 and entity identifier `123`. At request time Python computes `350 × 10⁶` and formats it as `$350,000,000`. In a prepared filing with that period end, it would seek a baseline with the same tag, unit, entity, and dimensions. The fixture itself only tests preparation and supplies no baseline. The nearby paragraph in the fixture is a passage; proximity alone does not make it an explicitly linked footnote.

Three identities matter:

| Identifier | Example | Meaning |
|---|---|---|
| Filing ID | `MSFT-2025-Q2` | App key for ticker and fiscal period; fiscal year need not equal the calendar year of period end. FY omits the suffix. |
| Reader ID | `reader-fact-0`, `reader-passage-0` | Sequential within the prepared filing, used by the browser and evidence lookup. Exhibit passages also get a distinct evidence ID and keep their document-local anchor. |
| SEC accession / original fragment | accession string / `#original` | Identifies the submitted filing / an element in its source. An accession is used for the preparation skip, while fragments are used for navigation. |

Key syntax as you read: Python indentation defines blocks; `None` means absent; `dict[key]` expects the key, while `dict.get(key, fallback)` supplies a fallback for a missing key. `a or b` falls back on any falsy value. `for … in …` visits items; `continue` skips one iteration and `return` exits the function. `with` scopes setup and cleanup; `try/except` handles specified failures. In JS, `const` keeps the binding fixed while its object can still change, `let` permits rebinding, and `await` pauses that async function until a promise settles. `?.` handles a missing receiver, `??` handles only null/undefined, and `===` compares without coercion. In HTML, an `id` names a single element; a class is reusable. Python and JavaScript regexes both appear here; Python raw strings such as `r"\d+"` keep the backslash for the regex engine.

## 1. The preparation run

[Identity setup](../src/setup_sec_identity.py). **1–3:** import the operating-system environment interface, dotenv loader, and EdgarTools identity setter. **5–10:** define the function; `load_dotenv()` loads environment settings, `os.getenv("SEC_user_agent")` reads one name, and `set_identity` passes it to EdgarTools. There is no explicit missing-value branch in this wrapper. The database loader independently loads the project-root dotenv file. Do not print values to trace this path.

[prepare.py](../src/filing_reader/prepare.py), in execution order:

| Lines | What the statements do and why |
|---|---|
| 2–13 | Import CLI parsing, hashing, JSON, regex, date arithmetic, and URL operations; import EdgarTools' company interface, lxml's tree tools, and local identity/storage functions. |
| 15–21 | Map supported tickers to names and numeric CIKs. Copy the visible years before appending one earlier year, so preparation has FY2023 while the catalog still exposes only FY2024–25. |
| 303–308 | Parse CLI args (no custom options), set SEC identity, build the DB engine, initialize tables, and index existing prepared payloads by accession with a dict comprehension. |
| 309–315 | For each company, use its CIK to resolve the SEC entity. Query amendments separately, then materialize the 10-K/10-Q/8-K results with a date window. The colon inside the f-string forms the date-range string (currently 2022-01-01 through 2026-04-30); fiscal focus later determines inclusion. |
| 316–329 | First collect every 8-K in that query so it can be considered for any selected report. Then process only originals of form 10-K/10-Q. Pass in any saved record for the accession, skip on `None`, unpack the two-element result, persist, and print counts immediately via `flush=True`. |
| 332–333 | The module guard calls `main()` when executed with `-m`, so importing the module for its constants/functions does not run preparation. |
| 259–266 | Collect accession IDs of amendments with the expected `/A` form and equal report date. A saved row skips work when its version and amendment list match. This check is early, before the main attachment is read, and does not detect newly available 8-K material by itself. List equality is order-sensitive. |
| 268–276 | Get the primary HTML attachment, content, and URL. Encode the content to bytes and parse as UTF-8. Iterate `ix:nonnumeric` elements and collect the two named DEI fiscal-focus values, stripping surrounding whitespace. |
| 277–284 | Convert fiscal year text to `int`, read the fiscal period, reject unsupported years, build the app ID, and annotate the document. Missing DEI entries raise on direct indexing; unexpected year text raises on conversion. |
| 285–290 | Allocate the attachment store and related passages list. Only visible years collect nearby current reports. Prepare matching amendments for any prepared year. |
| 291–300 | Assemble the payload (identity, dates, facts, passages, related reports, amendments, source IDs). Hash the version, original HTML, and sorted-key JSON of the related passages. SHA-256's hex digest is a revision label, not a signature or a digest of every payload field. Capture source IDs before adding `main`, then return the payload and document dictionary together. |

### Parsing and annotation inside preparation

| Lines | What executes and what decision it encodes |
|---|---|
| 24–28 | Read context/unit definitions first because facts refer to them by ID. Mark numeric facts, then mark searchable passages on the same tree. The functions mutate the parsed document and also return structured records. |
| 68–78 | Iterate all nodes; `split(":")[-1].lower()` takes the local part of a tag. For each context with an ID, scan descendants including the element itself and collect instant/start/end/identifier text. `text_content()` combines text under a node. |
| 79–87 | Store each explicit/typed dimension as axis and member. For a typed member, serialize the first child element with `with_tail=False` to exclude text after that child. Save by context ID. Units are stripped aggregate text, not a normalized numerator/denominator structure. |
| 90–98 | Make a snapshot of `ix:nonfraction` nodes with `list` because the walk mutates the tree. Skip numeric elements under hidden/header ancestors. Generate a local ID from the current count and preserve the original ID separately. |
| 99–106 | Insert a span before the fact and move the fact into it. Annotate the fact itself with a data attribute, focus order, button semantics and tooltip. Build a fallback title: split off the prefix, then use zero-width regex lookarounds to insert a space between lowercase and uppercase letters. |
| 107–115 | Walk parents until a table row (`tr`) or the root. XPath `./td \| ./th` unions direct cells; stop at the first cell containing a tagged number. Before that, keep the first nonempty whitespace-normalized cell text as a label. This is a layout heuristic. |
| 116–128 | Add an original fragment only if present. Save the verbatim displayed text, scale default `"0"`, sign default empty, optional transform name, resolved unit/context, row label and source. On a missing unit resolution, fall back to the unit reference string; on missing context, use an empty dict. |
| 131–139 | The `*` makes `local` keyword-only. Start with a default heading and MD&A off. XPath selects h1–h4, p, li and divs without nested div/p/table/li; exclude nodes inside tables and hidden/header containers. Normalize whitespace before indexing. |
| 140–148 | For text under 170 characters without descendant links, recognize Item 2/7 beginning with Management as MD&A; another Item heading turns it off. Heading tags or a direct bold/strong child set the running heading. A short heading can change state even if it is skipped as a passage for being under 30 characters. |
| 149–164 | Make a new local passage ID; when enabled, insert its span before the element. Preserve an original fragment when present. Record text, heading, source, local status and MD&A flag. The flag reflects the running heading heuristic, not a parsed section boundary guaranteed by the filing. |
| 29–37 | Index `ix:footnote` elements by original ID and facts by the fragment in their saved source URL. Link by original ID because that is what the relation names. |
| 38–49 | Only process the fact-footnote arcrole. Split whitespace-separated `fromrefs` and `torefs`; ignore unknown fact IDs and visit every combination for a known fact. A new note uses direct indexing; a missing referenced note can raise. |
| 50–63 | Give each referenced note a passage ID once, inserting an anchor only when outside hidden/header ancestors. Preserve note text and SEC fragment. Attach the passage ID to each linked fact without duplicates. |
| 64–65 | Render the annotated tree and return HTML, facts, passages. Extraction precedes cleanup of the presentation HTML. |
| 167–173 | Remove scripted/embedded/form/base/metadata/link elements with `drop_tree()`. Snapshot each attribute list before deletion; remove event handlers and selected active attributes. |
| 174–184 | Resolve non-fragment href/src relative to the source URL. Retain only HTTP(S) for those resolved attributes; fragment-only references stay local. External HTTP anchors open a separate tab with opener/referrer protections. |
| 185–198 | Ensure a head exists; inject CSS for pointer, focus, selection and fragment scroll margin. Add the reader script only in interactive mode. Serialize the tree as HTML text. This cleanup works alongside the browser sandbox and HTTP policy described below. |

### Additional documents

| Lines | What executes and why |
|---|---|
| 201–212 | For the chosen reporting period, accept 8-Ks with filing dates strictly after period end and at or before the original filing date plus 30 days. Accept the 8-K itself and EX-99-prefixed attachments only if HTML. This is a date/type gate, not relevance confirmation. |
| 213–223 | Parse the attachment; number it using the size of the shared document dict. Mark passages with actual anchors, copy each anchor into `anchor`, reset `local` to false (it is outside the main frame), attach the document/form/exhibit/date, and assign a distinct `current-report-N` evidence ID. |
| 224–230 | Without an original fragment, construct an EDGAR text-fragment URL using up to eight words, URL encoding, and explicit escaping for hyphens. This is a navigation hint, not a guaranteed match. Save the prepared attachment with no click script. |
| 233–242 | Sort amendments by filing date then accession using a lambda (an inline function returning a tuple). Require matching form and report date, then parse the primary document and extract its structured facts. |
| 243–256 | Remove click affordance attributes but retain anchors and facts; render without the script. Allocate a document ID in the same attachment map and emit an amendment record with source, date, and facts. This step pairs documents; it does not infer which facts changed. |

## 2. Persistence

[storage.py](../src/filing_reader/storage.py)

| Lines | How it works |
|---|---|
| 1–11 | Load JSON/env/path/SQL utilities. `Path(__file__).resolve().parents[2]` climbs from the module to project root. `VERSION = "reader-7"` is used for preparation and cache reuse. |
| 14–19 | Load root dotenv, read `db_connection_string`, fail early if missing, and create a SQLAlchemy engine. `pool_pre_ping` probes reused connections; the driver gets a five-second connection-establishment timeout. |
| 22–40 | Use `engine.begin()` for a transaction. Create filing JSONB storage keyed by app ID and explanation JSONB keyed by filing/fact/version. The filing reference is a foreign key. `created_at` defaults to the DB's current timestamp. |
| 42–49 | Store HTML as text in a separate table keyed by filing ID plus document ID. Keeping HTML separate allows startup to load payloads without transferring the full documents. |
| 52–59 | SQL `INSERT … ON CONFLICT … DO UPDATE` writes or replaces the payload. `:id` and `:payload` are bound parameters; JSON serialization plus `CAST` writes JSONB. `EXCLUDED` is the row that the insert attempted to write. |
| 60–72 | Delete old HTML rows for this filing, build one dictionary per new document with a comprehension, and execute the insert over that list. All of these writes share a transaction, so an error rolls back the payload and document changes together. |
| 75–84 | Fetch one document by both keys over a scoped connection; `scalar_one_or_none()` returns a single column value or `None` if no row. |
| 87–97 | Join filings to their `main` document and return ordered payload scalars. The join excludes incomplete entries without a primary document; it does not filter by preparation version. |
| 100–107 | Fetch cached explanation on the caller's connection using the full three-part identity. |
| 110–122 | Serialize and insert the cache result, ignoring a duplicate key. The caller's transaction controls commit/rollback. Old cache versions are not deleted on re-prepare. |

## 3. Startup and deterministic fact handling

[server.py](../src/filing_reader/server.py)

| Lines | How it works |
|---|---|
| 1–24 | Import CLI, dates, Decimal arithmetic, HTTP/path/URL, EdgarTools transforms, Pydantic, BM25, SQL and local modules. `STATIC` points beside the module; `STOP` is a set of common words excluded from search. |
| 27–33 | Lowercase, extract ASCII letter runs with `[a-z]+`, and keep tokens over two letters outside the stop-word set. Search uses these tokens; source excerpts keep their text. |
| 480–489, 492–493 | Parse `--port` as an integer (default 8765); initialize DB tables, configure local Ollama model/context size from environment/defaults, construct a Reader, and serve on loopback using threaded request handling. The guard starts this only when executed as a module. |
| 127–138 | Retain engine/model and load filing payloads into memory keyed by ID. Create indexes for search, concept groups and fiscal lookup. Start the cache version with reader version and model name. Fiscal lookup keys are `(ticker, year, fiscal_period)`; older missing periods default to FY. |
| 139–155 | Group every fact under its concept tuple. For each passage kind, tokenize headings plus text into parallel rows and build BM25 only if at least one row has tokens. Build a passage ID dictionary for direct footnote lookup. `earnings` is a fallback corpus supported by the server; current preparation writes `current_reports`. |
| 157–174 | Return lightweight catalog entries for exposed years. Include amendment form/date/source/document ID only, excluding their facts, and default absent original form/period fields to 10-K/FY. |
| 36–52 | Strip the recorded display text; apply a named transform if present; parse a `Decimal` and reject nonfinite values. Within `localcontext()`, raise precision to at least the input coefficient length before `scaleb` shifts its base-10 exponent. Invalid conversions return `None` for the caught exceptions. If the sign field is minus, flip the sign after scaling. |
| 55–77 | Return `None` for absent amount. Look up exact special unit strings for USD/EUR/USD per share/shares/pure, otherwise use the final colon-separated piece as a suffix. Suppress a negative sign on zero. The f-string specifier `:,f` groups thousands and renders fixed point. |
| 80–91 | Convert dimensions to sorted axis/member tuples so order differences do not affect grouping. Return tag, raw resolved unit string, entity identifier and dimensions together. Period is separate: start date plus instant if present, otherwise end date. This is exact string identity, so synonymous concepts or differently represented contexts do not automatically match. |
| 94–110 | Parse target end; skip undated facts. Without a duration, require exact start/end (including no start for an instant). With a duration, require a start and allow up to seven days of drift separately in end date and duration. Parse amount and deduplicate on actual start/end/amount. |
| 111–113 | Succeed only when one distinct candidate remains. `popitem()` supplies the key and fact via tuple unpacking, and the return carries a one-item source list. Zero or multiple distinct candidates yield implicit `None`. Since duplicates overwrite the dict value, the last equivalent fact supplies the source. |

### Comparison: follow the branches in order

| Lines | Decision |
|---|---|
| 176–190 | Require the selected fact to end on this filing's period end, and parse its amount. Derive duration from the dates if present. An older comparison column can supply a baseline but receives no comparison when it is itself clicked in this filing. |
| 191–198 | If duration exists, FY must be 350–378 days. A quarter must be 75–105 days, or Q2 YTD 165–195 days, or Q3 YTD 255–285 days. Out-of-range durations return no comparison. These are app thresholds. |
| 199–207 | Default to the same period in the previous fiscal year. For quarterly instants and standalone periods, use sequential mode: Q2→Q1 and Q3→Q2 in the same FY; Q1→prior FY Q3 because the policy chooses the previous 10-Q. Q1→Q3 crosses Q4. |
| 208–220 | Get the concept bucket in current and baseline reports. Prefer the baseline report's recorded end. With no loaded baseline, sequential Q2/Q3 with start dates infer the end as the day before start; nonsequential durations infer end minus 365 days. Other cases return no result, including Q1 without a loaded baseline. |
| 222–228 | For sequential duration Q2/Q3, require consecutive periods before searching. Search current report first, then baseline report if no unique usable match is returned. Within an individual search, a conflict fails that search; the subsequent search can still succeed. |
| 229–250 | Unpack prior amount/date(s)/source. Subtract prior from current; use prior end or full date range as baseline label. Show prior-fiscal-period basis for sequential mode and “prior year” otherwise. Calculate percentage only if prior amount > 0, using signed one-decimal formatting. Add explicit plus to positive amount changes and return all comparison fields. |

Work the [quarter/YTD test](../tests/test_filing_reader.py#L50) by hand. The same FY2025 Q2 report contains standalone revenue of 30 (October–December) and YTD revenue of 55 (July–December). The first takes the sequential branch and finds 25 in FY2025 Q1: change +5, +20.0%. The second takes the prior-year branch and finds 50 in the selected filing's comparative column: change +5, +10.0%. Same tag and end date, different start dates and therefore different questions.

### Amendments and the initial click response

| Lines | Decision |
|---|---|
| 278–286 | Begin with no corrections; require an original parsed amount and a usable reporting end. Save concept identity and the last established amount. |
| 287–292 | For each prepared amendment, consider only equal concept tuples and call the exact-period matcher. Here `start` is positional and `duration` is omitted, so there is no date tolerance. |
| 293–306 | Skip unresolved or unchanged matches. For each new amount, attach formatted amount, amendment date/form, saved document ID and source, then update the last amount to detect later changes. A filing-level amendment can still appear without a figure-level change. |
| 275–276 | Form the version string from reader/model version, filing revision and stable-key JSON of the comparison. Fact and filing IDs are separate cache-key columns. Model context size and amendments are not explicitly hashed into this key. |
| 308–318 | Look up the selected fact and compute comparison, read the cache on a DB connection, normalize the amount and resolve all explicitly linked note IDs. |
| 319–326 | Return raw fact, formatted amount, comparison, saved explanation or null, footnotes, and deterministic corrections. This request never calls Ollama. |

## 4. Search, model and cache

[server.py](../src/filing_reader/server.py)

| Lines | What happens and why |
|---|---|
| 116–124 | Pydantic classes describe one claim (explanation string and list of citation IDs) and their containing list. The type annotations define fields; `extra="forbid"` rejects unexpected keys. The schema itself does not limit the number of claims. |
| 252–259 | Convert the query to tokens, look up a BM25 index by filing and source kind, and stop if unavailable. BM25 compares words against corpus rows based on factors such as word frequency and document length; it does not read for meaning. |
| 260–273 | Iterate passages paired with their scores via `zip`. Keep the highest strictly positive eligible result, keeping the earlier result on ties. Exclude explicitly linked notes and table-marked records from this search; when requested require MD&A. Return a list with up to one hit. MD&A filtering is after scoring on the same original index. |
| 328–339 | Recompute the same comparison and cache version as GET. Inside a DB transaction, hash the filing/fact/version into a PostgreSQL transaction advisory lock. Concurrent requests for that key wait, then recheck the cache before computing. Returning a cached result exits the transaction. |
| 340–348 | Use the row label or fallback title, strip dimension prefixes and split camel-case dimension terms for search. Resolve explicit notes directly by their IDs. |
| 349–361 | Combine the first explicit note, best general passage, best MD&A passage, and best 8-K passage (or earnings fallback). Copy records before adding default form/date, then deduplicate by ID. That yields at most four unique candidates; the general and MD&A hit can be the same. |
| 363–369 | Start with no claims. If there is evidence, include comparison details when available; deliberate field selection omits sources and extra fields from the comparison subobject. |
| 371–386 | Send each candidate's ID, first 475 characters and filing date. The packet includes the clicked item, tag, source display string, context, filing date and deterministic comparison. The app retains full evidence separately. |
| 387–395 | Ask for up to two specific, distinct details tied to the selected metric and scope. Require supplied IDs and matching periods for causal attribution; distinguish later developments; permit abstention. Adjacent string literals concatenate into one instruction. |
| 396–399 | Slice the response to its first two claims. Reject any retained claim with an empty citation list or IDs outside the evidence dictionary. Converting IDs to a set lets `issubset` check membership against dictionary keys. `model_dump()` converts each validated model to a plain dict. Citation ID validation establishes traceability, not whether the passage actually supports the prose. |
| 400–402 | Save claims, full evidence and model name, even for a valid empty answer. With no candidates, skip model inference and cache the empty answer. If an exception escapes, this transaction does not save the result. |

[model.py](../src/filing_reader/model.py)

| Lines | What happens and why |
|---|---|
| 1–16 | Import JSON, process-local thread lock, urllib's request/error types and schema-validation error. Define a runtime-error subclass for model availability and a more specific subclass for requests over the context budget. |
| 19–23 | An Ollama instance stores model and context settings and one lock shared by its callers in this server process. |
| 25–30 | Make a system message with the instruction plus the evidence-as-data rule, and a user message carrying serialized JSON evidence. `ensure_ascii=False` retains literal Unicode in this serialization. Pydantic produces a JSON Schema for the requested response type. |
| 31–36 | Estimate conservatively from serialized UTF-8 message bytes, schema bytes, and 4096 bytes of headroom. Compare against the configured context size before sending. This is a conservative heuristic, not tokenization. It rejects the whole request rather than trimming it further. |
| 37–46 | Build the payload with streaming/thinking off, requested schema, zero temperature, context size and 2048 predicted output tokens. Encode to bytes; passing data makes urllib use POST. Address only the local Ollama chat endpoint. |
| 47–56 | Serialize model HTTP calls through the process lock. Open and decode the response with a 600-second network timeout. Map 404 to an install hint, other HTTP status errors to a generic HTTP error, and network/decoding troubles to an availability message. `raise … from exc` retains the underlying cause. The DB lock above is per cache key; this model lock serializes all calls in this server process. |
| 57–64 | Require an outer dict, reject a length-stop response, and parse the inner `message.content` JSON string with Pydantic. Map missing/bad response fields or rejected schema to a model error. |

## 5. HTTP boundary

[server.py](../src/filing_reader/server.py)

| Lines | How it works |
|---|---|
| 405–418 | Subclass the standard HTTP handler; startup sets its shared Reader. Convert dict/list to JSON bytes or string to UTF-8, then send status, type, byte length, no-sniff, no-referrer, and no-store headers. No-store is HTTP policy; it does not disable the PostgreSQL cache. |
| 419–423 | Add a restrictive CSP to HTML: same-origin scripts/frames/connections, inline/same-origin styles, SEC/data images, and no bases/forms; unspecified resources default to blocked. End headers and write the bytes. The frame sandbox adds a separate boundary. |
| 425–436 | Parse only the URL path, trim surrounding slash characters and split at slashes. Serve the shell for root and a fixed allowlist of static filenames with their MIME types. The filings endpoint gives the catalog JSON. |
| 437–452 | Recognize the filings prefix, reject an unknown filing ID, then map `document` to `main` or accept a source document ID from that filing's prepared list. A GET loads matching HTML by both IDs; absent data is 404. |
| 453–461 | Dispatch GET to the deterministic fact response. POST to the same fact's `/explain` checks Origin against the HTTP Host plus a custom request header. It uses the URL's fact ID, not a submitted prompt or body. |
| 462–471 | Fallthrough means 404; missing keys are reported as unknown fact; model errors return 503; caught `ValueError` returns 422; other exceptions are logged server-side and return generic 500. The 503 path leaves the initial fact response available in the UI. |
| 473–477 | The HTTP library calls `do_GET` and `do_POST`; both dispatch to the shared router with the method string. |

## 6. Browser execution

[index.html](../src/filing_reader/static/index.html): **1–7** declare HTML5, English, UTF-8, viewport scaling, title and stylesheet. **8–20** open the page and toolbar, connect labels to input/select IDs, reserve dropdown and report-meta elements, and provide the external source anchor. **21–28** reserve the amendments list and document wrapper; the iframe can execute scripts and open separate tabs but lacks `allow-same-origin`, giving it an opaque origin. The wrapper holds loading status and the footer gives the click cue. **29–32** define the hidden fact region, close button and polite announcement area. **33–36** define the source modal with a second sandboxed iframe that lacks script permission. **37–39** load the parent script after those DOM elements exist and close the document.

[filing.js](../src/filing_reader/static/filing.js) runs inside the main iframe:

| Lines | What happens |
|---|---|
| 1–3 | Split the frame path; the leading slash creates an empty first entry, so index 3 of `/api/filings/ID/document` is the filing ID. |
| 5–17 | Remove the previous highlight, add it to the newly selected element, and send the ID, filing ID and bounding rectangle to the parent. `dataset.readerFact` is the camel-case accessor for `data-reader-fact`. Rect coordinates are relative to the iframe viewport. |
| 19–27 | A single click handler uses `closest` to find a marked element even if a child received the click. Prevent its default action when selecting a fact; send dismissal for other clicks. |
| 28–33 | Enter or Space activates a focused tagged number; prevent default behavior such as Space scrolling. |
| 34–39 | Accept jump messages only from the parent, check the passage ID regex, then scroll to the anchor if found. The regex anchors require the entire ID to match. |
| 40–46 | Tell the parent it can clear the loading overlay, and forward Escape as dismissal because frame key events are separate. |

[app.js](../src/filing_reader/static/app.js) controls the parent page. Read its bottom first; that is where the initial catalog request is triggered.

| Lines | What happens |
|---|---|
| 1–9 | `byId` wraps the DOM lookup. Keep an in-flight explanation map, catalog array, open filing, committed company and current selection reference. |
| 10–22 | Create text/class nodes and source links. Assigning `textContent` renders a string as text instead of HTML; external links get a separate tab, no opener and no referrer. Defaults in parameter declarations apply when an argument is omitted/undefined. |
| 23–34 | Await the response and parse JSON, throw a readable error for failed status, or return the data. `jump` uses messaging to reach the opaque-origin frame. The section helper makes a titled container. |
| 380–390 | Load the catalog from the API, sort by descending ISO end date using the comparator callback, and populate choices. Errors are shown in the document status. Invoke the async routine at script end. |
| 308–326 | Rebuild filing options based on selected company and form; empty company means any supported company. Keep the previous option if still valid; otherwise let the selection change and open the corresponding report. Spread syntax puts the DOM options into an array for `.some()`. Bind DOM references for the company picker. |
| 327–347 | Trim/lowercase the typed query, use a Set to deduplicate company names then sort them, and build substring-match buttons. A choice commits the company, copies it into the input, closes suggestions and re-filters. No matches yields explanatory text. Typing alone does not commit a different company. |
| 349–362 | Suggest on input or focus; when choices are visible, ArrowDown focuses the first and Enter chooses the sole suggestion. Prevent default keyboard behavior in those branches. |
| 364–379 | On focus leaving the picker, hide choices and restore the committed name; Escape also restores it. Bind form and filing changes to the appropriate filter/open routine. |
| 243–264 | Set active filing from the select, close the old popup, look up the catalog record, reset the status and clear amendments. If there are amendments, create an expandable list with prepared and EDGAR links. |
| 266–282 | For no valid report, clear title/dates/link, show status and load a blank frame. Otherwise display fiscal/report dates, set the original link and load the local document endpoint. |
| 284–306 | Check that a message came from the main frame's window and that its filing ID matches the currently open filing. Dismissal closes popup/suggestions; readiness clears status. A selection also needs the expected fact-ID shape and finite coordinates. |
| 227–242 | Create a fresh selection object as an identity token and store the frame coordinates. Display a loading popup, GET the clicked fact, discard a stale result if selection identity changed, and render before requesting an explanation on a cache miss. GET failure updates only the still-current popup. |
| 35–51 | Rebuild the panel. Use label/title and formatted/raw amount with their respective fallbacks. Display instant or joined date range; if normalization failed, surface the recorded unit/scale/sign and the original fact link. |
| 52–61 | Add an expandable notice only when the API supplies figure-level corrections; show each separately with date, form, amount and source controls. |
| 62–74 | Display the baseline end date and basis, amount change, percent or `N/A`, and source. When no comparison exists show `N/A`. The API also has `baseline` and `baseline_amount`; this block does not render them. |
| 76–88 | Map full evidence by ID and collect IDs already cited by claims in a Set. Optional chaining and nullish fallbacks support the empty/cache-miss case. |
| 89–109 | Show uncited linked footnotes, with longer text (>350 characters) behind an expander, then show claims. A completed answer with nothing to display gets `N/A`. A cited linked note is still available through the claim source. |
| 111–128 | For an incomplete explanation show progress or an error with Retry. The retry clears the data error, redraws and requests again. Recompute popup coordinates whenever the panel is rebuilt. |
| 130–151 | A local passage uses a jump button; an external passage normally uses a link. When a prepared document ID exists, open a modal with the local source URL and its explicit anchor or the hash extracted from the source URL. Encode the fragment; `showModal()` opens the native dialog. |
| 153–154 | Bind the dialog close button and remove its iframe src on close. |
| 156–184 | Render claim text, deduplicate citation IDs, then build an expandable source list. Label sources from date/form/exhibit/heading and show the full recorded passage, prepared jump/link and external EDGAR link. It expects the server's validated IDs to resolve. |
| 186–200 | Reuse any promise for the same fact URL so repeated clicks in the page share the POST. `.finally()` removes it when settled, success or failure. Save data/error, and only redraw if that selection reference is still current. The database lock handles requests outside this browser page. |
| 202–225 | Closing clears selection and hides the popup. Add iframe origin coordinates to get page-viewport position. Prefer below if it fits, else above, then clamp both axes with min/max. Close on button, resize and Escape unless the dialog is open. |

[style.css](../src/filing_reader/static/style.css): layout only. **1** is a compact run of rules: universal border-box sizing, body type/colors, header/brand, a viewport-height workspace using column flex layout, flex toolbar, label/select and report/link styling, expanding document area and full-size iframe, a same-area loading overlay that disappears on `[hidden]`, footer, panel typography/spacing, status, keyboard focus, and narrower-screen overrides at 900px/700px. **3** enables toolbar wrapping, styles the input, and adds space between adjacent controls of the same type. **5–15** size the modal and backdrop, align its header, give the iframe the remaining height, indent cited quotations, and hide/limit the amendment area. **17–28** make the popup fixed (viewport coordinates), layer it above regular content, cap dimensions and keep scrolling inside it. **29–42** place and style its close button and leave space for it in the heading. **44–55** set a relative company picker and an absolutely positioned menu below it as wide as the picker, layered above the popup. **56–67** style full-width choices, hover/focus, and the no-match message. CSS `#` matches IDs, `.` matches classes, `+` matches the next sibling, `:empty` tests no child content, and `min()`/`calc()` size against the available viewport.

## 7. What the tests establish

[tests/test_filing_reader.py](../tests/test_filing_reader.py):

- **1–12:** imports test tools, parser and production entry points; the test class uses unittest discovery.
- **13–32:** construct a tiny filing tree and check hidden exclusion, instant/unit/scale/SEC source retention, passage URL, cleanup, and reader script. It does not verify every sanitizer rule.
- **34–48:** two facts share a tag; the explicit relationship names one fact ID. Assert the note only reaches that fact and preserves the source fragment.
- **50–73:** build synthetic quarterly and YTD facts, inject only needed Reader data through `Reader.__new__(Reader)` (no DB setup), then check both percentage branches.
- **75–101:** create prior EPS using dict unpacking `**current` with overridden value/dates/source. Check Q1 vs prior FY Q3 basis, change and source; clear the prior lookup and require absence.
- **103–123:** exact same number and wrong-tag number produce no correction. Replace the matching amount and require the normalized corrected amount and source.
- **125–145:** build an isolated Reader with a fake transaction context, mock model/search and one selected fact.
- **147–158:** patch imported cache functions to use a small in-memory cache, feed a valid shaped response, verify identical replay, one model invocation, an 8-K search attempt and full saved evidence.
- **160–166:** model supplies an invented ID; assert the handler-level function raises a citation error and does not call the cache writer.

[tests/test_model.py](../tests/test_model.py): **1–12** import test utilities/client/classes. **13–22** use `BytesIO` to simulate an HTTP response and inspect the mock's captured request, schema and parsed result. **24–28** pass oversized evidence and check the network stub is never called. **30–34** simulate an HTTP 404 and require the install hint. `patch` temporarily swaps a dependency at the name the code actually uses; the with-block restores it. These tests cover decisions with fixtures, including cache replay, but do not exercise SEC fetching, real PostgreSQL concurrency, browser interaction, or model quality.

[src/filing_reader/__init__.py](../src/filing_reader/__init__.py) is the one-line package description. [requirements.txt](../requirements.txt) pins direct dependencies: EdgarTools for filing lookup and transforms, lxml for trees, psycopg2 for the PostgreSQL driver, Pydantic for model response shape, dotenv for config, rank-bm25 for ranking, and SQLAlchemy for connection/transaction/query handling. [.gitignore](../.gitignore) ignores local credentials, the virtual environment, bytecode, macOS metadata, prepared files in the old data location, and the handoff. [README.md](../README.md) gives the product surface and roadmap.

## 8. Read these decisions before integration

- **Preparation is a snapshot.** The in-memory payloads and indexes need a server restart after refresh. HTML is fetched per document request. The skip rule can keep an old snapshot even when related filings have changed; adding a retrieval feature does not automatically regenerate existing rows.
- **Structure and evidence have different jobs.** Tag/unit/entity/dimensions/period plus a parsed amount govern matching. An exact linked-note relation is stronger evidence of association than a search hit. A search hit, a valid citation ID, and a semantically supported claim are three separate checks; only the first two are mechanical here.
- **The current retrieval boundary is deliberate and narrow.** It covers saved narrative passages, explicit inline footnotes, and selected nearby HTML current-report attachments. It does not follow arbitrary exhibit, contract, or filing references. It also does not index table rows as passage candidates.
- **API and popup details:** The comparison metadata includes the prior amount and full date range, but the popup currently renders the prior end date, basis, changes and link. The previous 10-Q rule applies to both standalone quarterly durations and quarterly instants. All linked footnotes are available in the initial response; only the first is submitted in the model candidate packet.
- **A useful next exercise:** pick one known example that needs a referenced exhibit. Write down the exact clicked fact, its context, the anchor that points to the exhibit, the expected passage, and what statement that passage actually supports. Then trace where the example falls out of the current preparation → ranking → citation sequence. This establishes a concrete retrieval test case before changing integration behavior.

Verified against the working tree with `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v`: 10 passed.
