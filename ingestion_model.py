"""Database-shaped XML contract; fields use the supplied PostgreSQL column names."""
import json
import re
import uuid
from datetime import date, datetime
from pathlib import Path

from lxml import etree

NS = 'urn:fvg:legal:ingest:2.0'
XSD = Path(__file__).resolve().parent / 'schemas/fvg-legal-ingest-2.0.xsd'
# type suffix ! means required; absent fields map to SQL NULL/default, never empty UUIDs.
TABLES = {
 'source_document': 'id:uuid! jurisdiction:code50! document_type:code80! number:code80 year:year document_date:date title:string issuer:code255 publication_type:code80 publication_number:code80 publication_date:date official_uri:string metadata:json!',
 'source_representation': 'id:uuid! source_document_id:uuid! source_system:code80! format:code30! source_url:string! retrieved_at:dateTime content_hash:code128 local_object_key:string media_type:code120 character_encoding:code40 is_official:boolean! legal_authority:boolean! raw_metadata:json!',
 'provision_identity': 'id:uuid! source_document_id:uuid! parent_provision_id:uuid unit_type:code50! unit_label:code100! canonical_key:code700! ordinal:integer',
 'provision_version': 'id:uuid! provision_identity_id:uuid! valid_period:period! text:string! normalized_text:string version_number:positiveInteger status:code30! introduced_by_document_id:uuid introduced_by_provision_id:uuid metadata:json!',
 'document_unit': 'id:uuid! source_representation_id:uuid! provision_version_id:uuid parent_unit_id:uuid unit_type:code50! unit_label:code100 ordinal:integer recital_type:code50 raw_text:string normalized_text:string source_locator:json! parser_confidence:confidence review_status:code30!',
 'ingestion_run': 'id:uuid! source_representation_id:uuid! parser_name:code100! parser_version:code50! status:code30! started_at:dateTime! finished_at:dateTime statistics:json! error_detail:string',
 'parser_issue': 'id:uuid! ingestion_run_id:uuid! document_unit_id:uuid issue_type:code60! candidate_text:string! source_locator:json! parser_context:json! resolution_status:code30! resolution:json resolved_by:code100 resolved_at:dateTime',
 'citation': 'id:uuid! citing_provision_version_id:uuid citing_document_unit_id:uuid cited_text:string! parsed_reference:json! target_document_id:uuid target_provision_id:uuid resolution_status:code30! extraction_method:code40! confidence:confidence verified_by:code100 verified_at:dateTime',
 'knowledge_entity': 'id:uuid! entity_type:code60! canonical_key:code700! preferred_label:string! description:string provision_identity_id:uuid attributes:json!',
 'legal_assertion': 'id:uuid! assertion_type:code80! statement:string! subject_entity_id:uuid object_entity_id:uuid source_provision_version_id:uuid! evidence_document_unit_id:uuid extraction_method:code40! verification_status:code30! confidence:confidence model_info:json! verified_by:code100 verified_at:dateTime',
 'legal_relation': 'id:uuid! subject_entity_id:uuid! predicate:code80! object_entity_id:uuid! source_assertion_id:uuid source_provision_version_id:uuid verification_status:code30! valid_period:period',
}
CONTAINERS = {'source_document': None, 'source_representation': None, 'ingestion_run': None,
 'provision_identity': 'provision_identities', 'provision_version': 'provision_versions',
 'document_unit': 'document_units', 'parser_issue': 'parser_issues', 'citation': 'citations',
 'knowledge_entity': 'knowledge_entities', 'legal_assertion': 'legal_assertions', 'legal_relation': 'legal_relations'}
ORDER = ['source_document', 'source_representation', 'provision_identity', 'provision_version',
         'document_unit', 'ingestion_run', 'parser_issue', 'citation', 'knowledge_entity', 'legal_assertion', 'legal_relation']


def fields(table):
    for item in TABLES[table].split():
        name, kind = item.split(':')
        yield name, kind.rstrip('!'), kind.endswith('!')


def xml_from_rows(rows):
    root = etree.Element(f'{{{NS}}}legal_ingestion', nsmap={None: NS}, schema_version='2.0')
    for table in ORDER:
        container = etree.SubElement(root, f'{{{NS}}}{CONTAINERS[table]}') if CONTAINERS[table] else root
        for row in rows.get(table, []):
            e = etree.SubElement(container, f'{{{NS}}}{table}')
            for name, kind, _ in fields(table):
                if row.get(name) is None:
                    continue
                value = row[name]
                text = json.dumps(value, ensure_ascii=False, sort_keys=True) if kind == 'json' else (
                    str(value).lower() if kind == 'boolean' else str(value))
                etree.SubElement(e, f'{{{NS}}}{name}').text = text
    return root


