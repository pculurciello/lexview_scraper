import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch
from lxml import etree
from ingestion_model import validate
from pdf_decree_to_xml import cover_metadata, convert

ROOT = Path(__file__).parent
class DecreeTests(unittest.TestCase):
    def test_supplied_examples(self):
        cases = [('2013-266', '266', '2013-12-27', 'DEBORA SERRACCHIANI', 3, 14),
                 ('2026-13', '13', '2026-02-02', 'MASSIMILIANO FEDRIGA', 2, 8)]
        for filename, number, issued, signer, pages, recitals in cases:
            with self.subTest(filename=filename):
                rows = validate(etree.parse(str(ROOT / 'examples' / ('DPREG-' + filename + '.xml'))).getroot())
                doc = rows['source_document'][0]
                self.assertEqual((doc['number'], doc['document_date']), (number, issued))
                self.assertEqual(doc['metadata']['signatories'][0]['name'], signer)
                self.assertEqual(len(doc['metadata']['countersigners']), 2)
                self.assertEqual(rows['source_representation'][0]['raw_metadata']['page_count'], pages)
                self.assertEqual(sum(u['unit_type'] == 'RECITAL' for u in rows['document_unit']), recitals)
                self.assertEqual([u['unit_label'] for u in rows['document_unit'] if u['unit_type'] == 'OPERATIVE_POINT'], ['1', '2'])
                self.assertFalse(rows['provision_version'])
                self.assertEqual(len(rows['provision_identity']), 2)
                if number == '266':
                    self.assertTrue(any('integrazioni;' in u['raw_text'] and len({x['page'] for x in u['source_locator'].get('pdf_lines', [])}) == 2 for u in rows['document_unit']))
    def test_missing_identity_is_rejected(self):
        with self.assertRaises(ValueError):
            cover_metadata('A reference to decreto 36/2023 is not the decree identity')
    def test_missing_body_does_not_publish_xml(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp)/'a.pdf', Path(tmp)/'a.xml'
            source.write_bytes(b'fixture')
            cover = 'Decreto n° 1 / Pres.\nTrieste, 1 gennaio 2026\noggetto:\nExample\nFirmato da:\nNAME in data 01/01/2026'
            with patch('pdf_decree_to_xml.extract_pages', return_value=[cover, 'Visto il testo;']), patch('pdf_decree_to_xml.pdf_info', return_value={}):
                with self.assertRaises(ValueError):
                    convert(source, target)
            self.assertFalse(target.exists())
if __name__ == '__main__':
    unittest.main()
