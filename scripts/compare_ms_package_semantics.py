"""Read-only comparison, allowing only refreshed relationship IDs and timestamps."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from xml.etree import ElementTree as ET
from zipfile import ZipFile

REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
DATES = {'{http://purl.org/dc/terms/}created', '{http://purl.org/dc/terms/}modified'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized_parts(path):
    with ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Duplicate package entries')
        data = {name: archive.read(name) for name in names}
    relationships = {}
    result = {}
    for name, content in data.items():
        if not name.endswith('.rels'):
            continue
        root = ET.fromstring(content)
        owner = str(PurePosixPath(name).parent.parent / PurePosixPath(name).name[:-5])
        mapping = {}
        entries = []
        for item in root:
            identifier = item.attrib.get('Id')
            if not identifier or identifier in mapping:
                raise ValueError('Invalid relationship identity')
            value = tuple(sorted((k, v) for k, v in item.attrib.items() if k != 'Id'))
            mapping[identifier] = value
            entries.append((item.tag, value, item.text, item.tail))
        relationships[owner] = mapping
        result[name] = (root.tag, tuple(sorted(root.attrib.items())), root.text, tuple(sorted(entries)))

    def node(value, mapping, core):
        attributes = []
        for key, text in value.attrib.items():
            if key in {'{' + REL + '}' + kind for kind in ('id', 'embed', 'link')}:
                if text not in mapping:
                    raise ValueError('Unresolved relationship reference')
                text = mapping[text]
            attributes.append((key, text))
        text = value.text
        if core and value.tag in DATES:
            if not text or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z', text):
                raise ValueError('Invalid core timestamp')
            text = '<generation timestamp>'
        return (value.tag, tuple(sorted(attributes)), text, value.tail,
                tuple(node(child, mapping, core) for child in value))

    for name, content in data.items():
        if name.endswith('.rels'):
            continue
        result[name] = (node(ET.fromstring(content), relationships.get(name, {}), name == 'docProps/core.xml')
                        if name.endswith('.xml') else hashlib.sha256(content).hexdigest())
    return result


def compare(first, second):
    left, right = normalized_parts(first), normalized_parts(second)
    return [name for name in sorted(set(left) | set(right)) if left.get(name) != right.get(name)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--folder', type=Path, required=True)
    args = parser.parse_args()
    folder = args.folder.resolve()
    stage = folder / 'registered-batch-v1'
    proof = json.loads((stage / 'render-qualified.json').read_bytes())
    accepted_proof = json.loads((folder / 'runtime-v1/candidate-qualified.json').read_bytes())
    accepted_items = {r['period_end']: r for r in accepted_proof['proof']['artifacts']}
    items = []
    for row in proof['proof']['artifacts']:
        name = Path(row['presentation_blob']).name
        current = stage / 'outputs' / name
        accepted = folder / 'runtime-v1/candidate-outputs' / name
        if (sha(current) != row['presentation_sha256']
                or sha(accepted) != accepted_items[row['period_end']]['presentation_sha256']):
            raise ValueError('Installed output changed')
        differences = compare(accepted, current)
        items.append({'period_end': row['period_end'], 'installed_sha256': sha(current),
                      'accepted_sha256': sha(accepted), 'semantic_differences': differences})
    record = {'status': 'passed' if len(items) == 21 and not any(i['semantic_differences'] for i in items) else 'review_required',
              'months': len(items), 'render_qualified_sha256': sha(stage / 'render-qualified.json'),
              'accepted_qualified_sha256': sha(folder / 'runtime-v1/candidate-qualified.json'),
              'checker_sha256': sha(Path(__file__)), 'source_data_changed': False,
              'allowed_variation': ['relationship identifiers with identical resolved properties', 'core creation/modification timestamps'],
              'items': items}
    with (stage / 'semantic-package-v2.json').open('x', encoding='utf-8') as stream:
        json.dump(record, stream, indent=2)
    print(json.dumps({'status': record['status'], 'months': len(items),
                      'semantic_differences': sum(len(i['semantic_differences']) for i in items)}))
    if record['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
