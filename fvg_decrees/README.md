# FVG presidential decrees: PDF → ingestion XML 2.0

Python 3.10+, `lxml`, and Poppler (`pdftotext` and `pdfinfo`) are required.
On Debian/Ubuntu install Poppler using `apt-get install poppler-utils`.

```sh
python -m pip install -r requirements.txt
python pdf_decree_to_xml.py 'decree.pdf' 'decree.xml'
# Supply provenance when known:
python pdf_decree_to_xml.py decree.pdf decree.xml \
  --source-url 'https://example.org/decree.pdf' \
  --retrieved-at '2026-10-23T09:00:00+00:00'
python -m unittest -v test_pdf_decree
```

The included `schemas/fvg-legal-ingest-2.0.xsd` and `ingestion_model.py` are the
existing contract and semantic validator from pculurciello/codex, unchanged.
Every export passes both validators before an atomic write. No database connection
is made. No legal validity periods or resolved legal citations are fabricated.

## Cover metadata

The first page is a cover, rather than decree body text. Its text layer yields:
number (canonical and printed with leading zeros), decree date and place, subject,
signatories and signing dates, countersigners and their dates, and the statement
that the file is a copy of a digitally signed original. Additional PDF technical
metadata from `pdfinfo` is retained in `source_representation.raw_metadata`.
Signature names and dates are cover statements, not cryptographic verification.
Publication dates are left absent: references to earlier bulletins in recitals
are not publication metadata for the decree being converted.

## Body and provenance

The converter splits the decree into PREAMBLE/RECITAL, OPERATIVE_SECTION,
OPERATIVE_POINT, CLOSING_FORMULA and SIGNATURE units. Paragraphs may continue
across pages; PDF page and extracted line numbers accompany each unit. The
standalone Decreta heading determines the transition to operative text. Stable
provision identities are created only for explicitly numbered operative points;
provision versions stay empty pending legal temporal reconciliation.

HEADER and PAGE_TRANSCRIPT units preserve the complete extracted text as evidence.
**PAGE_TRANSCRIPT duplicates the structured body.** Consumers should exclude those
units from ordinary full-text indexing or text concatenation. Whitespace in
structured paragraphs is normalized separately; extracted lines remain in raw_text.
PDF bytes are not embedded: retain the original PDF identified by its SHA-256.

## Examples and limits

The two supplied examples produce:

| Decree | Cover date | Signatory | Recitals | Operative points |
| --- | --- | --- | ---: | ---: |
| 0266/Pres., 2013 | 2013-12-27 | Debora Serracchiani | 14 | 2 |
| 013/Pres., 2026 | 2026-02-02 | Massimiliano Fedriga | 8 | 2 |

Both exports are included under `examples/` and validated against the XSD.
The 2013 recital ending “integrazioni;” continues across two pages and is kept as
one unit. Both decrees refer to attachments; referenced attachment content is
not inferred. In particular, the regulation referenced in the 2026 decree is
not present in its supplied two-page PDF.

This is a converter for the demonstrated text-based cover/body layout, not an OCR
or general PDF layout engine. Image-only/empty pages and missing identity/body
headings cause a failure. Complex tables, article-based regulations, nested lists,
quoted text and annex boundaries need additional layout rules and review; their
extracted text is retained in page transcripts. Cover art/logos are not transcribed.
PDFs with a misleading partial text layer require human review even if extraction
succeeds. Parser issues explicitly record these review needs, unknown acquisition
time, attachment references and unknown provision validity. Official status and
legal authority are left false because they are not established by an uploaded copy.
