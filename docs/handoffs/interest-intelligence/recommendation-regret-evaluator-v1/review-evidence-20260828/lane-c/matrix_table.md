| # | Area | Case | Expected | Actual | Verdict |
|---|------|------|----------|--------|---------|
| 1 | E.1 build | packet_id None | PBE | `PacketBindError` | PASS |
| 2 | E.1 build | packet_id empty | PBE | `PacketBindError` | PASS |
| 3 | E.1 build | packet_id int | PBE | `PacketBindError` | PASS |
| 4 | E.1 build | packet_id dict | PBE | `PacketBindError` | PASS |
| 5 | E.1 build | packet_id whitespace-only | PBE | `PacketBindError` | PASS |
| 6 | E.1 build | arms 1 arm | PBE | `PacketBindError` | PASS |
| 7 | E.1 build | arms 3 arms | PBE | `PacketBindError` | PASS |
| 8 | E.1 build | arms passed as LIST (duplicate-arm path) | PBE | `RAISED:AttributeError:'list' object has no attribute 'keys'` | FAIL |
| 9 | E.1 build | non-string arm id (int arm key) | PBE | `RAISED:TypeError:'<' not supported between instances of 'str' and 'int'` | FAIL |
| 10 | E.1 build | arm value not a list (int) | PBE | `RAISED:TypeError:object of type 'int' has no len()` | FAIL |
| 11 | E.1 build | item record not a dict (int) | PBE | `PacketBindError` | PASS |
| 12 | E.1 build | two items in one arm (pairwise=1/arm) | PBE | `PacketBindError` | PASS |
| 13 | E.1 build | title int | PBE | `PacketBindError` | PASS |
| 14 | E.1 build | title dict | PBE | `PacketBindError` | PASS |
| 15 | E.1 build | title list | PBE | `PacketBindError` | PASS |
| 16 | E.1 build | title None | PBE | `ACCEPTED -> {'blinded_packet': {'packet_schema': 'rre_v1_pairwise', 'packet_id': 'pid', 'marker_synthetic': Tr` | FAIL-SILENT |
| 17 | E.1 build | claims int | PBE | `PacketBindError` | PASS |
| 18 | E.1 build | claims dict | PBE | `PacketBindError` | PASS |
| 19 | E.1 build | claims list | PBE | `PacketBindError` | PASS |
| 20 | E.1 build | claims None | PBE | `ACCEPTED -> {'blinded_packet': {'packet_schema': 'rre_v1_pairwise', 'packet_id': 'pid', 'marker_synthetic': Tr` | FAIL-SILENT |
| 21 | E.1 build | why_surfaced int | PBE | `PacketBindError` | PASS |
| 22 | E.1 build | why_surfaced dict | PBE | `PacketBindError` | PASS |
| 23 | E.1 build | why_surfaced list | PBE | `PacketBindError` | PASS |
| 24 | E.1 build | why_surfaced None | PBE | `ACCEPTED -> {'blinded_packet': {'packet_schema': 'rre_v1_pairwise', 'packet_id': 'pid', 'marker_synthetic': Tr` | FAIL-SILENT |
| 25 | E.1 build | evidence_refs str | PBE | `PacketBindError` | PASS |
| 26 | E.1 build | evidence_refs dict | PBE | `PacketBindError` | PASS |
| 27 | E.1 build | evidence_refs int | PBE | `PacketBindError` | PASS |
| 28 | E.1 build | evidence_refs entry dict w/ 'score' | PBE | `ACCEPTED -> {'blinded_packet': {'packet_schema': 'rre_v1_pairwise', 'packet_id': 'pid', 'marker_synthetic': Tr` | FAIL-SILENT |
| 29 | E.1 build | evidence_refs entry int | PBE | `ACCEPTED -> {'blinded_packet': {'packet_schema': 'rre_v1_pairwise', 'packet_id': 'pid', 'marker_synthetic': Tr` | FAIL-SILENT |
| 30 | E.1 build | evidence_refs entry None | PBE | `ACCEPTED -> {'blinded_packet': {'packet_schema': 'rre_v1_pairwise', 'packet_id': 'pid', 'marker_synthetic': Tr` | FAIL-SILENT |
| 31 | E.1 build | evidence_refs entry nested list | PBE | `ACCEPTED -> {'blinded_packet': {'packet_schema': 'rre_v1_pairwise', 'packet_id': 'pid', 'marker_synthetic': Tr` | FAIL-SILENT |
| 32 | E.1 build | related_records int | PBE | `PacketBindError` | PASS |
| 33 | E.1 build | related_records str | PBE | `PacketBindError` | PASS |
| 34 | E.1 build | related_records dict | PBE | `PacketBindError` | PASS |
| 35 | E.1 build | related_records entry non-dict | PBE | `PacketBindError` | PASS |
| 36 | E.1 build | related_records entry extra keys | PBE | `PacketBindError` | PASS |
| 37 | E.1 build | related_records kind int | PBE | `PacketBindError` | PASS |
| 38 | E.1 build | related_records id empty str | PBE | `PacketBindError` | PASS |
| 39 | E.1 build | recent_surfaced_items int entry | PBE | `PBE:opaque id source (r) must be a non-empty string, got 42` | PASS |
| 40 | E.1 build | listwise k=0 | PBE | `PacketBindError` | PASS |
| 41 | E.1 build | listwise k=-1 (3 items/arm: slice drops last) | PBE | `ACCEPTED -> {'blinded_packet': {'packet_schema': 'rre_v1_listwise', 'packet_id': 'pid', 'k': -1, 'marker_synth` | FAIL-SILENT |
| 42 | E.1 build | listwise k str '2' | PBE | `RAISED:TypeError:slice indices must be integers or None or have an __index__ method` | FAIL |
| 43 | E.1 build | listwise k float 1.5 | PBE | `RAISED:TypeError:slice indices must be integers or None or have an __index__ method` | FAIL |
| 44 | E.1 build | listwise empty arms dict | PBE | `PacketBindError` | PASS |
| 45 | E.1 build | listwise one arm empty list | PBE | `PacketBindError` | PASS |
| 46 | E.1 build | listwise arm value not list | PBE | `RAISED:TypeError:'int' object is not subscriptable` | FAIL |
| 47 | E.2 validate | packet doc int | OKD | `OKD:ok=False:packet document must be a JSON object` | PASS |
| 48 | E.2 validate | packet doc list | OKD | `OKD:ok=False:packet document must be a JSON object` | PASS |
| 49 | E.2 validate | blinded_packet scalar int | OKD | `OKD:ok=False:blinded_packet must be a JSON object` | PASS |
| 50 | E.2 validate | blinded_packet None (falsy falls to wrapper) | OKD | `OKD:ok=False:unsupported schema None` | PASS |
| 51 | E.2 validate | unknown schema | OKD | `OKD:ok=False:unsupported schema 'rre_v9'` | PASS |
| 52 | E.2 validate | missing packet_id | OKD | `OKD:ok=False:packet_id must be a non-empty string, got None` | PASS |
| 53 | E.2 validate | items as list | OKD | `OKD:ok=False:items must be a JSON object; ITEM_1: missing item_id; ITEM_2: missing item_id` | PASS |
| 54 | E.2 validate | view as scalar | OKD | `RAISED:AttributeError:'str' object has no attribute 'get'` | FAIL |
| 55 | E.2 validate | view as falsy scalar 0 (EXTRA) | OKD | `OKD:ok=False:ITEM_2: judge view must be a JSON object; ITEM_2: missing item_id` | PASS |
| 56 | E.2 validate | scalar related_records | OKD | `OKD:ok=False:ITEM_1: judge field 'related_records' must be an array` | PASS |
| 57 | E.2 validate | marker_synthetic absent | OKD | `OKD:ok=False:ITEM_1: synthetic/provenance marker absent; ITEM_2: synthetic/provenance marker absent` | PASS |
| 58 | E.2 validate | inlined sidecar next to packet | OKD | `OKD:ok=False:packet leaked forbidden machinery key at blind_sidecar; packet leaked forbidden machinery key at ` | PASS |
| 59 | E.2 validate | wrapper key 'arms' (machinery substring) | OKD | `OKD:ok=False:packet leaked forbidden machinery key at arms` | PASS |
| 60 | E.3 bind | sidecar list | PBE | `PacketBindError` | PASS |
| 61 | E.3 bind | sidecar None | PBE | `PacketBindError` | PASS |
| 62 | E.3 bind | sidecar int | PBE | `PacketBindError` | PASS |
| 63 | E.3 bind | unknown binding_version | PBE | `PacketBindError` | PASS |
| 64 | E.3 bind | missing binding_mac key | PBE | `PacketBindError` | PASS |
| 65 | E.3 bind | empty binding_mac | PBE | `PacketBindError` | PASS |
| 66 | E.3 bind | sidecar arms 1 arm | PBE | `PacketBindError` | PASS |
| 67 | E.3 bind | sidecar arms 3 arms | PBE | `PacketBindError` | PASS |
| 68 | E.3 bind | sidecar arms duplicate arms | PBE | `PacketBindError` | PASS |
| 69 | E.3 bind | sidecar arms non-string arm | PBE | `PacketBindError` | PASS |
| 70 | E.3 bind | sidecar arms whitespace arm | PBE | `PacketBindError` | PASS |
| 71 | E.3 bind | sidecar arms unhashable arm entry | PBE | `PacketBindError` | PASS |
| 72 | E.3 bind | reverse_map list | PBE | `PacketBindError` | PASS |
| 73 | E.3 bind | slots str | PBE | `PacketBindError` | PASS |
| 74 | E.3 bind | reverse_map int key | PBE | `PacketBindError` | PASS |
| 75 | E.3 bind | reverse_map int value | PBE | `PacketBindError` | PASS |
| 76 | E.3 bind | slots list value (unhashable) | PBE | `PacketBindError` | PASS |
| 77 | E.3 bind | slots keys 0/2 | PBE | `PacketBindError` | PASS |
| 78 | E.3 bind | slots missing key 1 | PBE | `PacketBindError` | PASS |
| 79 | E.3 bind | rev value LIST_ token on pairwise | PBE | `PacketBindError` | PASS |
| 80 | E.3 bind | rev value TIE | PBE | `PacketBindError` | PASS |
| 81 | E.3 bind | rev keys not the arm set | PBE | `PacketBindError` | PASS |
| 82 | E.3 bind | flipped reverse_map | PBE | `PacketBindError` | PASS |
| 83 | E.3 bind | same-layout sidecar from other packet | PBE | `PacketBindError` | PASS |
| 84 | E.3 bind | sidecar packet_id mismatch | PBE | `PacketBindError` | PASS |
| 85 | E.3 bind | one-byte packet change (digest) | PBE | `PacketBindError` | PASS |
| 86 | E.3 bind | binding_mac wrong value | PBE | `PacketBindError` | PASS |
| 87 | E.3 bind | binding_mac non-ASCII | PBE | `PacketBindError` | PASS |
| 88 | E.3 bind | binding_mac wrong length | PBE | `PacketBindError` | PASS |
| 89 | E.3 bind | binding_mac None value | PBE | `PacketBindError` | PASS |
| 90 | E.3 bind | secret None | PBE | `PacketBindError` | PASS |
| 91 | E.3 bind | secret empty | PBE | `PacketBindError` | PASS |
| 92 | E.3 bind | secret empty bytes | PBE | `PacketBindError` | PASS |
| 93 | E.3 bind | secret whitespace | PBE | `PacketBindError` | PASS |
| 94 | E.3 bind | secret short | PBE | `PacketBindError` | PASS |
| 95 | E.3 bind | secret non-hex | PBE | `PacketBindError` | PASS |
| 96 | E.3 bind | blinded_packet as list (EXTRA) | PBE | `RAISED:AttributeError:'list' object has no attribute 'get'` | FAIL |
| 97 | E.4 unbind | missing WOULD_REGRET | PBE | `PacketBindError` | PASS |
| 98 | E.4 unbind | missing MORE_USEFUL | PBE | `PacketBindError` | PASS |
| 99 | E.4 unbind | BOTH missing | PBE | `PacketBindError` | PASS |
| 100 | E.4 unbind | judgment int | PBE | `PacketBindError` | PASS |
| 101 | E.4 unbind | judgment None | PBE | `PacketBindError` | PASS |
| 102 | E.4 unbind | ITEM_3 | PBE | `PacketBindError` | PASS |
| 103 | E.4 unbind | lowercase item_1 | PBE | `PacketBindError` | PASS |
| 104 | E.4 unbind | judgments as list (EXTRA) | PBE | `RAISED:AttributeError:'list' object has no attribute 'get'` | FAIL |
| 105 | E.5 unbindL | missing outcome | PBE | `PacketBindError` | PASS |
| 106 | E.5 unbindL | unknown outcome ITEM_9 | PBE | `PacketBindError` | PASS |
| 107 | E.5 unbindL | marks missing LIST_2 side | PBE | `PacketBindError` | PASS |
| 108 | E.5 unbindL | mark id from OTHER list | PBE | `PacketBindError` | PASS |
| 109 | E.5 unbindL | marks value int (non-list) | PBE | `RAISED:TypeError:'int' object is not iterable` | FAIL |
| 110 | E.5 unbindL | marks value None (non-list) | PBE | `RAISED:TypeError:'NoneType' object is not iterable` | FAIL |
| 111 | E.5 unbindL | marks value str (non-list) | PBE | `PacketBindError` | PASS |
| 112 | E.6 agg | missing judgment for a question | PBE | `PacketBindError` | PASS |
| 113 | E.6 agg | unknown preferred_arm | PBE | `PacketBindError` | PASS |
| 114 | E.6 agg | conflicting duplicate packet_id | PBE | `PacketBindError` | PASS |
| 115 | E.6 agg | exact duplicate dedup receipted | OK(receipt) | `OK:{'WOULD_REGRET_MISSING': {'counts': {'A_strict_wins': 1, 'B_strict_wins': 0, 'ties': 0, 'neither': 0, 'prov` | PASS |
| 116 | E.6 agg | provisional exclusion reported | OK(counted) | `OK:{'WOULD_REGRET_MISSING': {'counts': {'A_strict_wins': 0, 'B_strict_wins': 0, 'ties': 0, 'neither': 0, 'prov` | PASS |
| 117 | E.6 agg | three-arm record (pref arm C) | PBE | `PacketBindError` | PASS |
| 118 | E.6 agg | mixed three-arm record SET (B vs C challengers) | PBE? | `OK:{'WOULD_REGRET_MISSING': {'counts': {'A_strict_wins': 2, 'B_strict_wins': 0, 'ties': 0, 'neither': 0, 'prov` | FAIL-SILENT |
| 119 | E.6 eval | challenger_arm==baseline_arm ('A','A') | PBE | `OK:{'evaluator': 'rre_v1', 'amendment': 'ARCHITECT_AMENDMENT_2_BLINDING_AND_ATTRIBUTION_HARDENING', 'generated` | FAIL-SILENT |
| 120 | E.6 eval | record chal==base=='A' pref 'A' (EXTRA) | PBE | `OK:{'evaluator': 'rre_v1', 'amendment': 'ARCHITECT_AMENDMENT_2_BLINDING_AND_ATTRIBUTION_HARDENING', 'generated` | FAIL-SILENT |
| 121 | E.6 eval | judgments_evidence_class invalid str | PBE | `PacketBindError` | PASS |
| 122 | E.6 eval | judgments_evidence_class missing | PBE | `TypeError:evaluate() missing 1 required keyword-only argument: 'judgments_evidence_class'` | PASS |
| 123 | E.6 aggL | missing preferred_list | PBE | `PacketBindError` | PASS |
| 124 | E.6 aggL | unknown preferred_list | PBE | `PacketBindError` | PASS |
| 125 | E.6 aggL | listwise exact dup receipted | OK(receipt) | `OK:{'counts': {'challenger_list_wins': 0, 'baseline_list_wins': 1, 'ties': 0, 'neither': 0, 'provisional_exclu` | PASS |