def rows_from_xml(root):
    result = {}
    for table in ORDER:
        path = f'{{{NS}}}{CONTAINERS[table]}/{{{NS}}}{table}' if CONTAINERS[table] else f'{{{NS}}}{table}'
        result[table] = []
        kinds = {n: k for n, k, _ in fields(table)}
        for e in root.findall(path):
            row = {}
            for child in e:
                name, value = etree.QName(child).localname, child.text or ''
                kind = kinds[name]
                if kind == 'json':
                    value = json.loads(value)
                    if not isinstance(value, dict):
                        raise ValueError(f'{table}.{name} must be a JSON object')
                elif kind == 'boolean':
                    value = value in ('true', '1')
                elif kind in ('integer', 'positiveInteger', 'year'):
                    value = int(value)
                elif kind == 'confidence':
                    value = float(value)
                row[name] = value
            result[table].append(row)
    return result


def validate(root, xsd=XSD):
    etree.XMLSchema(etree.parse(str(xsd), etree.XMLParser(resolve_entities=False, no_network=True))).assertValid(root)
    rows = rows_from_xml(root)
    by_id = {t: {r['id']: r for r in entries} for t, entries in rows.items()}
    if any(len(by_id[t]) != len(rows[t]) for t in rows):
        raise ValueError('Duplicate row identity')
    for entries in rows.values():
        for row in entries:
            uuid.UUID(row['id'])
    for table, entries in rows.items():
        uuid_fields = [name for name, kind, _ in fields(table) if kind == 'uuid']
        timestamp_fields = [name for name, kind, _ in fields(table) if kind == 'dateTime']
        for row in entries:
            for column in uuid_fields:
                if column in row:
                    uuid.UUID(row[column])
            for column in timestamp_fields:
                if column in row and datetime.fromisoformat(row[column].replace('Z', '+00:00')).tzinfo is None:
                    raise ValueError('Timestamp requires an explicit timezone')
    doc = rows['source_document'][0]['id']
    rep = rows['source_representation'][0]['id']
    if rows['source_representation'][0]['source_document_id'] != doc:
        raise ValueError('Representation belongs to a different act')
    for table, parent_field, owner_field, owner in (
        ('provision_identity', 'parent_provision_id', 'source_document_id', doc),
        ('document_unit', 'parent_unit_id', 'source_representation_id', rep)):
        seen_keys = set()
        for row in rows[table]:
            if row[owner_field] != owner:
                raise ValueError('Cross-document or cross-representation owner')
            parent = row.get(parent_field)
            visited = {row['id']}
            while parent:
                if parent in visited or parent not in by_id[table]:
                    raise ValueError('Cyclic or missing parent')
                visited.add(parent)
                parent = by_id[table][parent].get(parent_field)
            key = ((row.get(parent_field), row['unit_type'], row.get('unit_label')) if table == 'provision_identity'
                   else (row.get(parent_field), row['unit_type'], row.get('unit_label'), row.get('ordinal')))
            if key in seen_keys:
                raise ValueError('Duplicate database natural unit key')
            seen_keys.add(key)
    if len({r['canonical_key'] for r in rows['provision_identity']}) != len(rows['provision_identity']):
        raise ValueError('Duplicate canonical provision key')
    periods = {}
    for row in rows['provision_version']:
        lower, upper = row['valid_period'][1:-1].split(',')
        start, end = date.fromisoformat(lower), date.fromisoformat(upper) if upper else date.max
        if start >= end:
            raise ValueError('Empty/reversed validity interval')
        ranges = periods.setdefault(row['provision_identity_id'], [])
        if any(start < b and a < end for a, b in ranges):
            raise ValueError('Overlapping provision validity intervals')
        ranges.append((start, end))
    for row in rows['citation']:
        if sum(row.get(k) is not None for k in ('citing_provision_version_id', 'citing_document_unit_id')) != 1:
            raise ValueError('Citation must have exactly one citing origin')
        if row.get('target_provision_id') and not row.get('target_document_id'):
            raise ValueError('Resolved provision requires its target act identity')
        if row['resolution_status'] == 'RESOLVED' and not row.get('target_document_id'):
            raise ValueError('A parsed link alone is not a database-resolved citation')
    for row in rows['legal_assertion']:
        if not (row.get('subject_entity_id') or row.get('object_entity_id')):
            raise ValueError('Assertion needs a subject or object')
    for row in rows['legal_relation']:
        if row['subject_entity_id'] == row['object_entity_id'] or not (
                row.get('source_assertion_id') or row.get('source_provision_version_id')):
            raise ValueError('Relation needs distinct endpoints and legal evidence')
    for row in rows['ingestion_run']:
        if row.get('finished_at') and datetime.fromisoformat(row['finished_at']) < datetime.fromisoformat(row['started_at']):
            raise ValueError('Ingestion finished before it started')
    return rows


