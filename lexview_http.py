"""Live Lexview adapter: cookie session, ASP.NET postbacks, original HTML exports."""
import hashlib
import http.cookiejar
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from lxml import html

from html_to_fvg import decode_html
from lexview_html_versions import BASE, SEARCH, DATE_RE, date_key, law_identity, normalize


def fields(doc):
    forms = doc.xpath('//form[@id="myform"]')
    if len(forms) != 1:
        raise ValueError('Expected the Lexview ASP.NET form #myform.')
    result = {}
    for e in forms[0].xpath('.//input[@name][not(@disabled)]'):
        kind = e.get('type', 'text').lower()
        if kind in ('submit', 'button', 'reset', 'image', 'file'):
            continue
        if kind in ('checkbox', 'radio') and e.get('checked') is None:
            continue
        result[e.get('name')] = e.get('value', '')
    for e in forms[0].xpath('.//select[@name]'):
        options = e.xpath('./option[@selected]') or e.xpath('./option[1]')
        if options:
            result[e.get('name')] = options[0].get('value', '')
    return result


def temporal_links(doc):
    links = doc.xpath('//*[@id="PageBody_PanelMultivigenza"]//a[@href]')
    result = []
    for link in links:
        match = re.fullmatch(r"javascript:__doPostBack\('([^']*)','([^']*)'\)", link.get('href'))
        label = normalize(link.text_content())
        if not match or not (DATE_RE.search(label) or re.search(r'TESTO (VIGENTE|STORICO)', label)):
            raise ValueError(f'Unrecognized temporal link: {label!r}')
        result.append({'label': label, 'value': match[1], 'argument': match[2],
                       'date': date_key(label)})
    if not result:
        raise ValueError('No temporal links found; refusing a latest-only download.')
    if len({e['value'] for e in result}) != len(result):
        raise ValueError('Duplicate temporal postback targets.')
    return result


class Session:
    def __init__(self, delay, timeout):
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.delay, self.timeout = delay, timeout
        self.last = 0.0
        self.url, self.raw, self.doc = None, None, None

    def request(self, url, data=None):
        if urllib.parse.urlsplit(url).hostname != urllib.parse.urlsplit(BASE).hostname:
            raise ValueError('Refusing a request outside the Lexview host.')
        time.sleep(max(0, self.delay - (time.monotonic() - self.last)))
        request = urllib.request.Request(url,
            data=urllib.parse.urlencode(data).encode() if data is not None else None,
            headers={'User-Agent': 'FVG-Legal-HTML-Archive/1.0', 'Referer': self.url or SEARCH})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                self.raw = response.read()
                self.url = response.url
        finally:
            self.last = time.monotonic()
        text, _ = decode_html(self.raw)
        self.doc = html.fromstring(text)
        return self.doc

    def postback(self, target, argument='', updates=None):
        data = fields(self.doc)
        data.update(updates or {})
        data.update(__EVENTTARGET=target, __EVENTARGUMENT=argument)
        action = self.doc.xpath('//form[@id="myform"]/@action')[0]
        return self.request(urllib.parse.urljoin(self.url, action), data)

    def find_law(self, args):
        if args.law_url:
            if law_identity(args.law_url) != (args.year, args.number):
                raise ValueError('--law-url identifies another law.')
            self.request(args.law_url)
        else:
            self.request(SEARCH)
            data = fields(self.doc)
            for identifier, value in [('PageBody_tbAnno', str(args.year)), ('PageBody_legge', str(args.number))]:
                elements = self.doc.xpath('//*[@id=$id]', id=identifier)
                if len(elements) != 1:
                    raise ValueError(f'Missing search field {identifier}.')
                data[elements[0].get('name')] = value
            button = self.doc.xpath('//*[@id="PageBody_btnRicerca"]')[0]
            data[button.get('name')] = button.get('value')
            action = self.doc.xpath('//form[@id="myform"]/@action')[0]
            self.request(urllib.parse.urljoin(self.url, action), data)
            matches = list(dict.fromkeys(urllib.parse.urljoin(self.url, e.get('href'))
                for e in self.doc.xpath('//a[@href]')
                if law_identity(e.get('href')) == (args.year, args.number)
                and re.search(r'(indicelex|xmllex)\.aspx', e.get('href'), re.I)))
            if not matches:
                raise ValueError('Requested law not found in search results.')
            matches.sort(key=lambda u: ('indicelex' not in u.lower(), len(u)))
            self.request(matches[0])
        whole = self.doc.xpath('//a[contains(translate(@title,"INTERA","intera"),"legge intera")]/@href')
        if whole:
            self.request(urllib.parse.urljoin(self.url, whole[0]))
        if law_identity(self.url) != (args.year, args.number):
            raise ValueError('Whole-law URL does not identify the requested law.')
        return self.url


