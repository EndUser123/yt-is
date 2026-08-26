import json
from collections import Counter, defaultdict
pk = {p['id']: p for p in json.load(open('packets-A-B.json'))}
r1 = {c['id']: c['label_class'] for c in json.load(open('review-1.json'))['concepts']}
r2 = {c['id']: c['label_class'] for c in json.load(open('review-2.json'))['concepts']}
# Preregistered adjudication (frozen in this file before reading results into the report):
# exact match kept; one-GOOD disagreement -> the non-GOOD verdict; other disagreements ->
# fixed severity order [ARTIFACT > DUPLICATE > ALIAS > TYPE_ERROR > TOO_GENERIC > TOO_SPECIFIC > AMBIGUOUS > GOOD].
order = ['EXTRACTION_ARTIFACT','DUPLICATE_OR_FRAGMENT','ALIAS_FRAGMENT','TYPE_ERROR','TOO_GENERIC','TOO_SPECIFIC_OR_EPHEMERAL','AMBIGUOUS','GOOD_CONCEPT']
def adj(a,b):
    if a==b: return a, True
    if b=='GOOD_CONCEPT' and a!='GOOD_CONCEPT': return a, False
    if a=='GOOD_CONCEPT' and b!='GOOD_CONCEPT': return b, False
    return sorted([a,b], key=order.index)[0], False
agg = {i: {'final': (v:=adj(r1[i],r2[i]))[0], 'agree': v[1], 'kind': pk[i]['kind'], 'label': pk[i]['label']} for i in pk}
json.dump({'concepts': agg}, open('aggregate-concepts-check.json','w'), indent=1)
