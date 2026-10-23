import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from lxml import etree
from ingestion_model import validate
from pdf_deliberation_to_xml import cover_metadata, convert

ROOT = Path(__file__).parent
COVER = '''Delibera n° 12
Estratto del processo verbale della seduta del
11 ottobre 2024
oggetto:
Example subject
Example PERSON Presidente presente
Other PERSON Vice Presidente assente
Third PERSON Assessore presente
Secretary NAME Segretario generale
In riferimento all'oggetto
'''

class DeliberationTests(unittest.TestCase):
    def test_cover_identity_and_attendance(self):
        metadata = cover_metadata(COVER)
        self.assertEqual(metadata['document_date'], '2024-10-11')
        self.assertEqual(metadata['title'], 'Example subject')
        self.assertEqual(metadata['attendees'][1]['attendance'], 'assente')
        self.assertEqual(metadata['secretary'], 'Secretary NAME')
        with self.assertRaises(ValueError):
            cover_metadata('reference to delibera 55/2020')

    def test_ocr_path_keeps_evidence_and_dgr_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp)/'a.pdf', Path(tmp)/'a.xml'
            source.write_bytes(b'fixture')
            with patch('pdf_decree_to_xml.extract_pages', return_value=['broken\x01', 'Visto il testo;\nDELIBERA\n1. First point\nIL PRESIDENTE\nIL SEGRETARIO GENERALE']), patch('pdf_decree_to_xml.extract_cover_ocr', return_value=COVER), patch('pdf_decree_to_xml.pdf_info', return_value={}):
                rows = convert(source, target, ocr_cover=True)
            validate(etree.parse(str(target)).getroot())
            self.assertEqual(rows['source_document'][0]['document_type'], 'DGR')
            self.assertEqual(rows['provision_identity'][0]['canonical_key'], 'IT-FVG:DGR:2024:12:PUNTO1')
            self.assertTrue(any(u['unit_type'] == 'HEADER_OCR' for u in rows['document_unit']))
            self.assertTrue(any('\\u0001' in (u['raw_text'] or '') for u in rows['document_unit']))
            self.assertEqual(sum(u['unit_type'] == 'SIGNATURE' for u in rows['document_unit']), 2)
            self.assertTrue(any(i['issue_type'] == 'OCR_COVER_REQUIRES_REVIEW' for i in rows['parser_issue']))

    def test_supplied_examples(self):
        for year,number,date,counts,points in [('2026','1507','2026-10-16',[6],2),('2024','1506','2024-10-11',[2,5,16],3)]:
            with self.subTest(year=year):
                rows = validate(etree.parse(str(ROOT/'examples'/f'DGR-{year}-{number}.xml')).getroot())
                doc=rows['source_document'][0]
                self.assertEqual((doc['document_type'],doc['number'],doc['document_date']),('DGR',number,date))
                self.assertEqual(len(doc['metadata']['attendees']),11)
                self.assertEqual(doc['metadata']['secretary'],'Gianni CORTIULA')
                self.assertEqual([a['page_count'] for a in doc['metadata']['attachments']],counts)
                self.assertEqual(len(rows['provision_identity']),points)
                self.assertFalse(rows['provision_version'])
                self.assertEqual(sum(u['source_locator'].get('role') == 'attachment_content' for u in rows['document_unit']),sum(counts))
                self.assertNotIn('/workspace/',etree.tostring(etree.parse(str(ROOT/'examples'/f'DGR-{year}-{number}.xml'))).decode())

if __name__ == '__main__':
    unittest.main()
