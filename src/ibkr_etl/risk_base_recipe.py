"""Explicit stable-Trixie security candidate, never a production release gate.

Preserve the original template and package provenance. Install only the base's
configured Debian repositories; no mixed-distribution libraries or scan filters.
"""
import re


SOURCE='docker.io/library/python:3.12.15-slim-trixie'
OLD_FROM='FROM python:3.12.15-slim-bookworm@sha256:2ed6491b93cd49272ee6de2b5a38440c3448360322c089fc23e370722d74179d AS builder'
DIGEST=re.compile(r'sha256:[0-9a-f]{64}')


def trixie_security_recipe(template, resolution):
    if not isinstance(template,str) or not isinstance(resolution,dict):
        raise ValueError('Original template and resolved registry evidence required')
    digest=resolution.get('amd64_digest')
    if resolution.get('source')!=SOURCE or not isinstance(digest,str) or not DIGEST.fullmatch(digest):
        raise ValueError('Exact official stable Python Trixie source required')
    if not isinstance(resolution.get('index_digest'),str) or not DIGEST.fullmatch(resolution['index_digest']):
        raise ValueError('Immutable original index required')
    index=resolution.get('index',{});manifest=resolution.get('manifest',{})
    platforms=[m for m in index.get('manifests',[]) if m.get('platform')=={'architecture':'amd64','os':'linux'}]
    if len(platforms)!=1 or platforms[0].get('digest')!=digest or manifest.get('schemaVersion')!=2:
        raise ValueError('Resolved native amd64 manifest differs')
    if template.count(OLD_FROM)!=1 or template.count('FROM ')!=2 or 'FROM scratch' not in template:
        raise ValueError('Exact reviewed original two-stage template required')
    lines=template.splitlines()
    if OLD_FROM not in lines or 'RUN python scripts/assemble_minimal_risk_runtime.py' not in lines:
        raise ValueError('Expected exact builder statements required')
    replacement='FROM python:3.12.15-slim-trixie@'+digest+' AS builder'
    recipe=template.replace(OLD_FROM,replacement,1)
    # Fail closed if the configured native repositories do not contain fixes.
    # Preserve installed status records; no custom/mislabelled fixed versions.
    upgrade=('RUN apt-get update && apt-get dist-upgrade -y '
        '&& dpkg --compare-versions "$(dpkg-query -W -f=\'${Version}\' libsqlite3-0)" ge 3.46.1-7+deb13u2 '
        '&& dpkg --compare-versions "$(dpkg-query -W -f=\'${Version}\' libssl3t64)" ge 3.5.7-1~deb13u3 '
        '&& python -c "from pathlib import Path; assert \'VERSION_CODENAME=trixie\' in Path(\'/etc/os-release\').read_text()"\n')
    recipe=recipe.replace(replacement+'\n',replacement+'\n'+upgrade,1)
    return recipe
