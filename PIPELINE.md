# Lexview 1964–2026 HTML → FVG XML pipeline

**Default output is now database-aligned ingestion XML 2.0** under `xml-db/`.
See [DATABASE_ALIGNMENT.md](DATABASE_ALIGNMENT.md) for the new XSD and staging exporter.
Use `--xml-format light` for the historical light 1.0 behavior described below.
The new format embeds editorial notes, records snapshot scope, and keeps provision
validity and target-FK resolution explicitly pending instead of inventing them.

```sh
python -m pip install -r requirements.txt
python lexview_pipeline.py --start-year 1964 --end-year 2026 --output downloads/corpus
```

The inclusive default range is 1964–2026. This builds a current snapshot of laws
already published in the database, including repealed laws; it cannot include future
laws from the remainder of 2026 or historical versions not exposed by Lexview.
The full archive job may take many hours. Requests are sequential, with at least
one second between requests; `--delay`, `--timeout`, and `--attempts` are configurable.

## Stages and recovery

1. For each year, submit the real search form with **Escludi abrogati** unchecked.
   Follow **tutti** to retrieve all results. Discovery succeeds only if the number
   of unique law identities equals the result count reported by Lexview.
2. For each law, follow the actual whole-law link, enumerate all temporal entries,
   and download each original annotated HTML export. Dates and file hashes are checked.
3. Convert each downloaded version to the selected XML contract and validate it
   before atomically publishing the XML. The default uses `html_to_database_xml.py`
   and ingestion 2.0; `--xml-format light` uses the original converter/XSD.
4. Retain editorial notes (embedded document units by default; sidecars in light
   mode) and rebuild a SQLite outgoing/incoming candidate citation index.

Rerun the same command to resume. Verified files are reused, failed laws are retried,
and converted files are reused only when source, schema, converter and pipeline
fingerprints match. Corrupted files are not accepted as completed work. Successful
versions from a partially failed law can still be converted. An interrupted run can
be resumed; no state depends on a running browser.

Resume preserves the downloaded snapshot. To discover newly published laws and
refresh mutable current/historical entries, run with **both** flags:

```sh
python lexview_pipeline.py --refresh-catalog --refresh-html --output downloads/corpus
```

Checkpointed conversion, manifests, and cached catalogues are stored in the output
directory. Do not modify them manually during a run. Run only one process per output
directory. Keep the output directory in place: state contains absolute XML paths.

```text
downloads/corpus/
  catalog.json             # discovered years and law URLs
  html/LR_YEAR_NUMBER/      # original exports and per-law temporal manifest
  xml-db/LR_YEAR_NUMBER/    # database-aligned XML with embedded editorial notes
  xml/LR_YEAR_NUMBER/       # optional legacy light XML, plus *.editorial.json
  state.json               # version status, hashes, validity and conversion fingerprints
  citations.sqlite         # references in all successfully converted versions
  report.json              # selected scope, completed/failed counts and errors
```

In ingestion 2.0 act/provision identities remain stable, while representations and
physical units have export-specific identities. Snapshot intervals remain evidence
metadata; individual provision validity is deferred to reconciliation. The report
distinguishes conversion completion from parser issues and database loading.
Legacy light XML uses hash-based document IDs, `metadati/data`, and `numero=""` for
unnumbered paragraphs; these compatibility conventions are not the new DB contract.

Failures produce a nonzero exit status and an explicit report. A `--limit` run is
labelled as limited and is not a full-corpus completion claim. Discovery failures
are reported rather than treating inaccessible years as empty years. HTML that
cannot be represented by the current parser is retained and reported as a failure;
it is not silently flattened into a supposedly complete law.

## Smaller runs and cached conversion

```sh
# Discover 1964 and download/convert its first law only:
python lexview_pipeline.py --start-year 1964 --end-year 1964 --limit 1

# Convert/revalidate an existing pipeline archive with no network access:
python lexview_pipeline.py --offline --output downloads/corpus

# Incoming references to law 26/2015:
python citation_index.py --database downloads/corpus/citations.sqlite --target fvg:lr:2015:26
```

Offline mode requires pipeline catalogues and per-law manifests, not just loose HTML
files. Results preserve version identity: fourteen references in fourteen temporal
versions do not imply fourteen different citing laws. Incoming results cover only
the successfully converted part of this archive, not all possible external sources.
See [CITATIONS.md](CITATIONS.md) for schema mapping and editorial-note limitations.

## Validation performed

- Live discovery for 1964 found all three laws reported by Lexview.
- Its first law was downloaded in both available temporal versions and converted
  successfully, including the original unnumbered paragraphs.
- All fourteen previously downloaded versions of law 7/2024 were converted and
  schema-validated, with a citation index built from those outputs.
- The entire 1964–2026 database has **not** been downloaded or converted in this session.
  The full command above runs that job and records any archive-wide exceptions.
