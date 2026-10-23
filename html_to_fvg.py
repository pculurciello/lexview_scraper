#!/usr/bin/env python3
"""Convert FVG legislative HTML to FVG Legal Light XML; validate before writing."""
from __future__ import annotations

import argparse
import codecs
import hashlib
import re
import sys
from datetime import date
from pathlib import Path

from lxml import etree, html
from legal_citations import destinations

NS = 'urn:fvg:legal:light:1.0'
MONTHS = dict(zip(('gennaio febbraio marzo aprile maggio giugno luglio agosto '
                   'settembre ottobre novembre dicembre').split(), range(1, 13)))
TYPES = {'leggeRegionale': ('Legge regionale', 'lr', 'Consiglio regionale'),
         'deliberazioneGiunta': ('Deliberazione della Giunta regionale', 'dgr', 'Giunta regionale'),
         'regolamento': ('Regolamento', 'reg', None),
         'decretoPresidente': ('Decreto del Presidente', 'dpreg', None),
         'decretoDirettore': ('Decreto del Direttore', 'dd', None),
         'decretoAltro': ('Decreto', 'decreto', None),
         'altro': ('', 'atto', None)}
IGNORED = {'decorazione', 'ndr', 'note', 'ndr_titolo', 'note_titolo'}


def norm(text):
    return re.sub(r'\s+', ' ', text).strip()


def classes(node):
    return set(node.get('class', '').split())


def select(node, name):
    return node.xpath('.//*[contains(concat(" ", normalize-space(@class), " "), $c)]',
                      c=f' {name} ')


def child(parent, name, text=None, **attrs):
    e = etree.SubElement(parent, f'{{{NS}}}{name}', **attrs)
    e.text = text
    return e


def decode_html(raw, encoding=None):
    if encoding:
        return raw.decode(encoding), encoding
    if raw.startswith(codecs.BOM_UTF8):
        return raw.decode('utf-8-sig'), 'utf-8-sig'
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return raw.decode('utf-16'), 'utf-16'
    # Prefer a valid UTF-8 stream; this export incorrectly declares UTF-8.
    try:
        return raw.decode('utf-8'), 'utf-8'
    except UnicodeDecodeError:
        declared = re.search(br'charset\s*=\s*["\']?([\w-]+)', raw[:4096], re.I)
        enc = declared.group(1).decode('ascii') if declared else 'cp1252'
        if enc.lower().replace('-', '') in ('utf8', 'utf16'):
            enc = 'cp1252'
        return raw.decode(enc), enc


def extract(node, skip=(), omit_label=False):
    """Flatten inline markup, retaining nested list text and link labels."""
    chunks, links = [], []
    first_label = next(iter(node.xpath('./strong[1] | ./b[1]')), None) if omit_label else None

    def walk(e):
        if (e is first_label or classes(e) & (IGNORED | set(skip))
                or e.tag in ('script', 'style')):
            return
        if e.tag in ('br', 'div', 'p', 'li', 'tr'):
            chunks.append(' ')
        if e.text:
            chunks.append(e.text)
        for sub in e:
            walk(sub)
            if sub.tail:
                chunks.append(sub.tail)
        if e.tag == 'a' and e.get('href') and norm(e.text_content()):
            links.append((norm(e.text_content()), e.get('href')))
        if e.tag in ('strong', 'b') and re.fullmatch(r'\d+[.)]', norm(e.text_content())):
            chunks.append(' ')
        if e.tag in ('div', 'p', 'li', 'td', 'tr'):
            chunks.append(' ')
    walk(node)
    return norm(''.join(chunks)), links


def label(node, pattern):
    labels = node.xpath('./strong[1] | ./b[1]')
    s = norm(labels[0].text_content()) if labels else norm(node.text or '')
    m = re.match(pattern, s, re.I)
    if not m:
        raise ValueError(f'Cannot identify numbering: {s[:100]!r}')
    return norm(m.group(1))


def token(s):
    return re.sub(r'[^A-Za-z0-9_.-]', '_', s)


