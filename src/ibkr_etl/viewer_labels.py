"""Consistent identifiers without presenting a derived portfolio as an IBKR account."""
import re

def account_card_title(subject):
    identifier=str(subject['subject_id'])
    name=str(subject['display_name']).strip()
    name=re.sub(r'^'+re.escape(identifier)+r'\s*[-–—]\s*','',name)
    return f'{identifier} - {name}' if name!=identifier else identifier
