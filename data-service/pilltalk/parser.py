"""Parse product-scoped SPL attributes; never copy one strength's appearance to another."""
import re
from urllib.parse import urlencode
from defusedxml import ElementTree as ET
NS = {'h': 'urn:hl7-org:v3'}

def norm(value):
    return re.sub(r'\s+', ' ', (value or '').strip().lower())

def imprint_key(value):
    return re.sub(r'[^A-Z0-9]', '', (value or '').upper())

def text(el):
    return ''.join(el.itertext()).strip() if el is not None else None

def attr(el, key):
    return el.get(key) if el is not None else None

def parse_spl(raw):
    root = ET.fromstring(raw)
    sid = attr(root.find('h:setId', NS), 'root')
    if not sid:
        raise ValueError('SPL has no setId')
    version = attr(root.find('h:versionNumber', NS), 'value') or ''
    doc_type = attr(root.find('h:code', NS), 'code')
    # Human prescription and OTC drug labels. Animal/device labels are excluded.
    if doc_type not in {'34391-3', '34390-5'}:
        return sid, version, []
    labeler = text(root.find('h:author/h:assignedEntity/h:representedOrganization/h:name', NS))
    media = {}
    for node in root.findall('.//h:observationMedia', NS):
        ref = node.find('h:value/h:reference', NS)
        if node.get('ID') and ref is not None:
            media[node.get('ID')] = ref.get('value')
    parents = {child: parent for parent in root.iter() for child in parent}
    products = {}
    for p in root.findall('.//h:manufacturedProduct', NS) + root.findall('.//h:manufacturedMedicine', NS):
        code = p.find('h:code', NS)
        if code is None or code.get('codeSystem') != '2.16.840.1.113883.6.69':
            continue
        ndc = code.get('code')
        form = attr(p.find('h:formCode', NS), 'displayName') or ''
        scope = parents.get(p)
        if scope is None or scope.tag != '{urn:hl7-org:v3}manufacturedProduct':
            scope = p
        routes = {x.get('code') for node in (p, scope) for x in node.findall('h:consumedIn/h:substanceAdministration/h:routeCode', NS)}
        # Require explicit oral route, preserving exclusion rather than guessing.
        if 'C38288' not in routes or not any(x in form.upper() for x in ('TABLET', 'CAPSULE', 'PELLET', 'LOZENGE', 'TROCHE')):
            continue
        chars = {}
        for ch in scope.findall('h:subjectOf/h:characteristic', NS):
            k = attr(ch.find('h:code', NS), 'code')
            v = ch.find('h:value', NS)
            if k and v is not None:
                chars.setdefault(k, []).append(v)
        def vals(k):
            return [norm(x.get('displayName') or x.get('value') or text(x)) for x in chars.get(k, []) if x.get('displayName') or x.get('value') or text(x)]
        imprint = ';'.join(vals('SPLIMPRINT')) or None
        size = chars.get('SPLSIZE', [None])[0]
        score = vals('SPLSCORE')
        ingredients = []
        for ing in p.findall('h:ingredient', NS):
            if not (ing.get('classCode') or '').startswith('ACTI'):
                continue
            n = ing.find('h:quantity/h:numerator', NS)
            d = ing.find('h:quantity/h:denominator', NS)
            ingredients.append({'name': text(ing.find('h:ingredientSubstance/h:name', NS)),
                'unii': attr(ing.find('h:ingredientSubstance/h:code', NS), 'code'),
                'numerator': dict(n.attrib) if n is not None else None,
                'denominator': dict(d.attrib) if d is not None else None})
        images = []
        for v in chars.get('SPLIMAGE', []):
            for ref in v.findall('.//h:reference', NS):
                filename = ref.get('value')
                if filename:
                    images.append('https://dailymed.nlm.nih.gov/dailymed/image.cfm?' + urlencode({'setid': sid, 'name': filename}))
            for ref in v.findall('.//h:renderMultiMedia', NS):
                filename = media.get(ref.get('referencedObject'))
                if filename:
                    images.append('https://dailymed.nlm.nih.gov/dailymed/image.cfm?' + urlencode({'setid': sid, 'name': filename}))
        packages = sorted({x.get('code') for x in p.findall('.//h:containerPackagedProduct/h:code', NS) if x.get('codeSystem') == '2.16.840.1.113883.6.69' and x.get('code')})
        products[ndc] = dict(id=f'{sid}:{ndc}', setid=sid, version=version, product_ndc=ndc,
            name=text(p.find('h:name', NS)) or ndc, labeler=labeler, dosage_form=form,
            colours=sorted(set(vals('SPLCOLOR'))), shape=(vals('SPLSHAPE') or [None])[0],
            imprint=imprint, imprint_normalized=imprint_key(imprint),
            size_value=attr(size, 'value'), size_unit=attr(size, 'unit'), score=(score or [None])[0],
            ingredients=ingredients, package_ndcs=packages, image_urls=sorted(set(images)),
            source_url=f'https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={sid}')
    return sid, version, list(products.values())
