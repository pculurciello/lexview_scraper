#!/usr/bin/env python3
"""Extract text-based FVG presidential decree PDFs into ingestion XML 2.0."""
import argparse
from datetime import date, datetime, timezone
import hashlib
import re
import subprocess
import uuid
from pathlib import Path
from lxml import etree
from ingestion_model import ORDER, XSD, validate, xml_from_rows

MONTHS = dict(zip('gennaio febbraio marzo aprile maggio giugno luglio agosto settembre ottobre novembre dicembre'.split(), range(1, 13)))
RECITAL = re.compile(r'^(Premess[oaie]|Attes[oaie]|Vist[oaie]|Ritenut[oaie]|Considerat[oaie]|Preso atto|Dato atto|Richiamat[oaie]|Sentit[oaie]|Acquisit[oaie]|Su conforme deliberazione)\b', re.I)

def norm(text):
    return ' '.join(text.split())

def uid(text):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'fvg-pdf-decree:' + text))

def extract_pages(path):
    try:
        result = subprocess.run(['pdftotext', '-layout', '-enc', 'UTF-8', str(path), '-'], capture_output=True, check=True)
    except FileNotFoundError as exc:
        raise ValueError('Install Poppler (pdftotext) before running this script.') from exc
    except subprocess.CalledProcessError as exc:
        raise ValueError('PDF extraction failed: ' + exc.stderr.decode(errors='replace')) from exc
    pages = result.stdout.decode('utf-8').split('\f')
    if pages and not pages[-1].strip():
        pages.pop()
    if not pages or any(not p.strip() for p in pages):
        raise ValueError('Empty or image-only page detected; OCR/review is required before conversion.')
    return pages

def cover_metadata(cover):
    number = re.search(r'Decreto\s+n[°º.]*\s*(\d+)\s*/\s*Pres\.', cover, re.I)
    issued = re.search(r'Trieste,\s*(\d{1,2})\s+(' + '|'.join(MONTHS) + r')\s+(\d{4})', cover, re.I)
    title = re.search(r'oggetto\s*:\s*(.*?)\s*Firmato da\s*:', cover, re.I | re.S)
    if not all((number, issued, title)):
        raise ValueError('Cover must contain decree number, Trieste date, oggetto, and Firmato da; unsupported layout.')
    def people(label, stop=None):
        match = re.search(label + r'\s*:\s*(.*?)(?=' + (stop + r'\s*:' if stop else r'\Z') + ')', cover, re.I | re.S)
        if not match:
            return []
        result = []
        for name, d in re.findall(r'([^\n]+?)\s+in data\s+(\d{2}/\d{2}/\d{4})', match[1], re.I):
            result.append({'name': norm(name), 'date': datetime.strptime(d, '%d/%m/%Y').date().isoformat()})
        return result
    signers = people('Firmato da', 'Siglato da') if re.search('Siglato da', cover, re.I) else people('Firmato da')
    if not signers:
        raise ValueError('No dated cover signatory could be extracted.')
    return dict(number=str(int(number[1])), printed_number=number[1],
                document_date=date(int(issued[3]), MONTHS[issued[2].lower()], int(issued[1])).isoformat(),
                title=norm(title[1]), place='Trieste', signatories=signers,
                countersigners=people('Siglato da'),
                copy_statement=next((norm(x) for x in cover.splitlines() if "originale firmato" in x.lower()), None))

