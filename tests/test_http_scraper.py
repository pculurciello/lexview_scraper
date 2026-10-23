import sys
import unittest
from pathlib import Path

from lxml import html

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lexview_http import fields, temporal_links, validate_export


class HttpAdapterTests(unittest.TestCase):
    def test_form_successful_fields(self):
        doc = html.fromstring('''<form id="myform"><input type="hidden" name="__VIEWSTATE" value="state">
        <input name="anno" value="2024"><input type="checkbox" name="exclude">
        <input type="checkbox" name="enabled" checked value="yes">
        <input type="submit" name="button" value="Cerca"><input disabled name="disabled">
        <select name="display"><option value="0">Solo testo</option><option selected value="1">Testo annotato</option></select></form>''')
        self.assertEqual(fields(doc), {'__VIEWSTATE': 'state', 'anno': '2024', 'enabled': 'yes', 'display': '1'})

    def test_live_style_temporal_links(self):
        doc = html.fromstring('''<div id="PageBody_PanelMultivigenza">
        <a href="javascript:__doPostBack('ctl00$PageBody$lvMultivigenza$ctrl0$lbMultivigenza','')">dal 08/08/2026</a>
        <a href="javascript:__doPostBack('ctl00$PageBody$lvMultivigenza$ctrl1$lbMultivigenza','')">dal 11/06/2026 al 07/08/2026</a></div>''')
        result = temporal_links(doc)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[1]['date'], '2026-06-11')
        with self.assertRaises(ValueError):
            temporal_links(html.fromstring('<html>No versions</html>'))

    def test_export_encoding_annotations_and_wrong_version(self):
        raw = '''<html><meta charset="utf-8"><div class="intestazione">TESTO VIGENTE dal 08/08/2026 - Attività</div>
        <div class="NIR"><div class="articolo"><div class="ndr">Nota</div>Testo</div></div></html>'''.encode('cp1252')
        info = validate_export(raw, {'date': '2026-08-08', 'label': 'dal 08/08/2026'})
        self.assertEqual(info['encoding'], 'cp1252')
        self.assertEqual(info['annotations'], 1)
        with self.assertRaises(ValueError):
            validate_export(raw, {'date': '2026-06-11', 'label': 'old'})
        with self.assertRaises(ValueError):
            validate_export(raw, {'date': None, 'label': 'TESTO STORICO'})
        historical = raw.replace(b'dal 08/08/2026', b'dal 03/03/2001 al 23/06/2004')
        info = validate_export(historical, {'date': None, 'label': 'TESTO STORICO'})
        self.assertEqual(info['validity_dates'], ['2001-03-03', '2004-06-23'])
        with self.assertRaises(ValueError):
            validate_export(historical, {'date': '2001-03-03', 'label': 'dal 03/03/2001 al 24/06/2004'})


if __name__ == '__main__':
    unittest.main()
