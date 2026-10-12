"""Emit a guarded apply_patch patch; do not write source files directly."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
TARGETS=['src/ibkr_etl/web.py','.work/viewer-report-investigation-20261008/candidate/context/src/ibkr_etl/web.py']
changes=[
('account_display_name = escape(str(subject["display_name"]))','account_display_name = escape(account_card_title(subject))'),
('document.querySelector(\'#trade-note\').textContent=`Showing','document.querySelector(\'#trade-note\').textContent=result.not_applicable?result.message:`Showing'),
('<label><span class="label">As of</span>','<label class="asof-toolbar-control{\' report-date-hidden\' if section == \'reports\' else \'\'}"><span class="label">As of</span>'),
('Return observations: ${{rm.return_observations||0}}','Validated return intervals: ${{rm.return_observations||0}} from ${{rm.observations||0}} aligned valuations (the first valuation is a starting point, not a return)'),
]
print('*** Begin Patch')
for target in TARGETS:
    text=(ROOT/target).read_text(encoding='utf-8')
    replacements=[]
    for old,new in changes:
        lines=[l for l in text.splitlines() if old in l]
        assert len(lines)==1,(target,old,len(lines))
        line=lines[0]; replacement=line.replace(old,new)
        if 'account_display_name = escape(account_card_title' in replacement:
            replacement='        from .viewer_labels import account_card_title\n'+replacement
        if 'textContent=result.not_applicable?' in replacement:
            replacement+='\nif(result.not_applicable){{document.querySelector(\'#trade-history\').innerHTML=\'<tr><td colspan="11">No independently executed trades for this derived portfolio.</td></tr>\';document.querySelector(\'#export-trades\').hidden=true;document.querySelector(\'#trade-apply\').disabled=true;}}'
        replacements.append((line,replacement))
    print('*** Update File: '+(ROOT/target).as_posix())
    for old,new in sorted(replacements,key=lambda item:text.index(item[0])):
        print('@@');print('-'+old)
        print('\n'.join('+'+l for l in new.splitlines()))
print('*** End Patch')