def convert(input_path, output_path, xsd=XSD, source_url=None, retrieved_at=None, annexes=()):
    input_path, output_path = Path(input_path), Path(output_path)
    annexes = [Path(path) for path in annexes]
    if len({path.resolve() for path in [input_path, *annexes]}) != 1 + len(annexes):
        raise ValueError("Decree and annex paths must be distinct.")
    if output_path.resolve() in {path.resolve() for path in [input_path, *annexes]}:
        raise ValueError("Output must not overwrite an input PDF.")
    pages = extract_pages(input_path)
    metadata = cover_metadata(pages[0])
    if len(pages) < 2:
        raise ValueError('The PDF contains no decree body after the cover.')
    if retrieved_at:
        stamp = datetime.fromisoformat(retrieved_at.replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError('--retrieved-at requires a timezone.')
    digest = hashlib.sha256(input_path.read_bytes()).hexdigest()
    key = f'IT-FVG:DPREG:{metadata["document_date"][:4]}:{metadata["number"]}'
    doc_id, rep_id = uid(key), uid(key + ':PDF:' + digest)
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    rows = {t: [] for t in ORDER}
    rows['source_document'] = [dict(id=doc_id, jurisdiction='IT-FVG', document_type='DPREG',
        number=metadata['number'], year=int(metadata['document_date'][:4]), document_date=metadata['document_date'],
        title=metadata['title'], issuer='Presidente della Regione autonoma Friuli Venezia Giulia',
        metadata={**metadata, 'canonical_key': key, 'metadata_source': {'pdf_page': 1},
                  'signature_cryptographically_verified': False})]
    rows['source_representation'] = [dict(id=rep_id, source_document_id=doc_id, source_system='FVG_DPREG_PDF',
        format='PDF', source_url=source_url or input_path.resolve().as_uri(), retrieved_at=retrieved_at,
        content_hash=digest, local_object_key=str(input_path.resolve()), media_type='application/pdf',
        is_official=False, legal_authority=False,
        raw_metadata={'page_count': len(pages), 'extractor': 'pdftotext -layout',
                      'pdf_metadata': pdf_info(input_path), 'authority_assessment': 'NOT_ESTABLISHED_FROM_UPLOADED_COPY',
                      'raw_artifact_preserved': True, 'provision_validity_inferred': False})]
    ordinals = {}
    def unit(kind, text, parent=None, label=None, loc=None):
        ordinal = ordinals.get(parent, 0) + 1
        ordinals[parent] = ordinal
        identity = uid(rep_id + ':' + str(len(rows['document_unit'])))
        rows['document_unit'].append(dict(id=identity, source_representation_id=rep_id, parent_unit_id=parent,
            unit_type=kind, unit_label=label, ordinal=ordinal, raw_text=text, normalized_text=norm(text),
            source_locator=loc or {}, review_status='UNREVIEWED'))
        return identity
    def issue(kind, text, context=None):
        rows['parser_issue'].append(dict(id=uid(run_id + ':' + str(len(rows['parser_issue']))), ingestion_run_id=run_id,
            issue_type=kind, candidate_text=text, source_locator={}, parser_context=context or {}, resolution_status='OPEN'))
    root = unit('DOCUMENT', '', loc={'pdf_pages': list(range(1, len(pages) + 1))})
    unit('HEADER', pages[0], root, loc={'pdf_page': 1})
    # Exact extracted page text remains available even when paragraph boundaries are uncertain.
    for i, page in enumerate(pages[1:], 2):
        unit('PAGE_TRANSCRIPT', page, root, loc={'pdf_page': i, 'role': 'extraction_evidence'})
    preamble = unit('PREAMBLE', '', root)
    operative = None
    buffer, locations, kind, label = [], [], 'RECITAL', None
    parent = preamble
    def flush():
        nonlocal buffer, locations
        if buffer:
            identity = unit(kind, '\n'.join(buffer), parent, label, {'pdf_lines': locations})
            if kind == 'OPERATIVE_POINT':
                canonical = key + ':PUNTO' + label
                if any(r['canonical_key'] == canonical for r in rows['provision_identity']):
                    issue('AMBIGUOUS_PROVISION_IDENTITY', canonical)
                else:
                    rows['provision_identity'].append(dict(id=uid(canonical), source_document_id=doc_id,
                        unit_type=kind, unit_label=label, canonical_key=canonical,
                        ordinal=len(rows['provision_identity']) + 1))
                    rows['document_unit'][-1]['source_locator']['candidate_provision_key'] = canonical
            buffer, locations = [], []
    for page_no, page in enumerate(pages[1:], 2):
        for line_no, raw_line in enumerate(page.splitlines(), 1):
            line = raw_line.strip()
            if not line:
                continue
            if re.fullmatch(r'Decreta\s*:?', line, re.I):
                flush()
                if operative:
                    raise ValueError('Multiple Decreta headings; manual review required.')
                operative = unit('OPERATIVE_SECTION', line, root, loc={'pdf_page': page_no, 'line': line_no})
                parent, kind, label = operative, 'TEXT', None
                continue
            point = re.match(r'^(\d+)\s*[.)]\s+', line) if operative else None
            signature = bool(re.match(r'^[-–]\s*.+\s*[-–]$', line))
            closing = bool(re.match(r'^Il presente decreto\s+(sarà|è)\s+pubblicato', line, re.I))
            if point or signature or closing or (not operative and RECITAL.match(line)):
                flush()
                kind = 'OPERATIVE_POINT' if point else ('SIGNATURE' if signature else ('CLOSING_FORMULA' if closing else 'RECITAL'))
                label = point[1] if point else None
                parent = root if signature or closing else (operative or preamble)
            buffer.append(line)
            locations.append({'page': page_no, 'line': line_no})
    flush()
    if not operative:
        raise ValueError('No standalone Decreta heading found; refusing an incomplete structural conversion.')
    issue('PROVISION_VALIDITY_NOT_INFERRED', 'Decree date and signature dates do not establish provision validity.')
    if not retrieved_at:
        issue('UNKNOWN_RETRIEVAL_TIME', 'Acquisition time was not supplied.')
    attachment_metadata = []
    for index, path in enumerate(annexes, 1):
        annex_pages = extract_pages(path)
        provenance = dict(attachment_index=index, filename=path.name,
            source_url=path.resolve().as_uri(), content_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
            page_count=len(annex_pages), pdf_metadata=pdf_info(path),
            relationship_basis='USER_SUPPLIED', completeness_verified=False)
        attachment_metadata.append(provenance)
        annex_root = unit('ANNEX', '', root, str(index), {'attachment_index': index})
        for page_no, text in enumerate(annex_pages, 1):
            unit('PAGE_TRANSCRIPT', text, annex_root,
                 loc={'attachment_index': index, 'pdf_page': page_no,
                      'source_url': provenance['source_url'], 'content_hash': provenance['content_hash'],
                      'role': 'attachment_content'})
        issue('ANNEX_STRUCTURE_REQUIRES_REVIEW', path.name,
              {'attachment_index': index, 'policy': 'Full extracted text retained; article, table and list structure not inferred.'})
    rows['source_document'][0]['metadata']['attachments'] = attachment_metadata
    rows['source_representation'][0]['raw_metadata']['attachment_policy'] = (
        'Separate PDF artifacts carried as ANNEX children; locators identify their own files, not decree pages.')
    if re.search(r'\ballegat[oaie]\b', ' '.join(pages[1:]), re.I):
        issue('ANNEX_REFERENCE_REQUIRES_REVIEW', 'Verify supplied attachments against decree references; completeness is not inferred.',
              {'supplied_attachment_count': len(annexes), 'policy': 'No completeness inferred from references or filenames.'})
    issue('PDF_STRUCTURE_REQUIRES_REVIEW', 'Review inferred paragraph boundaries, tables, annexes and reading order against the PDF.',
          {'page_transcripts_preserved': True, 'transcripts_duplicate_structured_content': True})
    rows['ingestion_run'] = [dict(id=run_id, source_representation_id=rep_id, parser_name='fvg_pdf_decree',
        parser_version='1.1.0', status='COMPLETED_WITH_ISSUES', started_at=now,
        finished_at=datetime.now(timezone.utc).isoformat(), statistics={'pages': len(pages), 'attachments': len(annexes),
        'attachment_pages': sum(a['page_count'] for a in attachment_metadata),
        'document_units': len(rows['document_unit']), 'parser_issues': len(rows['parser_issue'])})]
    xml = xml_from_rows(rows)
    validate(xml, xsd)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp = output_path.with_suffix(output_path.suffix + '.tmp')
    temp.write_bytes(etree.tostring(xml, encoding='UTF-8', xml_declaration=True, pretty_print=True))
    temp.replace(output_path)
    return rows

def pdf_info(path):
    result = subprocess.run(['pdfinfo', str(path)], capture_output=True, text=True, check=True)
    return {line.split(':', 1)[0]: line.split(':', 1)[1].strip() for line in result.stdout.splitlines() if ':' in line}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--xsd', type=Path, default=XSD)
    parser.add_argument('--annex', type=Path, action='append', default=[],
                        help='Separate attachment PDF; repeat in attachment order')
    parser.add_argument('--source-url')
    parser.add_argument('--retrieved-at', help='Known acquisition time, with timezone (not conversion time)')
    args = parser.parse_args()
    try:
        rows = convert(args.input, args.output, args.xsd, args.source_url, args.retrieved_at, args.annex)
    except (ValueError, OSError, subprocess.CalledProcessError, etree.Error) as exc:
        parser.exit(1, f'Conversion failed: {exc}\n')
    print(f'Validated {args.output}: {len(rows["document_unit"])} units, {len(rows["parser_issue"])} review issues')

if __name__ == '__main__':
    main()

