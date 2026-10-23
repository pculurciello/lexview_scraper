# Review: intermediate XML aligned with create_database.sql

The supplied PostgreSQL 15+ DDL is retained unchanged at
[`database/create_database.sql`](database/create_database.sql). The new ingestion
contract is [`schemas/fvg-legal-ingest-2.0.xsd`](schemas/fvg-legal-ingest-2.0.xsd),
namespace `urn:fvg:legal:ingest:2.0`. The old light 1.0 format remains an explicit
compatibility format. A breaking namespace change is intentional: the old model
conflated an act, an exported snapshot, and evidence passages.

This is an ingestion interchange format, not a replacement for PostgreSQL or a
serialization of drafting-case runtime state. One XML contains one act, one acquired
representation, its parsed tree, citations, issues, and optional reviewed graph rows.
Multiple XML files may describe different representations of the same act.

## Mapping and corrections

| Database table | Intermediate XML policy |
| --- | --- |
| `source_document` | Stable act UUID, jurisdiction/type/year/number, date, title, issuer and metadata. The act ID never includes the temporal export hash. |
| `source_representation` | Separate UUID for the act + source URL + original HTML hash; original encoding, local object key, acquisition timestamp when known, source authority flags, snapshot scope. |
| `provision_identity` | Stable UUID and canonical key for an explicitly numbered chapter/article/paragraph/letter/operative point. Its identity survives a text change. |
| `document_unit` | Acquired representation-specific physical tree, own text, ordinal, HTML XPath/anchor, context and review status. Headings, recitals, notes and quoted amendments are retained. |
| `provision_version` | Supported in the XSD but **not fabricated** from a single HTML snapshot. Until reconciliation, a unit has no `provision_version_id`; its locator records a candidate stable identity instead. |
| `citation` | Exactly one citing origin; baseline extraction uses `citing_document_unit_id`. Explicit quoted text, extraction method and parsed candidate target keys are preserved. Target foreign keys remain absent and status is `UNRESOLVED` until database lookup. |
| `ingestion_run` / `parser_issue` | A new audited parse execution, parser version, row counts, unknowns and unresolved cases. A recognized link is not silently promoted to a verified legal conclusion. |
| `knowledge_entity` / `legal_assertion` / `legal_relation` | Optional extension points with the supplied columns and evidence references. Baseline extraction does not manufacture assertions or semantic relations. |

All XML row field names are actual SQL column names. The typed JSON staging exporter
preserves JSONB values as objects, booleans as booleans and UUIDs as strings. Missing
nullable fields are omitted, not replaced by empty UUIDs or invented facts. Timestamp
values require explicit timezones. Confidence values are not guessed.
Source evidence references are checked inside the XML. Target act/provision IDs,
introducing-act references and graph entity endpoints may refer to records already
stored outside this one-act file; only a database-aware loader can verify those FKs.

The extensible SQL vocabulary is respected: document types and unit types are bounded
strings, not closed XSD enums. Recommended document codes are `LR`, `DGR`, `REG`,
`DPREG`, `DD`, `DECRETO`, and `OTHER`. Other jurisdictions and document codes can use
the same contract. Extracting another region's HTML still needs a suitable source
adapter; a common schema does not make every regional website structurally identical.

## Temporal identity and evidence

For law 7/2024:

```text
act key:                IT-FVG:LR:2024:7
provision key:          IT-FVG:LR:2024:7:ART1:COM4
representation identity: act UUID + source URL + original byte hash
physical unit identity:  representation UUID + HTML XPath
```

Article canonical keys do not incorporate chapter placement. The parent FK records
the tree; moving an article between chapters must not automatically give it a new
legal identity. Unnumbered historical paragraphs remain physical units with no
invented stable legal number. Quoted articles/paragraphs inside amendments are physical
evidence, not newly owned provisions of the citing act.

A snapshot headed “dal 11/06/2026 al 07/08/2026” records start `2026-06-11`, inclusive
end `2026-08-07`, and exclusive end `2026-08-08` in representation `raw_metadata`.
This is **document snapshot scope**, not a newly introduced version of every provision.

Before populating `provision_version`, a reconciliation step must:

1. Assemble each provision's operative subtree in source order. A physical unit's
   own text alone is not an article's complete text. Keep quoted legislative text;
   distinguish editorial notes and navigation markers from operative content.
2. Compare observations by stable provision identity and known interval. Merge
   adjacent equivalent observations where justified; retain gaps and contradictions.
3. Distinguish missing/unknown observations from repealed provisions; do not mark
   every provision `IN_FORCE` merely because its document has a current snapshot.
4. Resolve introduction/amendment evidence before setting `introduced_by_*`.
5. Produce nonempty `[start,end)` ranges and reconcile existing open intervals in
   one transaction before inserting later versions, respecting the exclusion constraint.

This reconciliation is **not implemented by the baseline converter**. The XML makes
the uncertainty explicit and retains enough source evidence for that next stage.
Manually curated provision versions can be represented; validation rejects overlapping
intervals within an XML. PostgreSQL must also reject overlaps with already stored rows.

## Citation direction, resolution, and relations

The inspected 11 June 2026 version of law 7/2024 contains this actual reference:

```text
articolo 4, comma 3, della legge regionale 26/2015
```

Its XML citation has a UUID source **document unit**, with this JSONB-compatible
`parsed_reference`:

```json
{
  "target_document_key": "IT-FVG:LR:2015:26",
  "target_provision_key": "IT-FVG:LR:2015:26:ART4:COM3",
  "link_parse_status": "PARSED",
  "context": "BODY"
}
```

