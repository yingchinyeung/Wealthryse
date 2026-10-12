"""Count-checked mechanical UI substitutions in the isolated upgrade only."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / '.work/platform-upgrade-20261009-v1/runtime/src/ibkr_etl/web.py'

def main():
    text = TARGET.read_text(encoding='utf-8')
    replacements = [
        ("['Earliest completion',burnIn.earliest_completion_date", "['Original earliest completion',burnIn.earliest_completion_date", 2),
        ('<div class="value">IBKR executions</div><div class="muted">Read-only executed trades',
         '<div id="trade-source-title" class="value">Trade records</div><div id="trade-source-note" class="muted">Read-only executed trades', 1),
        ('<div class="filter-grid"><label><span class="label">From</span><input id="trade-from"',
         '<div id="trade-filter-controls" class="filter-grid"><label><span class="label">From</span><input id="trade-from"', 1),
        ("if(result.not_applicable){{document.querySelector('#trade-history')",
         "if(result.not_applicable){{document.querySelector('#trade-source-title').textContent='Derived portfolio — no independently executed trades';document.querySelector('#trade-source-note').textContent=result.message;document.querySelector('#trade-filter-controls').hidden=true;document.querySelector('#trade-history')", 1),
        ("const pct=n=>n==null?'—':new Intl.NumberFormat('en-US',{{style:'percent',maximumFractionDigits:2}}).format(Number(n));",
         "const pct=n=>{{if(n==null)return '—';const formatter=new Intl.NumberFormat('en-US',{{style:'percent',maximumFractionDigits:2}}),parts=formatter.formatToParts(Number(n)),zero=parts.filter(p=>p.type==='integer'||p.type==='fraction').every(p=>Number(p.value)===0);return (zero?parts.filter(p=>p.type!=='minusSign'):parts).map(p=>p.value).join('');}};", 1),
    ]
    for old, new, expected in replacements:
        if text.count(old) != expected:
            raise ValueError('UI baseline changed; do not guess a replacement: ' + old[:65])
        text = text.replace(old, new)
    TARGET.write_text(text, encoding='utf-8')
    print('Applied bounded label/control/rounding substitutions; no financial input values changed.')

if __name__ == '__main__':
    main()
