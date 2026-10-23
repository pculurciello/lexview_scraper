# Giunta regionale deliberations: PDF to ingestion XML 2.0

Run from `fvg_decrees/` with Python 3.10+, lxml and Poppler. The deliberation
entry point shares the decree extraction, annex handling and validation code.
The XSD and database contract are unchanged.

```sh
python -m pip install -r requirements.txt
# Debian/Ubuntu; OCR is needed for the supplied covers:
sudo apt-get install poppler-utils tesseract-ocr tesseract-ocr-ita
python pdf_deliberation_to_xml.py deliberation.pdf deliberation.xml \
  --ocr-cover --annex attachment-1.pdf --annex attachment-2.pdf
python -m unittest -v test_pdf_decree test_pdf_deliberation
```

Without `--ocr-cover`, the first-page text layer must contain the demonstrated
cover fields. The supplied covers have corrupt font mappings: nonempty extracted
text alone is insufficient. `--ocr-cover` renders page 1 with pdftoppm and reads
it with Tesseract, default language `ita`. Use `--ocr-language eng` when only that
model is installed; the committed examples were generated with `eng`, and the
number, date, attendance and secretary were checked against rendered covers.
OCR spelling and punctuation, including subject text, remain review candidates.
There is no OCR fallback for body or annex pages, nor automatic quality guarantee
for a partial text layer. Empty pages cause conversion failure.

## Metadata and structure

`source_document.document_type` is `DGR`; keys use `IT-FVG:DGR:YEAR:NUMBER`.
The cover yields the act number, meeting date, subject, eleven attendance entries
(name, role, presente/assente), and secretary. These describe the meeting, not
cryptographically verified signers. No place, publication date or legal effective
date is inferred from filenames, earlier acts, or annex references.

The body uses PREAMBLE/RECITAL and the standalone DELIBERA heading to identify
OPERATIVE_SECTION and numbered OPERATIVE_POINT units. Final presidential and
secretary role lines become SIGNATURE units. Numbered point identities are stable;
provision versions remain empty pending temporal reconciliation. Multiline and
cross-page text is preserved, along with page and line locators. Nested lists,
annex articles and tables retain extracted text but do not receive semantic parsing.

HEADER stores the original text-layer evidence; XML-forbidden control characters
are preserved as visible Unicode escapes. HEADER_OCR stores the separate OCR
transcript. OCR cover metadata is explicitly marked for review with a parser issue.
PAGE_TRANSCRIPT body units duplicate structured text and should be excluded from
ordinary full-text indexing. Annex PAGE_TRANSCRIPT units with role
`attachment_content` hold the annex content and should be included.

Repeat `--annex` in attachment order. Each ANNEX has independent page locators,
filename, source reference, SHA-256 and PDF metadata. A caller-supplied attachment
relationship does not prove completeness. Original PDF bytes are not embedded;
retain them using the recorded hashes. `--source-url`, `--retrieved-at` and `--xsd`
work as for decrees. Missing retrieval time remains unknown; uploaded copies do
not establish official status or legal authority. Conversion validates before
atomic output replacement; failed OCR, extraction or validation preserves output.

## Supplied examples

| XML | Meeting date | Main PDF pages | Annex pages | Operative points |
| --- | --- | ---: | --- | ---: |
| `examples/DGR-2026-1507.xml` | 2026-10-16 | 5 | 6 | 2 |
| `examples/DGR-2024-1506.xml` | 2024-10-11 | 4 | 2, 5, 16 | 3 |

Both contain the supplied annexes and pass XSD and semantic validation. The main
PDF covers determine identity; filenames are not used as metadata evidence.
Example source references use portable `inputs/` paths instead of workspace paths.
The original PDFs must be supplied separately to resolve those references.