Other fields preserve the original href, navigation URI, link wording and parsed
act/provision components. Yet `target_document_id` and `target_provision_id` are
absent and `resolution_status` is **UNRESOLVED**: a parsed name is not proof that
the corresponding database rows exist. This corrects the legacy `risolta` label.

The resolver should match the act's natural key and the provision's canonical key,
verify they belong together, then set real FK values and resolution status. National
NIR identifiers are retained as external identifiers; do not create them under the
default FVG jurisdiction or pretend they are already ingested regional acts.

Incoming citations are a reverse query over the same citation table, not duplicated
reverse edges. After genuine resolution:

```sql
SELECT * FROM fvg_laws.citation WHERE target_document_id = :target_document_id;
```

Before resolution, candidate mentions must be labelled as such:

```sql
SELECT * FROM fvg_laws.citation
WHERE resolution_status = 'UNRESOLVED'
  AND parsed_reference->>'target_document_key' = 'IT-FVG:LR:2015:26'
  AND parsed_reference->>'context' = 'BODY';
```

Editorial notes now live **inside XML** as `document_unit` rows, with their own source
locators and citations. For a numeric footnote link, `cited_text` retains the full
note wording, while `parsed_reference.link_text` preserves the numeric link label.
The context distinguishes `BODY`, `EDITORIAL`, and `QUOTED_OR_AMENDMENT_TEXT`.
An editorial note saying law B added words to law A is evidence about a possible
amendment; its outgoing link A → B is not automatically a verified semantic edge
“A amends B”. Relations require correct direction, entities, and the evidence demanded
by `legal_assertion` / `legal_relation`.

Do not store every citation as `legal_relation`: the SQL requires an assertion or
provision-version source. A document-unit citation alone cannot satisfy that model.
Runtime drafting tables (`drafting_case`, requirements, proofs and generated drafts)
remain a separate application layer; they cannot be derived from a downloaded act.

## Database constraints and remaining design decisions

- Stable act identification currently requires a known year and number. Your
  `UNIQUE NULLS NOT DISTINCT(jurisdiction, document_type, year, number)` cannot
  distinguish multiple unnumbered acts of one type/year. Supporting them needs an
  authoritative identifier policy and likely a unique source key/schema change;
  the converter asks for identity metadata instead of fabricating a number.
- Old archives have no exact per-file acquisition timestamp. They carry
  `retrieval_time_known=false` and an issue, not the current conversion time.
  New downloads now record timestamps. Do not silently treat PostgreSQL's default
  `retrieved_at=now()` as a verified historical acquisition timestamp; either supply
  evidence, explicitly accept an ingestion-time fallback, or allow unknown retrieval
  times through a reviewed database migration.
- `is_official` and `legal_authority` remain false until established. The Lexview
  disclaimer states the reproduction is not a legally authoritative publication;
  a genuine official gazette representation must be assessed separately.
- `provision_identity` parent changes, renumbering, and reused labels need review.
  Stable keys based on source labels are not a substitute for legal identity analysis.
- The SQL parent triggers check immediate owner consistency, not recursive cycles.
  The XML validator checks both ownership and cycles. A future importer still needs
  transactions, uniqueness/conflict handling and database-side integrity checks.
- Re-parsing the same representation reuses stable unit UUIDs but creates a new
  ingestion-run UUID. The database has one unit tree per representation, not a full
  immutable parse-tree history per ingestion run; preserve/audit revisions explicitly
  rather than blindly overwriting reviewed units.

The supplied SQL has **not** been modified or executed. No PostgreSQL server was
available in this environment, so a real PostgreSQL loading/constraint test has not
been performed. Tests check XML/XSD constraints, semantic invariants, and field-name
alignment with the supplied DDL; these do not substitute for a database integration test.

## Commands and compatibility

```sh
python -m pip install -r requirements.txt

# Recommended intermediate format (metadata is inferred when available):
python html_to_database_xml.py scarico.htm output.xml

# Administrative example with explicit metadata:
python html_to_database_xml.py deliberazione.htm output.xml \
  --document-type DGR --year 2025 --numero 100 --data 2025-02-10 \
  --titolo 'Approvazione dei criteri'

# Typed table-shaped staging rows; this does NOT connect to PostgreSQL:
python database_xml_to_json.py output.xml staging.json

# Pipeline now defaults to database-aligned XML under xml-db/:
python lexview_pipeline.py --start-year 1964 --end-year 2026

# Explicit legacy output for older consumers:
python lexview_pipeline.py --xml-format light --start-year 1964 --end-year 2026

# Local reverse candidate index, excluding editorial mentions:
python citation_index.py --database downloads/corpus/citations.sqlite \
  --target IT-FVG:LR:2015:26 --context BODY
```

The pipeline includes schema/parser/temporal-context fingerprints so old light
outputs are not accepted as new ingestion XML. The legacy `html_to_fvg.py` command
and its original XSD remain available. `ingestion_model.py` contains the checked
field specification and regenerates the committed XSD; update them together.

The full real-law example is
[`examples/database/lr-2024-7_2026-06-11.xml`](examples/database/lr-2024-7_2026-06-11.xml).
It contains 1,527 stable provision identities, 2,385 physical units (including 132
editorial notes), and 833 explicit references: 537 body references, 164 quoted/amendment
references, and 132 editorial references. The physical tree includes quoted articles;
they are not promoted to owned provision identities. All citation targets remain
database-unresolved, and no provision versions or semantic relations are invented.

All fourteen cached law 7/2024 versions and both law 1/1964 versions were converted
to the new format and validated. Deliberation, decree and regulation structures were
tested using clearly labelled synthetic fixtures, not claimed as live portal verification.
The full 1964–2026 database has not been crawled or loaded into PostgreSQL.
