"""Decode only literal, known archive wrappers; no eval or payload execution."""
import ast, base64, gzip, hashlib, json, lzma
from pathlib import Path
from freeze_upgrade_owner_jobs_20261010 import OUT
from control_upgrade_readers_20261010 import save


def literal(node):
    if isinstance(node, ast.Constant) and type(node.value) in (str, bytes): return node.value
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'replace' and len(node.args) == 2 and not node.keywords:
        a, b = node.args
        if isinstance(a, ast.Constant) and a.value == ':' and isinstance(b, ast.Call) and isinstance(b.func, ast.Name) and b.func.id == 'chr' and len(b.args) == 1 and isinstance(b.args[0], ast.Constant) and b.args[0].value == 36 and not b.keywords:
            return literal(node.func.value).replace(':', '$')
    raise ValueError('Unknown static wrapper; manual review required')


def decode(source, seen=None, depth=0):
    if seen is None: seen = set()
    if depth > 6 or len(source) > 4000000: raise ValueError('Payload bound exceeded')
    key = hashlib.sha256(source.encode()).hexdigest()
    if key in seen: return []
    seen.add(key)
    tree = ast.parse(source); result = [source]
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and ast.unparse(n.func) in ('base64.b64decode', 'base64.b85decode') and len(n.args) == 1:
            # Dynamic library decoding is not a wrapper and is not evaluated.
            try: encoded = literal(n.args[0])
            except ValueError: continue
            raw = base64.b85decode(encoded) if ast.unparse(n.func).endswith('b85decode') else base64.b64decode(encoded, validate=True)
            if len(raw) > 4000000: raise ValueError('Compressed payload bound exceeded')
            if raw.startswith(b'\x1f\x8b'):
                import zlib
                decoder = zlib.decompressobj(31); raw = decoder.decompress(raw, 4000001)
                if not decoder.eof or decoder.unused_data or len(raw) > 4000000: raise ValueError('Unbounded gzip payload')
            elif raw.startswith(b'\xfd7zXZ\x00'):
                decoder = lzma.LZMADecompressor(); raw = decoder.decompress(raw, max_length=4000001)
                if not decoder.eof or decoder.unused_data or len(raw) > 4000000: raise ValueError('Unbounded LZMA payload')
            try: text = raw.decode(); ast.parse(text)
            except (UnicodeError, SyntaxError): continue
            result.extend(decode(text, seen, depth + 1))
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and '\n' in n.value and len(n.value) > 300:
            try: ast.parse(n.value)
            except SyntaxError: continue
            result.extend(decode(n.value, seen, depth + 1))
    return result


def main():
    if not (OUT / 'inventory.json').exists(): raise ValueError('Complete baseline capture required')
    items = []
    for path in sorted(OUT.glob('*/baseline.json')):
        frozen = json.loads(path.read_bytes()); name = path.parent.name
        c = frozen['job']['properties']['template']['containers'][0]
        argv = c.get('command', []) + c.get('args', [])
        texts = decode(argv[-1]) if '-c' in argv else []
        details = []
        for text in texts:
            tree = ast.parse(text)
            details.append({'sha256': hashlib.sha256(text.encode()).hexdigest(), 'text': text,
                'imports': sorted({n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module} | {x.name for n in ast.walk(tree) if isinstance(n, ast.Import) for x in n.names}),
                'image_binding': '276a4783bf4ddf437bd5d449e08df39729fb43f449975082af80fd4c13e1142c' in text,
                'runtime_binding': 'expected_runtime' in text or 'ca_sha256' in text})
        item = {'job': name, 'sources': details, 'baseline_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        items.append(item)
        print(json.dumps({'job': name, 'sources': len(details), 'image_binding': any(d['image_binding'] for d in details), 'runtime_binding': any(d['runtime_binding'] for d in details)}), flush=True)
    save(OUT, 'expanded-static-v2', {'jobs': items, 'payloads_executed': False, 'live_settings_changed': False,
        'qualification': 'Static source inspection only; no behavioral or security pass inferred'})


if __name__ == '__main__': main()