class Converter:
    def __init__(self, source_url=None):
        self.source_url = source_url
        self.citations = []
        self.ids = set()

    def unique(self, value):
        if value in self.ids:
            raise ValueError(f'Duplicate structural ID: {value}')
        self.ids.add(value)
        return value

    def text(self, parent, node, source_id, skip=(), omit_label=False, tag='testo'):
        text, links = extract(node, skip, omit_label)
        child(parent, tag, text)
        for quoted, href in links:
            if href.startswith('#') and re.search(r'-not\d+', href):
                continue
            for dest, status in destinations(quoted, href, self.source_url):
                self.citations.append((source_id, quoted, dest, status))
        return text

    def law(self, content, doc):
        articles = [e for e in select(doc, 'articolo')
                    if not any('articolo' in classes(a) for a in e.iterancestors())]
        if not articles:
            raise ValueError('No .articolo sections found; refusing to invent an articulated law.')
        body = child(content, 'articolato')
        preambles = select(doc, 'formulainiziale')
        if preambles and extract(preambles[0])[0]:
            self.text(body, preambles[0], 'preambolo1', tag='preambolo')
            body[-1].set('id', self.unique('preambolo1'))
        for source in articles:
            number = label(source, r'Art(?:icolo|\.)?\s*(\d+(?:\s*[a-z]+)?|unico)\s*$')
            aid = self.unique('art' + token(number))
            article = child(body, 'articolo', id=aid, numero=number)
            rubrics = [e for e in source if 'rubrica' in classes(e)]
            if rubrics:
                child(article, 'rubrica', extract(rubrics[0])[0])
            commas = [e for e in source if 'comma' in classes(e)]
            if not commas:
                # Light has no article-level text/status; retain the repeal marker in rubrica.
                remaining = extract(source, skip={'rubrica'}, omit_label=True)[0]
                if remaining:
                    if rubrics:
                        article[0].text = (article[0].text or '') + ' ' + remaining
                    else:
                        child(article, 'rubrica', remaining)
                continue
            for position, cs in enumerate(commas, 1):
                try:
                    cn = label(cs, r'(\d+(?:\s*[a-z]+)?)\s*\.')
                    numbered = True
                except ValueError:
                    # Early laws have unnumbered paragraphs. Empty xs:string is valid;
                    # do not invent a printed legal paragraph number or discard prose.
                    cn, numbered = '', False
                cid = self.unique(aid + '_com' + (token(cn) if numbered else f'_unnumbered{position}'))
                comma = child(article, 'comma', id=cid, numero=cn)
                self.text(comma, cs, cid, skip={'el'}, omit_label=numbered)
                for ls in [e for e in cs if 'el' in classes(e)]:
                    ln = label(ls, r'([a-z]+(?:\s*-?\s*(?:bis|ter|quater))?)\s*\)')
                    lid = self.unique(cid + '_let' + token(ln))
                    letter = child(comma, 'lettera', id=lid, numero=ln)
                    self.text(letter, ls, lid, omit_label=True)

    def administrative(self, content, doc, pre_xpath=None, device_xpath=None):
        pre = doc.xpath(pre_xpath) if pre_xpath else select(doc, 'premesse')
        devices = doc.xpath(device_xpath) if device_xpath else select(doc, 'dispositivo')
        if len(devices) != 1 or not isinstance(devices[0], etree._Element):
            raise ValueError('Expected one .dispositivo; use --dispositivo-xpath for other layouts.')
        if len(pre) > 1 or any(not isinstance(e, etree._Element) for e in pre):
            raise ValueError('Premesse selector must return at most one element.')
        body = child(content, 'amministrativo')
        if pre:
            self.text(body, pre[0], 'premesse1', tag='premesse')
            body[-1].set('id', self.unique('premesse1'))
        device = child(body, 'dispositivo')
        points = select(devices[0], 'punto')
        if not points:
            points = devices[0].xpath('./ol/li | ./p | ./li')
        if not points:
            raise ValueError('No .punto, direct p, li, or ol/li found inside dispositivo.')
        for i, ps in enumerate(points, 1):
            text = extract(ps)[0]
            m = re.match(r'^(\d+)\s*[.)]\s*', text)
            number = m.group(1) if m else str(i)
            pid = self.unique('punto' + token(number))
            point = child(device, 'punto', id=pid, numero=number)
            self.text(point, ps, pid)
            if m:
                point[0].text = text[m.end():]

    def add_citations(self, root):
        if not self.citations:
            return
        container = child(root, 'citazioni')
        for i, (source, quoted, dest, status) in enumerate(self.citations, 1):
            c = child(container, 'citazione', id=self.unique(f'cit{i}'), sorgente=source, stato=status)
            if dest:
                child(c, 'destinazione', **dest)
            child(c, 'testoCitato', quoted)