def validate_export(raw, version):
    text, encoding = decode_html(raw)
    doc = html.fromstring(text)
    header = doc.xpath('//*[contains(concat(" ",normalize-space(@class)," ")," intestazione ")]')
    laws = doc.xpath('//*[contains(concat(" ",normalize-space(@class)," ")," NIR ")]')
    if len(header) != 1 or len(laws) != 1 or not laws[0].xpath('.//*[@class="articolo"]'):
        raise ValueError('HTML export is not a complete law.')
    heading = normalize(' '.join(header[0].itertext()))
    if version['date']:
        dates = [datetime(int(y), int(m), int(d)).date().isoformat() for d, m, y in DATE_RE.findall(heading)]
        expected = [datetime(int(y), int(m), int(d)).date().isoformat()
                    for d, m, y in DATE_RE.findall(version['label'])] or [version['date']]
        if not dates or dates[:len(expected)] != expected:
            raise ValueError(f'Wrong version heading: {heading}')
    elif version['label'] == 'TESTO STORICO':
        # Lexview labels the selector STORICO but exports "VIGENTE dal ... al ...".
        if not re.search(r'dal\s+\d+[/.]\d+[/.]\d+\s+al\s+\d+[/.]\d+[/.]\d+', heading):
            raise ValueError(f'Historical export lacks its validity interval: {heading}')
    elif version['label'] not in heading:
        raise ValueError(f'Wrong current/historical heading: {heading}')
    return {'heading': heading, 'encoding': encoding,
            'articles': len(laws[0].xpath('.//*[@class="articolo"]')),
            'annotations': len(laws[0].xpath('.//*[@class="ndr" or @class="ndr_titolo"]')),
            'validity_dates': [datetime(int(y), int(m), int(d)).date().isoformat()
                               for d, m, y in DATE_RE.findall(heading)]}


def scrape_http(args, session=None):
    directory = args.output / f'LR_{args.year}_{args.number:04d}'
    directory.mkdir(parents=True, exist_ok=True)
    previous = {}
    path = directory / 'manifest.json'
    if getattr(args, 'resume', False) and path.exists():
        previous = json.loads(path.read_text(encoding='utf-8'))
    manifest = {'year': args.year, 'number': args.number, 'backend': 'http', 'complete': False,
                'started_at': datetime.now(timezone.utc).isoformat(), 'versions': []}
    path = directory / 'manifest.json'
    session = session or Session(args.delay, args.timeout)

    def save():
        temporary = path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)

    try:
        whole = session.find_law(args)
        versions = temporal_links(session.doc)
        manifest.update(law_url=whole, listed_versions=versions,
            coverage='All temporal entries exposed by Lexview for this law; no unlisted intermediate versions inferred.')
        save()
        print(f'Found {len(versions)} listed temporal versions.', flush=True)
        for index, version in enumerate(versions, 1):
            entry = {**version, 'status': 'pending'}
            manifest['versions'].append(entry)
            try:
                cached = next((e for e in previous.get('versions', [])
                               if e.get('label') == version['label'] and e.get('value') == version['value']
                               and e.get('status') == 'downloaded'), None)
                if cached and (directory / cached['filename']).is_file():
                    raw = (directory / cached['filename']).read_bytes()
                    if hashlib.sha256(raw).hexdigest() == cached.get('sha256'):
                        info = validate_export(raw, version)
                        entry.update(cached, **info)
                        print(f'[{index}/{len(versions)}] Reusing verified {cached["filename"]}', flush=True)
                        continue
                session.request(whole)
                available = temporal_links(session.doc)
                if version not in available:
                    raise ValueError('Temporal list changed during download; restart the run.')
                session.postback(version['value'], version['argument'])
                if not version['date']:
                    status = session.doc.xpath('//*[@id="PageBody_lbTesto"]/text()')
                    if not status or normalize(status[0]) != version['label']:
                        raise ValueError('Temporal postback did not select the requested status.')
                display = session.doc.xpath('//select[@id="PageBody_ddlVisualizza"]')
                if len(display) != 1:
                    raise ValueError('Missing display control.')
                selected = display[0].xpath('./option[@selected]') or display[0].xpath('./option[1]')
                annotated = display[0].xpath('./option[contains(.,"annotato")]')
                if len(annotated) != 1:
                    raise ValueError('Missing Testo annotato option.')
                if selected[0].get('value') != annotated[0].get('value'):
                    session.postback(display[0].get('name'), updates={display[0].get('name'): annotated[0].get('value')})
                links = session.doc.xpath('//a[@id="PageBody_aHtml"]/@href')
                if len(links) != 1:
                    raise ValueError('Missing HTML export link.')
                export_url = urllib.parse.urljoin(session.url, links[0])
                session.request(export_url)
                info = validate_export(session.raw, version)
                filename = f'{index:03d}_{version["date"] or ("storico" if "STORICO" in version["label"] else "vigente")}.html'
                temp = directory / (filename + '.tmp')
                temp.write_bytes(session.raw)
                temp.replace(directory / filename)
                entry.update(info, status='downloaded', filename=filename, url=export_url,
                             retrieved_at=datetime.now(timezone.utc).isoformat(),
                             annotation_label='Testo annotato', sha256=hashlib.sha256(session.raw).hexdigest())
                print(f'[{index}/{len(versions)}] {version["label"]} -> {filename} ({info["annotations"]} notes)', flush=True)
            except Exception as exc:
                entry.update(status='failed', error=str(exc))
                if session.raw:
                    (directory / f'failure_{index:03d}.html').write_bytes(session.raw)
                print(f'Failed {version["label"]}: {exc}', flush=True)
            finally:
                save()
        manifest['complete'] = all(v['status'] == 'downloaded' for v in manifest['versions'])
        if not manifest['complete']:
            raise RuntimeError(f'Some exports failed; see {path}')
    except Exception as exc:
        manifest['error'] = str(exc)
        raise
    finally:
        save()