def generate_xsd(path=XSD):
    xs = 'http://www.w3.org/2001/XMLSchema'
    root = etree.Element(f'{{{xs}}}schema', nsmap={'xs': xs, 'f': NS}, targetNamespace=NS,
                         elementFormDefault='qualified', version='2.0')
    def sub(parent, tag, **attrs):
        return etree.SubElement(parent, f'{{{xs}}}{tag}', **attrs)
    for name, base, facets in [
        ('uuid', 'string', [('pattern', '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')]),
        ('json', 'string', []), ('year', 'integer', [('minInclusive', '1800'), ('maxInclusive', '2200')]),
        ('confidence', 'decimal', [('minInclusive', '0'), ('maxInclusive', '1'), ('fractionDigits', '4'), ('totalDigits', '5')]),
        ('period', 'string', [('pattern', r'\[[0-9]{4}-[0-9]{2}-[0-9]{2},([0-9]{4}-[0-9]{2}-[0-9]{2})?\)')]),
        *[(f'code{n}', 'string', [('maxLength', str(n))]) for n in (30,40,50,60,80,100,120,128,255,700)]]:
        restriction = sub(sub(root, 'simpleType', name=name), 'restriction', base=f'xs:{base}')
        for facet, value in facets:
            sub(restriction, facet, value=value)
    for table in ORDER:
        seq = sub(sub(root, 'complexType', name=table + 'Type'), 'sequence')
        for name, kind, required in fields(table):
            if table == 'citation' and name == 'citing_provision_version_id':
                choice = sub(seq, 'choice')
                for origin in ('citing_provision_version_id', 'citing_document_unit_id'):
                    sub(choice, 'element', name=origin, type='f:uuid')
                continue
            if table == 'citation' and name == 'citing_document_unit_id':
                continue
            scalar = kind in ('string', 'integer', 'positiveInteger', 'date', 'dateTime', 'boolean')
            sub(seq, 'element', name=name, type=('xs:' if scalar else 'f:') + kind,
                minOccurs='1' if required else '0')
    document = sub(root, 'element', name='legal_ingestion')
    complex_type = sub(document, 'complexType')
    seq = sub(complex_type, 'sequence')
    for table in ORDER:
        container = CONTAINERS[table]
        if container:
            inner = sub(sub(sub(seq, 'element', name=container), 'complexType'), 'sequence')
            sub(inner, 'element', name=table, type=f'f:{table}Type', minOccurs='0', maxOccurs='unbounded')
        else:
            sub(seq, 'element', name=table, type=f'f:{table}Type')
    sub(complex_type, 'attribute', name='schema_version', type='xs:string', fixed='2.0', use='required')
    for table in ORDER:
        selector = f'f:{CONTAINERS[table]}/f:{table}' if CONTAINERS[table] else f'f:{table}'
        key = sub(document, 'key', name=table + 'Ids')
        sub(key, 'selector', xpath=selector)
        sub(key, 'field', xpath='f:id')
    references = {
        'source_representation': {'source_document_id':'source_document'},
        'provision_identity': {'source_document_id':'source_document', 'parent_provision_id':'provision_identity'},
        'provision_version': {'provision_identity_id':'provision_identity'},
        'document_unit': {'source_representation_id':'source_representation', 'parent_unit_id':'document_unit', 'provision_version_id':'provision_version'},
        'ingestion_run': {'source_representation_id':'source_representation'},
        'parser_issue': {'ingestion_run_id':'ingestion_run', 'document_unit_id':'document_unit'},
        'citation': {'citing_document_unit_id':'document_unit', 'citing_provision_version_id':'provision_version'},
        # Target acts/provisions and graph endpoints can already exist outside this
        # one-act interchange file. Their foreign keys must be checked by the loader.
        'legal_assertion': {'source_provision_version_id':'provision_version', 'evidence_document_unit_id':'document_unit'},
        'legal_relation': {'source_assertion_id':'legal_assertion', 'source_provision_version_id':'provision_version'}}
    for table, refs in references.items():
        selector = f'f:{CONTAINERS[table]}/f:{table}' if CONTAINERS[table] else f'f:{table}'
        for column, target in refs.items():
            ref = sub(document, 'keyref', name=table + '_' + column, refer='f:' + target + 'Ids')
            sub(ref, 'selector', xpath=selector)
            sub(ref, 'field', xpath='f:' + column)
    path.write_bytes(etree.tostring(root, encoding='UTF-8', xml_declaration=True, pretty_print=True))


if __name__ == '__main__':
    generate_xsd()
