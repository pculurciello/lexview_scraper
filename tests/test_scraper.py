import argparse
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lexview_html_versions import (annotated_option, date_key, document_html,
                                  find_version_select, law_identity, scrape, version_options)


class ParsingTests(unittest.TestCase):
    def test_version_detection_and_ambiguity(self):
        opts = [{'value': '', 'label': 'Seleziona'},
                {'value': 'old', 'label': 'dal 01/01/2001 al 02/03/2023'},
                {'value': 'new', 'label': 'Vigente dal 03/03/2023'},
                {'value': 'new', 'label': 'Vigente dal 03/03/2023'}]
        self.assertEqual(len(version_options(opts)), 2)
        control = {'id': 'versions', 'selector': '#versions', 'options': opts}
        self.assertEqual(find_version_select([control]), control)
        with self.assertRaises(ValueError):
            find_version_select([control, {**control, 'id': 'other'}])
        with self.assertRaises(ValueError):
            find_version_select([])
        self.assertEqual(date_key('dal 7/3/2023'), '2023-03-07')

    def test_annotations_identity_and_document_guard(self):
        options = [{'value': '0', 'label': 'Solo testo'},
                   {'value': '1', 'label': 'Testo con annotazioni'}]
        self.assertEqual(annotated_option(options)['value'], '1')
        with self.assertRaises(ValueError):
            annotated_option(options[:1])
        self.assertEqual(law_identity('https://example.org/XmlLex.aspx?anno=2001&legge=0003'), (2001, 3))
        raw = '<div class="intestazione">Vigente dal 07/03/2023</div><div class="NIR"><div class="articolo"><div class="ndr">Nota</div>Legal text</div></div>'
        data, _ = document_html(raw, 'https://example.org/', '2023-03-07')
        self.assertIn(b'Nota', data)
        with self.assertRaises(ValueError):
            document_html(raw, 'https://example.org/', '2001-01-01')
        with self.assertRaises(ValueError):
            document_html('<html><body>Error page</body></html>', 'https://example.org/')


class BrowserFlowTests(unittest.TestCase):
    def test_all_versions_through_browser_postbacks(self):
        """Exercise real Chromium with intercepted fixture responses, not the live site."""
        from playwright.async_api import Browser
        original = Browser.new_context

        async def context_with_fixtures(browser, *a, **kw):
            context = await original(browser, *a, **kw)

            async def serve(route):
                u = urlsplit(route.request.url)
                q = parse_qs(u.query)
                if 'ricerca.aspx' in u.path:
                    body = '''<form action="results.aspx"><input name="Anno" id="PageBody_tbAnno">
                    <input type="checkbox" id="PageBody_lbAbrogati" checked>
                    <input type="submit" value="Cerca"></form>'''
                elif 'results.aspx' in u.path:
                    body = '<a href="IndiceLex.aspx?anno=2001&amp;legge=0003">Legge regionale 3/2001</a>'
                elif 'IndiceLex.aspx' in u.path:
                    body = '<a title="Visualizza legge intera" href="XmlLex.aspx?anno=2001&amp;legge=0003">Legge intera</a>'
                elif 'XmlLex.aspx' in u.path:
                    version = q.get('v', ['new'])[0]
                    display = q.get('display', ['plain'])[0]
                    dates = {'old': '01/01/2001', 'new': '07/03/2023'}
                    version_opts = ''.join(f'<option value="{v}" {"selected" if v == version else ""}>Vigente dal {d}</option>' for v, d in dates.items())
                    display_opts = ''.join(f'<option value="{v}" {"selected" if v == display else ""}>{label}</option>' for v, label in [('plain', 'Solo testo'), ('annotated', 'Testo con annotazioni')])
                    body = f'''<script>function change(key,value) {{ let u=new URL(location.href);u.searchParams.set(key,value);location.href=u; }}</script>
                    <select id="versions" onchange="change('v',this.value)">{version_opts}</select>
                    <select id="PageBody_ddlVisualizza" onchange="change('display',this.value)">{display_opts}</select>
                    <div class="intestazione">Legge regionale 1 gennaio 2001, n. 3 - TESTO VIGENTE dal {dates[version]}</div>
                    <div class="NIR"><div class="articolo"><strong>Art. 1</strong><div class="comma">Version {version}</div>
                    {'<div class="ndr">Nota di modifica</div>' if display == 'annotated' else ''}</div></div>'''
                else:
                    await route.abort()
                    return
                await route.fulfill(status=200, content_type='text/html; charset=utf-8', body=body)

            await context.route('**/*', serve)
            return context

        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(year=2001, number=3, output=Path(tmp), law_url=None,
                                      delay=0, timeout=10, headed=False,
                                      browser_executable=os.environ.get('TEST_CHROMIUM'),
                                      version_selector=None, annotation_label=None,
                                      display_selector='#PageBody_ddlVisualizza')
            with patch.object(Browser, 'new_context', context_with_fixtures):
                asyncio.run(scrape(args))
            directory = Path(tmp) / 'LR_2001_0003'
            manifest = json.loads((directory / 'manifest.json').read_text())
            self.assertTrue(manifest['complete'])
            self.assertEqual(len(manifest['versions']), 2)
            for entry in manifest['versions']:
                content = (directory / entry['filename']).read_text()
                self.assertIn('Nota di modifica', content)
                self.assertIn('Version ' + entry['value'], content)


if __name__ == '__main__':
    unittest.main()
