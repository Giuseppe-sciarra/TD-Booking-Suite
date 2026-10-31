"""2.14.0: locale/*/LC_MESSAGES/django.po -> django.mo without GNU gettext (not in the Docker image nor on Windows).
Only the plain form the catalogs use: one-line msgid "..." / msgstr "..." pairs, JSON-style escapes."""
import json,struct,sys
from pathlib import Path
def compile_po(po):
    pairs={};key=None
    for line in po.read_text(encoding="utf-8").splitlines():
        if line.startswith("msgid "):key=json.loads(line[6:])
        elif line.startswith("msgstr "):pairs[key]=json.loads(line[7:])
        elif line.startswith('"'):pairs[key]+=json.loads(line)  # the header's lines (charset)
    keys=sorted(pairs);ids=b"";strs=b"";offsets=[]
    for k in keys:
        a,b=k.encode(),pairs[k].encode();offsets.append((len(ids),len(a),len(strs),len(b)));ids+=a+b"\0";strs+=b+b"\0"
    start=7*4+16*len(keys);vals=start+len(ids)
    table=[x for o1,l1,_,_ in offsets for x in (l1,start+o1)]+[x for _,_,o2,l2 in offsets for x in (l2,vals+o2)]
    po.with_suffix(".mo").write_bytes(struct.pack("Iiiiiii",0x950412de,0,len(keys),28,28+8*len(keys),0,0)+struct.pack(f"{len(table)}i",*table)+ids+strs)
if __name__=="__main__":
    for po in Path(sys.argv[1] if len(sys.argv)>1 else Path(__file__).resolve().parent.parent/"locale").glob("*/LC_MESSAGES/django.po"):compile_po(po);print(po.with_suffix(".mo"))
