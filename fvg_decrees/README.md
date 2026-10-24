# FVG presidential decrees: PDF → ingestion XML 2.0

Python 3.10+, `lxml`, and Poppler (`pdftotext` and `pdfinfo`) are required.
On Debian/Ubuntu install Poppler using `apt-get install poppler-utils`.

```sh
python -m pip install -r requirements.txt
python pdf_decree_to_xml.py 'decree.pdf' 'decree.xml' --annex 'attachment.pdf'
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
one unit. The committed XML examples include the supplied attachments: the 2026 regulation
(18 pages) and the 2013 planning opinion (5 pages). They remain distinct from the
decree body and do not create decree provision identities.

This is a converter for the demonstrated text-based cover/body layout, not an OCR
or general PDF layout engine. Image-only/empty pages and missing identity/body
headings cause a failure. Complex tables, article-based regulations, nested lists,
quoted text and annex boundaries need additional layout rules and review; their
extracted text is retained in page transcripts. Cover art/logos are not transcribed.
PDFs with a misleading partial text layer require human review even if extraction
succeeds. Parser issues explicitly record these review needs, unknown acquisition
time, attachment references and unknown provision validity. Official status and
legal authority are left false because they are not established by an uploaded copy.


## Separate attachments (version 1.1)

Repeat `--annex PATH` for each attachment, in the intended order. Attachment links
are supplied by the caller; filenames and decree references do not prove a match
or completeness. Each file is extracted independently and retained as an ANNEX
with PAGE_TRANSCRIPT children. Annex text includes the complete extracted article,
table and list text; their semantic structure is not reconstructed.

`source_document.metadata.attachments` records each attachment's filename, local
source URI, SHA-256, page count, PDF technical metadata and user-supplied relationship.
Each annex page locator carries its attachment index, source URI, hash and page
number. Those page numbers belong to the attachment PDF. The single schema-mandated
source_representation describes the main decree PDF; annex provenance lives in the
metadata and locators, without changing the XSD or inventing extra representations.

For full-text indexing exclude decree PAGE_TRANSCRIPT evidence (role
`extraction_evidence`), but include annex PAGE_TRANSCRIPT content (role
`attachment_content`); annex pages have no duplicate structured text. Retain all
original PDFs alongside XML. No acquisition timestamp, publication date, effective
date or cryptographic signature verification is inferred for attachments.

All inputs must have nonempty text layers. An unreadable annex aborts conversion
before output replacement. Duplicate inputs and an output path equal to an input
are rejected. Existing outputs survive extraction or validation failures.
The tests cover annex page numbering, text, provenance, and failed-annex
output preservation. Run `python -m unittest -v test_pdf_decree` from this directory.

The committed XML examples include decree and annex text. Their local source URIs and object keys use portable `inputs/` references; place the original PDFs there to resolve those references. SHA-256 values identify the original bytes.
