# Lexview scraper and conversion pipeline

Extracted from [pculurciello/codex](https://github.com/pculurciello/codex) at commit `7ec533d8bf0f06cd99b195e6dd0ff2fb76bcebf7`.

```sh
python -m pip install -r requirements.txt
python lexview_html_versions.py --year 2024 --number 7 --output downloads
```

See [SCRAPER.md](SCRAPER.md) for single-law collection and [PIPELINE.md](PIPELINE.md) for the resumable corpus pipeline. Conversion dependencies, schemas, fixtures, and tests are included for standalone operation.

# HTML → FVG legal ingestion XML

**The recommended database-oriented intermediate format is now ingestion XML 2.0.**
See [DATABASE_ALIGNMENT.md](DATABASE_ALIGNMENT.md) for the schema review, database
mapping, temporal/citation policies, examples and commands:

```sh
python -m pip install -r requirements.txt
python html_to_database_xml.py scarico.htm output.xml
python database_xml_to_json.py output.xml staging.json
```

The remainder of this page describes the legacy light 1.0 converter, retained for
compatibility. The archive pipeline defaults to the new format; use `--xml-format light`
to reproduce the legacy output.

To collect annotated HTML for every listed temporal version of a law, see
[the Lexview scraper instructions](SCRAPER.md) and `lexview_html_versions.py`.
For the resumable **1964–2026 database pipeline**, see [PIPELINE.md](PIPELINE.md).
For outgoing/incoming citation encoding and actual HTML examples, see [CITATIONS.md](CITATIONS.md).

Python 3.10+ and `lxml` are required. No NIR or DSP schema is needed.

```sh
python -m pip install -r requirements.txt
python html_to_fvg.py scarico.htm scarico.xml
```

By default the script validates against the supplied `schemas/fvg-legal-light-1.0.xsd`.
To use another location:

```sh
python html_to_fvg.py scarico.htm scarico.xml --xsd /path/to/fvg-legal-light-1.0.xsd
```

The output is UTF-8 and uses `urn:fvg:legal:light:1.0`. Validation happens before
writing the output. Missing required metadata, duplicate structural IDs, and unsupported
body layouts cause a nonzero exit status rather than fabricated content.

## Supported input

The supplied `scarico.htm` uses `.intestazione`, `.articolo`, `.rubrica`, `.comma`,
and `.el` classes. Numbering is read from the initial `strong` or `b` label.
HTML namespaces are ignored. Article IDs are generated deterministically, including
suffixes such as `12 bis`; comma suffixes such as `1 ante` are supported.

The act date and number come from the heading, not the consolidated-text date or
references in the body. The title comes from `.intestazione strong`.
Missing or different metadata can be supplied explicitly:

```sh
python html_to_fvg.py input.htm output.xml \
  --tipo leggeRegionale --numero 3 --data 2001-02-12 \
  --titolo 'Disposizioni in materia di ...' \
  --source-url 'https://example.org/original-document'
```

Other overrides: `--id`, `--ente`, `--organo`, `--encoding`.
Without `--source-url`, provenance uses the local input file URI. A SHA-256 of the
original bytes is recorded. UTF-8 is tried first, then the declared encoding;
Windows-1252 is used for invalid UTF-8 exports such as the supplied sample.
Use `--encoding` when the input has another known encoding.

## Deliberations

Administrative conversion expects one `.dispositivo` section containing `.punto`
elements, or direct `p`, `li`, or `ol/li` children. An optional `.premesse` section
holds the preamble. Numbered paragraphs start with `1.` or `1)`; otherwise their
document order supplies point numbers. Do not include a section heading as a point.
For different HTML, select existing sections with XPath:

```sh
python html_to_fvg.py deliberazione.htm deliberazione.xml \
  --tipo deliberazioneGiunta --data 2025-02-10 --numero 100 \
  --titolo 'Approvazione dei criteri' \
  --premesse-xpath '//*[@id="premesse"]' \
  --dispositivo-xpath '//*[@id="decisione"]'
```

This path is tested with a synthetic deliberation. No real deliberation HTML was
provided, so other layouts need selectors or a parser extension.
`--mode testoLibero` is an explicit fallback that flattens the body; it does not
recover article or administrative structure.

## Mapping limits

- Editorial change notes (`ndr`, `note`, `ndr_titolo`, `note_titolo`) and decorative
  note markers are excluded. Consolidation history is not represented.
- Chapter headings and HTML presentation are omitted: the XSD has no corresponding
  elements. Numbered sublists and quoted amendments remain plain text inside their
  containing comma or letter; quoted commas are not counted as operative commas.
- An article without commas retains its repeal marker `( ABROGATO )` in `rubrica`,
  because this schema has no article-level text or repeal-status element.
- Citations are extracted from hyperlinks in retained body text only. Plain-text
  references are preserved but are not automatically classified as citations.
  Regional `xmllex.aspx?anno=…&legge=…` links yield `fvg:lr:YEAR:NUMBER` destinations
  and optional article/comma/letter fragments. `risolta` denotes a parsed regional
  destination, not a verified remote resource or legal conclusion. National NIR
  destinations and explicit enumerations of articles are supported too (see CITATIONS.md). Other links are
  `parziale` when a URI is available, otherwise `nonRisolta`. Relative URLs require
  `--source-url` for absolute URI resolution. Citation sources refer to generated
  structural IDs. Offsets are omitted, as permitted by the schema.
- This is a converter for the documented HTML layouts, not arbitrary legal HTML.
  Metadata for publication, annexes, and content absent from the input is not invented.

## Checks

```sh
python -m unittest discover -s tests -v
```

The supplied law produces 38 articles, 77 operative commas, 33 letters, and 30
linked citations. Its four nested quoted commas are retained as text.
The included `examples/scarico.xml` validates against the supplied XSD.
