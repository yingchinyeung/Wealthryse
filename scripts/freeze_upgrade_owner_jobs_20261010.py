"""Read-only capture and bounded static inspection; never execute job payloads."""
import ast, base64, gzip, hashlib, json, lzma
from datetime import datetime, timezone
from pathlib import Path
from deploy_viewer_private_admin_20261008 import request, rid
from deploy_ms_presentation_worker_release import settings_invariant
from control_upgrade_readers_20261010 import save, SOURCE, API
from control_upgrade_legacy_recovery_probe_20261009 import protected

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.work/platform-upgrade-20261010-stage17/owner-jobs-v1'


def sources(source, seen=None, depth=0):
    if seen is None: seen = set()
    if depth > 5 or len(source) > 2000000: raise ValueError('Source bound exceeded')
    digest = hashlib.sha256(source.encode()).hexdigest()
    if digest in seen: return []
    seen.add(digest)
    tree = ast.parse(source)
    result = [source]
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and ast.unparse(node.func) in ('base64.b64decode', 'b64decode'):
            for arg in node.args[:1]:
                if not isinstance(arg, ast.Constant) or not isinstance(arg.value, (str, bytes)): continue
                raw = base64.b64decode(arg.value, validate=True)
                if raw.startswith(b'\x1f\x8b'): raw = gzip.decompress(raw)
                elif raw.startswith(b'\xfd7zXZ\x00'): raw = lzma.decompress(raw)
                try: text = raw.decode(); ast.parse(text)
                except (UnicodeError, SyntaxError): continue
                result.extend(sources(text, seen, depth + 1))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and '\n' in node.value and len(node.value) > 300:
            try: ast.parse(node.value)
            except SyntaxError: continue
            result.extend(sources(node.value, seen, depth + 1))
    return result


def main():
    if (OUT / 'inventory.json').exists(): raise ValueError('Inventory already complete')
    inventory = json.loads((ROOT / '.work/platform-upgrade-20261010-stage16/image-coverage-v1.json').read_bytes())
    names = next(i['templates'] for i in inventory['images'] if i['image'] == SOURCE)
    records = []
    for name in names:
        job = request('GET', rid('Microsoft.App/jobs', name) + API)
        c = job['properties']['template']['containers'][0]
        if c['image'] != SOURCE or job['properties']['configuration']['triggerType'] != 'Manual': raise ValueError('Frozen scope drift')
        if any(s.get('value') for s in (job['properties']['configuration'].get('secrets') or [])): raise ValueError('Inline secret capture forbidden')
        if any(e.get('value') for e in c.get('env', []) if any(k in e['name'] for k in ('PASSWORD', 'TOKEN', 'SECRET', '_URL'))): raise ValueError('Inline credential capture forbidden')
        if (OUT / name / 'baseline.json').exists():
            baseline = protected(OUT / name / 'baseline.json')
            if baseline['invariant'] != settings_invariant(job): raise ValueError('Prior captured job drifted')
        else: save(OUT / name, 'baseline', {'job': job, 'image': SOURCE, 'invariant': settings_invariant(job)})
        argv = c.get('command', []) + c.get('args', [])
        texts = sources(argv[-1]) if '-c' in argv else []
        details = []
        for text in texts:
            tree = ast.parse(text)
            imports = sorted({n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module} | {x.name for n in ast.walk(tree) if isinstance(n, ast.Import) for x in n.names})
            calls = sorted({ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)})
            details.append({'sha256': hashlib.sha256(text.encode()).hexdigest(), 'imports': imports, 'calls': calls,
                            'image_binding': SOURCE in text, 'runtime_descriptor_binding': 'expected_runtime' in text or 'ca_sha256' in text,
                            'text': text})
        static = {'job': name, 'sources': details, 'payload_executed': False}
        if (OUT / name / 'static.json').exists():
            prior = protected(OUT / name / 'static.json')
            if {k: v for k, v in prior.items() if k != 'protected_receipt'} != static: raise ValueError('Static source drift')
        else: save(OUT / name, 'static', static)
        row = {'job': name, 'source_count': len(texts), 'imports': sorted({i for d in details for i in d['imports']}),
               'image_binding': any(d['image_binding'] for d in details), 'runtime_descriptor_binding': any(d['runtime_descriptor_binding'] for d in details)}
        records.append(row)
        print(json.dumps(row), flush=True)
    save(OUT, 'inventory', {'checked_at': datetime.now(timezone.utc).isoformat(), 'jobs': records, 'mutations': False, 'payloads_executed': False})


if __name__ == '__main__': main()
