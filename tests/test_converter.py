import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
NS = {'f': 'urn:fvg:legal:light:1.0'}


class ConverterTests(unittest.TestCase):
    def test_unnumbered_early_law_paragraph_preserves_bold_prose(self):
        doc = self.run_converter('''<html><head><title>Legge regionale 31 agosto 1964, n. 1</title></head>
        <body><div class="intestazione"><strong>Organizzazione</strong></div>
        <div class="articolo"><strong>Art. 1</strong>
        <div class="comma"><strong>La Giunta Regionale</strong> è composta dal Presidente.</div>
        <div class="comma">Il Presidente provvede alla designazione.</div>
        </div></body></html>'''.encode('utf-8'))
        commas = doc.findall('.//f:comma', NS)
        self.assertEqual([c.get('numero') for c in commas], ['', ''])
        self.assertEqual(commas[0].findtext('f:testo', namespaces=NS), 'La Giunta Regionale è composta dal Presidente.')
        self.assertNotEqual(commas[0].get('id'), commas[1].get('id'))

    def test_supplied_law(self):
        doc = self.run_converter((ROOT / 'examples/scarico.htm').read_bytes())
        for element, count in [('articolo', 38), ('comma', 77), ('lettera', 33), ('citazione', 30)]:
            self.assertEqual(len(doc.findall('.//f:' + element, NS)), count)
        self.assertEqual(doc.getroot().get('id'), 'fvg:lr:2001:3')
        self.assertEqual(doc.findtext('.//f:data', namespaces=NS), '2001-02-12')
        self.assertIn('attività produttive', doc.findtext('.//f:titolo', namespaces=NS))
        # The four quoted commas must survive as text in their containing provision.
        sys.path.insert(0, str(ROOT))
        from html_to_fvg import decode_html, extract, norm
        from lxml import html
        source = html.fromstring(decode_html((ROOT / 'examples/scarico.htm').read_bytes())[0])
        nested = source.xpath('//*[@class="comma"][ancestor::*[@class="comma"]]')
        self.assertEqual(len(nested), 4)
        texts = [norm(t) for t in doc.xpath('//f:testo/text()', namespaces=NS)]
        for comma in nested:
            quoted, _ = extract(comma)
            self.assertTrue(any(quoted in text for text in texts), quoted)

    def run_converter(self, html, *extra, success=True):
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp) / 'input.htm', Path(tmp) / 'output.xml'
            source.write_bytes(html)
            result = subprocess.run([sys.executable, str(ROOT / 'html_to_fvg.py'),
                                     str(source), str(output), *extra], capture_output=True, text=True)
            if not success:
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(output.exists())
                return
            self.assertEqual(result.returncode, 0, result.stderr)
            return etree.parse(str(output))

    def test_law_encoding_nested_lists_notes_and_citations(self):
        doc = self.run_converter('''<html><head><meta charset="utf-8"><title>Legge regionale 12 febbraio 2001, n. 3 - TESTO VIGENTE dal 07/03/2023</title></head>
        <body><div class="intestazione"><strong>Attività produttive</strong></div>
        <div class="articolo"><strong>Art. 12 bis</strong><div class="rubrica">Finalità</div>
        <div class="comma"><strong>1 ante.</strong>Si applica la
        <a href="xmllex.aspx?anno=2020&amp;legge=0004#art2-com1-let_a">legge regionale 4/2020</a>.
        <div class="el"><strong>a)</strong>Primo criterio<div class="en"><strong>1)</strong>Dettaglio</div></div>
        <div class="decorazione">(99)</div></div><div class="ndr">Nota da escludere</div></div>
        <div class="articolo"><strong>Art. 13</strong><div>( ABROGATO )</div></div></body></html>'''.encode('cp1252'))
        self.assertEqual(doc.findtext('.//f:data', namespaces=NS), '2001-02-12')
        self.assertEqual(doc.findtext('.//f:titolo', namespaces=NS), 'Attività produttive')
        self.assertEqual(doc.find('.//f:comma', NS).get('numero'), '1 ante')
        self.assertIn('1) Dettaglio', doc.findtext('.//f:lettera/f:testo', namespaces=NS))
        self.assertNotIn('Primo criterio', doc.findtext('.//f:comma/f:testo', namespaces=NS))
        self.assertNotIn('Nota da escludere', etree.tostring(doc, encoding='unicode'))
        self.assertIn('ABROGATO', doc.findall('.//f:rubrica', NS)[1].text)
        dest = doc.find('.//f:destinazione', NS)
        self.assertEqual(dest.get('atto'), 'fvg:lr:2020:4')
        self.assertEqual(dest.get('lettera'), 'a')

    def test_administrative_structure(self):
        doc = self.run_converter(b'''<html><head><title>Deliberazione della Giunta regionale 10 febbraio 2025, n. 100</title></head>
        <body><div class="intestazione"><strong>Criteri</strong></div>
        <div class="premesse">Vista la normativa.</div><div class="dispositivo">
        <p>1. Di approvare i criteri.</p><p>2. Di dare attuazione.</p></div></body></html>''')
        self.assertEqual(len(doc.findall('.//f:punto', NS)), 2)
        self.assertEqual(doc.findtext('.//f:punto/f:testo', namespaces=NS), 'Di approvare i criteri.')
        self.assertEqual(doc.findtext('.//f:premesse', namespaces=NS), 'Vista la normativa.')

    def test_missing_metadata_and_structure_fail_without_output(self):
        self.run_converter(b'<html><body>Unknown document</body></html>', success=False)
        self.run_converter(b'<html><body>No articles</body></html>', '--tipo', 'leggeRegionale',
                           '--data', '2001-02-12', '--titolo', 'Example', success=False)


if __name__ == '__main__':
    unittest.main()
