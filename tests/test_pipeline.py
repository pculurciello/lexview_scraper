import argparse
import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lxml import etree, html

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from citation_index import incoming
from legal_citations import destinations
from lexview_pipeline import discover_year, run


class PipelineTests(unittest.TestCase):
    def test_citation_targets(self):
        results = destinations('articoli 1 e 2 della legge regionale 26/2015',
                               'xmllex.aspx?anno=2015&legge=26#art1', 'https://lexview-int.regione.fvg.it/FontiNormative/xml/x')
        self.assertEqual([d['articolo'] for d, _ in results], ['1', '2'])
        self.assertTrue(all(d['atto'] == 'fvg:lr:2015:26' for d, _ in results))
        self.assertNotIn('uri', results[1][0])
        national = destinations('articolo 42', 'LeggiEsterne.aspx?doc=urn:nir:stato:decreto.legislativo:2011-06-23%3b118~art42')
        self.assertEqual(national[0][0]['atto'], 'urn:nir:stato:decreto.legislativo:2011-06-23;118')
        self.assertEqual(national[0][0]['articolo'], '42')
        ambiguous = destinations('articoli da 1 a 5', 'xmllex.aspx?anno=2015&legge=26#art1')
        self.assertEqual(ambiguous[0][1], 'parziale')

    def test_discovery_selects_all_and_detects_missing_rows(self):
        class FakeSession:
            url = 'https://lexview-int.regione.fvg.it/FontiNormative/xml/ricerca.aspx'
            calls = 0
            expected = 2

            def request(self, url, data=None):
                self.calls += 1
                if self.calls == 1:
                    self.doc = html.fromstring('''<form id="myform" action="ricerca.aspx">
                    <input id="PageBody_tbAnno" name="year"><input id="PageBody_btnRicerca" name="search" value="Cerca">
                    <input type="checkbox" id="PageBody_cbAbrogate" name="exclude" checked></form>''')
                elif self.calls == 2:
                    assert 'exclude' not in data
                    self.doc = html.fromstring(f'''<span id="PageBody_nDoc">{self.expected}</span>
                    <select name="selectoroscopo"><option value="lista.aspx?pg=0">tutti</option></select>''')
                else:
                    assert url.endswith('lista.aspx?pg=0')
                    self.doc = html.fromstring('''<table id="PageBody_gvListaRegolamenti"><tr><td>
                    <a href="IndiceLex.aspx?anno=1964&amp;legge=1">One</a>
                    <a href="IndiceLex.aspx?anno=1964&amp;legge=2">Two</a></td></tr></table>''')
        self.assertEqual(len(discover_year(FakeSession(), 1964)), 2)
        broken = FakeSession()
        broken.expected = 3
        with self.assertRaises(ValueError):
            discover_year(broken, 1964)

    def test_offline_conversion_resume_and_incoming_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            source = out / 'html/LR_2001_0003'
            source.mkdir(parents=True)
            raw = (ROOT / 'examples/scarico.htm').read_bytes()
            (source / '001.html').write_bytes(raw)
            manifest = {'complete': True, 'versions': [{'filename': '001.html', 'status': 'downloaded',
                'sha256': hashlib.sha256(raw).hexdigest(), 'label': 'TESTO VIGENTE',
                'url': 'https://lexview-int.regione.fvg.it/FontiNormative/xml/scarico.aspx?ANN=2001&LEX=3'}]}
            (source / 'manifest.json').write_text(json.dumps(manifest))
            (out / 'catalog.json').write_text(json.dumps({'years': {'2001': {'status': 'discovered',
                'laws': [{'year': 2001, 'number': 3, 'url': 'unused offline'}]}}}))
            args = argparse.Namespace(output=out, xsd=ROOT / 'schemas/fvg-legal-light-1.0.xsd',
                offline=True, refresh_catalog=False, start_year=2001, end_year=2001,
                attempts=1, delay=0, timeout=10, limit=None)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(run(args), 0)
                xml = next((out / 'xml').glob('*/*.xml'))
                before = xml.stat().st_mtime_ns
                checkpoint = json.loads((out / 'state.json').read_text())
                checkpoint['versions']['LR_2001_0003/obsolete.html'] = dict(next(iter(checkpoint['versions'].values())))
                (out / 'state.json').write_text(json.dumps(checkpoint))
                with patch('lexview_pipeline.convert', side_effect=AssertionError('Should reuse validated XML')):
                    self.assertEqual(run(args), 0)
                self.assertEqual(len(json.loads((out / 'state.json').read_text())['versions']), 1)
                self.assertEqual(xml.stat().st_mtime_ns, before)
                refs = incoming(out / 'citations.sqlite', 'fvg:lr:2002:12')
                self.assertTrue(refs)
                self.assertEqual(refs[0]['source_law'], 'fvg:lr:2001:3')
                # Tampering must fail rather than serve a stale cached XML.
                (source / '001.html').write_bytes(b'corrupt')
                self.assertEqual(run(args), 1)
            state = json.loads((out / 'state.json').read_text())
            self.assertEqual(next(iter(state['versions'].values()))['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
