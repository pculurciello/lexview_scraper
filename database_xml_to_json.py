#!/usr/bin/env python3
"""Validate ingestion XML and export typed PostgreSQL staging rows. Does not connect to a DB."""
import argparse
import json
from pathlib import Path
from lxml import etree
from ingestion_model import XSD, validate


def export(input_path, output_path):
    root = etree.parse(str(input_path), etree.XMLParser(resolve_entities=False, no_network=True)).getroot()
    rows = validate(root)
    payload = {'schema':'fvg_laws', 'contract_version':'2.0', 'tables':rows,
               'policy':{'citation_targets_require_database_resolution':True,
                         'provision_versions_require_temporal_reconciliation':True}}
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return payload


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('input', type=Path)
    p.add_argument('output', type=Path)
    args = p.parse_args()
    export(args.input, args.output)
