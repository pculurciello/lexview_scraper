#!/usr/bin/env python3
"""Giunta regionale deliberation PDFs and annexes to FVG ingestion XML 2.0."""
import argparse
import re
import subprocess
from datetime import date
from pathlib import Path
from lxml import etree
from ingestion_model import XSD
from pdf_decree_to_xml import MONTHS, norm, convert as convert_act


def cover_metadata(cover):
    number = re.search(r'Delibera\s+n[°º.]?\s*(\d+)\b', cover, re.I)
    meeting = re.search(r'seduta\s+del\s+(\d{1,2})\s+(' + '|'.join(MONTHS) + r')\s+(\d{4})', cover, re.I)
    people = list(re.finditer(r'^\s*(.+?)\s+(Vice\s*Presidente|Presidente|Assessore)\s+(presente|assente)\s*$', cover, re.I | re.M))
    subject = re.search(r'oggetto\s*:\s*(.*)', cover, re.I | re.S)
    secretary = re.search(r'^\s*([^\n]+?)\s+Segretario\s+generale\s*$', cover, re.I | re.M)
    if not all((number, meeting, people, subject, secretary)):
        raise ValueError('Unsupported deliberation cover: require number, meeting date, subject, attendance and secretary. Try --ocr-cover for a broken text layer.')
    title = cover[subject.start(1):people[0].start()].strip()
    if not title:
        raise ValueError('Empty deliberation subject')
    return dict(number=str(int(number[1])), printed_number=number[1],
        document_date=date(int(meeting[3]), MONTHS[meeting[2].lower()], int(meeting[1])).isoformat(),
        title=norm(title), organ='Giunta regionale',
        attendees=[dict(name=norm(m[1]), role=norm(m[2]), attendance=m[3].lower()) for m in people],
        secretary=norm(secretary[1]), metadata_basis='MEETING_COVER_STATEMENTS')


def convert(input_path, output_path, xsd=XSD, source_url=None, retrieved_at=None,
            annexes=(), ocr_cover=False, ocr_language='ita'):
    return convert_act(input_path, output_path, xsd, source_url, retrieved_at, annexes,
                       act_type='DGR', ocr_cover=ocr_cover, ocr_language=ocr_language)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--annex', type=Path, action='append', default=[])
    parser.add_argument('--xsd', type=Path, default=XSD)
    parser.add_argument('--source-url')
    parser.add_argument('--retrieved-at')
    parser.add_argument('--ocr-cover', action='store_true', help='Render and OCR page 1; requires Tesseract and pdftoppm')
    parser.add_argument('--ocr-language', default='ita', help='Installed Tesseract language (default: ita)')
    args = parser.parse_args()
    try:
        rows = convert(args.input, args.output, args.xsd, args.source_url, args.retrieved_at,
                       args.annex, args.ocr_cover, args.ocr_language)
    except (ValueError, OSError, subprocess.CalledProcessError, etree.Error) as exc:
        parser.exit(1, f'Conversion failed: {exc}\n')
    print(f'Validated {args.output}: {len(rows["document_unit"])} units')


if __name__ == '__main__':
    main()
