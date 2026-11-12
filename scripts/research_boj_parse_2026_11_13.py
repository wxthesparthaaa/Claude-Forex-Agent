"""Parser for the BoJ monthly market-operation files used by scripts/research_boj_rinban_cover_2026_11_13.py (written before any outcome).
Input: /tmp/research/boj/*.xlsx and /tmp/research/bojx/*.xls (unzipped). Output: /tmp/research/boj_ops.json. Needs openpyxl and xlrd."""
import glob, os, json, datetime as dt, openpyxl, xlrd, re
def rows_of(path):
    if path.endswith('xlsx'):
        wb=openpyxl.load_workbook(path,read_only=True,data_only=True)
        for ws in wb.worksheets:
            for r in ws.iter_rows(values_only=True): yield list(r)
    else:
        wb=xlrd.open_workbook(path)
        for ws in wb.sheets():
            for i in range(ws.nrows):
                out=[]
                for c in ws.row(i):
                    if c.ctype==xlrd.XL_CELL_DATE: out.append(xlrd.xldate.xldate_as_datetime(c.value,wb.datemode))
                    else: out.append(c.value)
                yield out
ops=[]
files=sorted(glob.glob('/tmp/research/boj/*.xlsx'))+sorted(glob.glob('/tmp/research/bojx/*.xls'))
for f in files:
    insec=False
    for r in rows_of(f):
        vals=[x for x in r if x not in (None,'')]
        if not vals: continue
        s=' '.join(str(x) for x in vals)
        if isinstance(vals[0],dt.datetime) and len(vals)>=6 and isinstance(vals[1],str) and vals[1].strip().startswith('Outright Purchases of JGBs') and 'Floating' not in vals[1] and 'Inflation' not in vals[1] and isinstance(vals[2],dt.datetime):
            nums=[x for x in vals[3:] if isinstance(x,(int,float))]
            if len(nums)>=3:
                ops.append(dict(offer=vals[0].strftime('%Y-%m-%d'),offered=nums[0],bids=nums[1],succ=nums[2],bucket=vals[1].strip()[27:],file=os.path.basename(f)))
            continue
        if 'Outright Purchases of' in s and ('JGB' in s or 'Government Bond' in s):
            insec = ('Excluding' in s) or ('Floating' not in s and 'Inflation' not in s and 'Treasury Discount' not in s and 'Bills' not in s)
            continue
        if s.startswith('■') or (('Outright' in s or 'Operations' in s or 'Purchases' in s) and 'Market Operations by' not in s and not isinstance(vals[0],dt.datetime)):
            if s.startswith('■'): insec=False
            continue
        if insec and isinstance(vals[0],dt.datetime) and len(vals)>=5 and isinstance(vals[1],dt.datetime):
            nums=[x for x in vals[2:] if isinstance(x,(int,float))]
            if len(nums)>=3:
                ops.append(dict(offer=vals[0].strftime('%Y-%m-%d'),offered=nums[0],bids=nums[1],succ=nums[2],bucket=str(vals[-1])[:40],file=os.path.basename(f)))
json.dump(ops,open('/tmp/research/boj_ops.json','w'))
from collections import Counter
print(len(ops)); print(sorted(Counter(o['offer'][:4] for o in ops).items()))
print(Counter(o['bucket'] for o in ops).most_common(15))
