# sku_manager/geo.py
"""Find the Indian state / PIN code in a shipping address.

Order of trust:
  1. a state name written in the address (last mention wins - the state sits near
     the end of the address, while city names can repeat a state name),
  2. a PIN-code prefix, only for prefixes that map to one state without doubt.
"""
import re

# (canonical name, map code, aliases)  - map codes match static/sku_manager/india_map_data.js
_STATES = [
    ('Andaman and Nicobar Islands', 'an', ['andaman and nicobar islands', 'andaman nicobar', 'andaman']),
    ('Andhra Pradesh', 'ap', ['andhra pradesh', 'andhra']),
    ('Arunachal Pradesh', 'ar', ['arunachal pradesh', 'arunachal']),
    ('Assam', 'as', ['assam']),
    ('Bihar', 'br', ['bihar']),
    ('Chandigarh', 'ch', ['chandigarh']),
    ('Chhattisgarh', 'ct', ['chhattisgarh', 'chattisgarh', 'chhatisgarh']),
    ('Dadra and Nagar Haveli and Daman and Diu', 'dn',
     ['dadra and nagar haveli and daman and diu', 'dadra and nagar haveli', 'dadra nagar haveli',
      'daman and diu', 'daman diu', 'daman', 'silvassa']),
    ('Delhi', 'dl', ['nct of delhi', 'new delhi', 'delhi']),
    ('Goa', 'ga', ['goa']),
    ('Gujarat', 'gj', ['gujarat', 'gujrat']),
    ('Haryana', 'hr', ['haryana']),
    ('Himachal Pradesh', 'hp', ['himachal pradesh', 'himachal']),
    ('Jammu and Kashmir', 'jk', ['jammu and kashmir', 'jammu kashmir']),
    ('Jharkhand', 'jh', ['jharkhand']),
    ('Karnataka', 'ka', ['karnataka']),
    ('Kerala', 'kl', ['kerala']),
    ('Ladakh', 'jk', ['ladakh']),            # drawn inside J&K on the map
    ('Lakshadweep', 'ld', ['lakshadweep']),
    ('Madhya Pradesh', 'mp', ['madhya pradesh']),
    ('Maharashtra', 'mh', ['maharashtra']),
    ('Manipur', 'mn', ['manipur']),
    ('Meghalaya', 'ml', ['meghalaya']),
    ('Mizoram', 'mz', ['mizoram']),
    ('Nagaland', 'nl', ['nagaland']),
    ('Odisha', 'or', ['odisha', 'orissa']),
    ('Puducherry', 'py', ['puducherry', 'pondicherry']),
    ('Punjab', 'pb', ['punjab']),
    ('Rajasthan', 'rj', ['rajasthan']),
    ('Sikkim', 'sk', ['sikkim']),
    ('Tamil Nadu', 'tn', ['tamil nadu', 'tamilnadu']),
    ('Telangana', 'tg', ['telangana', 'telengana']),
    ('Tripura', 'tr', ['tripura']),
    ('Uttar Pradesh', 'up', ['uttar pradesh']),
    ('Uttarakhand', 'ut', ['uttarakhand', 'uttaranchal']),
    ('West Bengal', 'wb', ['west bengal']),
]

STATE_TO_CODE = {name: code for name, code, _ in _STATES}
# name shown on the map for a code (merged regions get a combined name)
CODE_TO_NAME = {code: name for name, code, _ in _STATES}
CODE_TO_NAME['jk'] = 'Jammu & Kashmir / Ladakh'
CODE_TO_NAME['dn'] = 'Dadra & Nagar Haveli and Daman & Diu'
CODE_TO_NAME['an'] = 'Andaman & Nicobar Islands'

_ALIAS_TO_NAME = {}
for _name, _code, _aliases in _STATES:
    for _alias in _aliases:
        _ALIAS_TO_NAME[_alias] = _name
_ALIAS_RE = re.compile(
    r'(?<![a-z0-9])(' + '|'.join(re.escape(a) for a in sorted(_ALIAS_TO_NAME, key=len, reverse=True)) + r')(?![a-z0-9])'
)

PIN_RE = re.compile(r'(?<!\d)([1-9]\d{5})(?!\d)')

# 3-digit overrides first, then 2-digit. Only unambiguous prefixes are listed.
_PIN3 = {'194': 'Ladakh', '160': 'Chandigarh', '403': 'Goa', '605': 'Puducherry', '737': 'Sikkim', '744': 'Andaman and Nicobar Islands',
         '246': 'Uttarakhand', '248': 'Uttarakhand', '249': 'Uttarakhand', '262': 'Uttarakhand', '263': 'Uttarakhand',
         '790': 'Arunachal Pradesh', '791': 'Arunachal Pradesh', '792': 'Arunachal Pradesh',
         '793': 'Meghalaya', '794': 'Meghalaya', '795': 'Manipur', '796': 'Mizoram',
         '797': 'Nagaland', '798': 'Nagaland', '799': 'Tripura'}
_PIN2 = {'11': 'Delhi', '12': 'Haryana', '13': 'Haryana', '14': 'Punjab', '15': 'Punjab', '16': 'Punjab',
         '17': 'Himachal Pradesh', '18': 'Jammu and Kashmir', '19': 'Jammu and Kashmir', '20': 'Uttar Pradesh', '21': 'Uttar Pradesh', '22': 'Uttar Pradesh',
         '23': 'Uttar Pradesh', '25': 'Uttar Pradesh', '26': 'Uttar Pradesh', '27': 'Uttar Pradesh',
         '28': 'Uttar Pradesh', '30': 'Rajasthan', '31': 'Rajasthan', '32': 'Rajasthan', '33': 'Rajasthan',
         '34': 'Rajasthan', '36': 'Gujarat', '37': 'Gujarat', '38': 'Gujarat', '39': 'Gujarat',
         '40': 'Maharashtra', '41': 'Maharashtra', '42': 'Maharashtra', '43': 'Maharashtra', '44': 'Maharashtra',
         '45': 'Madhya Pradesh', '46': 'Madhya Pradesh', '47': 'Madhya Pradesh', '48': 'Madhya Pradesh',
         '49': 'Chhattisgarh', '56': 'Karnataka', '57': 'Karnataka', '58': 'Karnataka', '59': 'Karnataka',
         '60': 'Tamil Nadu', '61': 'Tamil Nadu', '62': 'Tamil Nadu', '63': 'Tamil Nadu', '64': 'Tamil Nadu',
         '67': 'Kerala', '68': 'Kerala', '69': 'Kerala', '70': 'West Bengal', '71': 'West Bengal',
         '72': 'West Bengal', '73': 'West Bengal', '74': 'West Bengal', '75': 'Odisha', '76': 'Odisha',
         '77': 'Odisha', '78': 'Assam'}


def extract_pincode(address):
    matches = PIN_RE.findall(address or '')
    return matches[-1] if matches else ''


def state_from_pincode(pin):
    if not pin or len(pin) != 6:
        return ''
    return _PIN3.get(pin[:3]) or _PIN2.get(pin[:2]) or ''


def detect_state(address):
    """Return the canonical state name for an address, or '' if it cannot be told."""
    text = re.sub(r'[^a-z0-9]+', ' ', (address or '').lower()).strip()
    if text:
        found = _ALIAS_RE.findall(text)
        if found:
            return _ALIAS_TO_NAME[found[-1]]
    return state_from_pincode(extract_pincode(address))


def state_code(state_name):
    return STATE_TO_CODE.get(state_name or '', '')