def metadata(doc, args):
    headers = select(doc, 'intestazione')
    titles = doc.xpath('//head/title')
    header = norm(headers[0].text_content()) if headers else ''
    identity = norm(titles[0].text_content()) if titles else header
    # Read only the act heading, never dates/numbers in the body or amendment history.
    identity = re.split(r'\s+-\s+TESTO|\s+-\s+Testo', identity, maxsplit=1, flags=re.I)[0]
    kind = args.tipo
    if not kind:
        if re.match(r'Legge regionale\b', identity, re.I):
            kind = 'leggeRegionale'
        elif re.match(r'Deliberazione\b', identity, re.I):
            kind = 'deliberazioneGiunta'
        else:
            raise ValueError('Cannot infer document type; supply --tipo.')
    d = args.data
    if not d:
        m = re.search(r'\b(\d{1,2})\s+(' + '|'.join(MONTHS) + r')\s+(\d{4})\b', identity, re.I)
        if m:
            d = date(int(m[3]), MONTHS[m[2].lower()], int(m[1])).isoformat()
    if not d:
        raise ValueError('Cannot infer act date; supply --data YYYY-MM-DD.')
    date.fromisoformat(d)
    number = args.numero
    if number is None:
        m = re.search(r'\bn\.\s*(\d+(?:/[A-Za-z]+)?)', identity, re.I)
        number = m[1] if m else None
    title = args.titolo
    if not title and headers:
        strong = headers[0].xpath('.//strong')
        title = norm(strong[0].text_content()) if strong else None
    if not title:
        raise ValueError('Cannot infer descriptive title; supply --titolo.')
    return kind, number, d, title


def convert(args):
    raw = args.input.read_bytes()
    decoded, encoding = decode_html(raw, args.encoding)
    doc = html.fromstring(decoded, parser=html.HTMLParser(no_network=True))
    kind, number, d, title = metadata(doc, args)
    code, organ = TYPES[kind][1:]
    identifier = args.id or f'fvg:{code}:{d[:4]}:{number or hashlib.sha256(raw).hexdigest()[:12]}'
    root = etree.Element(f'{{{NS}}}documento', nsmap={None: NS}, id=identifier, versioneSchema='1.0')
    meta = child(root, 'metadati')
    child(meta, 'tipo', kind)
    if number is not None:
        child(meta, 'numero', number)
    child(meta, 'data', d)
    child(meta, 'titolo', title)
    child(meta, 'ente', args.ente)
    if args.organo or organ:
        child(meta, 'organo', args.organo or organ)
    child(meta, 'fonte', url=args.source_url or args.input.resolve().as_uri(),
          formato='HTML', sha256=hashlib.sha256(raw).hexdigest())
    content = child(root, 'contenuto')
    converter = Converter(args.source_url)
    mode = args.mode or ('articolato' if kind in ('leggeRegionale', 'regolamento') else 'amministrativo')
    if mode == 'articolato':
        converter.law(content, doc)
    elif mode == 'amministrativo':
        converter.administrative(content, doc, args.premesse_xpath, args.dispositivo_xpath)
    else:
        bodies = doc.xpath('//body')
        converter.text(content, bodies[0] if bodies else doc, 'testo1', tag='testoLibero')
        content[-1].set('id', converter.unique('testo1'))
    converter.add_citations(root)
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    schema = etree.XMLSchema(etree.parse(str(args.xsd), parser))
    schema.assertValid(root)
    # Explicitly check references as well as XSD validation.
    ids = set(root.xpath('//@id'))
    if any(s not in ids for s in root.xpath('//@sorgente')):
        raise ValueError('Dangling citation reference.')
    if args.output.resolve() in (args.input.resolve(), args.xsd.resolve()):
        raise ValueError('Output must differ from input and schema.')
    args.output.write_bytes(etree.tostring(root, encoding='UTF-8', xml_declaration=True, pretty_print=True))
    print(f'Validated {args.output} (input encoding: {encoding}; '
          f'{len(root.findall(".//{" + NS + "}articolo"))} articles; '
          f'{len(converter.citations)} linked citations)', file=sys.stderr)
    return root


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('input', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--xsd', type=Path, default=Path(__file__).resolve().parent / 'schemas/fvg-legal-light-1.0.xsd')
    p.add_argument('--encoding', help='Override automatic UTF-8 / declared charset / Windows-1252 detection')
    p.add_argument('--tipo', choices=TYPES)
    p.add_argument('--mode', choices=('articolato', 'amministrativo', 'testoLibero'))
    for name in ('numero', 'data', 'titolo', 'organo', 'id', 'source-url', 'premesse-xpath', 'dispositivo-xpath'):
        p.add_argument('--' + name)
    p.add_argument('--ente', default='Regione autonoma Friuli Venezia Giulia')
    args = p.parse_args()
    try:
        convert(args)
    except (OSError, ValueError, LookupError, etree.LxmlError) as exc:
        p.exit(1, f'Conversion failed: {exc}\n')


if __name__ == '__main__':
    main()
