# -*- coding: utf-8 -*-
import json, re, os, glob

errs = []

# 1. JSON valid
with open("data/kb_index.json", encoding="utf-8") as f:
    kbi = json.load(f)
with open("data/faq.json", encoding="utf-8") as f:
    faq = json.load(f)

# 2. kb_index structure + matches kb files
kb_files = sorted(glob.glob("data/kb/kb_*.md"))
print("kb md files:", len(kb_files), "| kb_index entries:", len(kbi), "| faq entries:", len(faq))

ids_idx = [e["id"] for e in kbi]
if len(set(ids_idx)) != len(ids_idx):
    errs.append("duplicate kb ids in index")

for e in kbi:
    for k in ("id","title","category","summary","key_questions"):
        if k not in e or not e[k]:
            errs.append(f"{e.get('id')} missing {k}")
    if not (3 <= len(e["key_questions"]) <= 5):
        errs.append(f"{e['id']} key_questions count = {len(e['key_questions'])}")
    # kb file exists and frontmatter id/title/category match
    p = f"data/kb/{e['id']}.md"
    if not os.path.exists(p):
        errs.append(f"missing file {p}")
        continue
    txt = open(p, encoding="utf-8").read()
    if f"id: {e['id']}" not in txt: errs.append(f"{e['id']} frontmatter id mismatch")
    if f"title: {e['title']}" not in txt: errs.append(f"{e['id']} frontmatter title mismatch")
    if f"category: {e['category']}" not in txt: errs.append(f"{e['id']} frontmatter category mismatch")
    if "last_updated: 2026-07-04" not in txt: errs.append(f"{e['id']} last_updated wrong")
    if "有關的文章：" in txt: errs.append(f"{e['id']} breadcrumb NOT removed")

# 3. faq structure
for e in faq:
    for k in ("id","category","question_patterns","core_steps","fallback_message"):
        if k not in e or not e[k]:
            errs.append(f"{e.get('id')} missing {k}")
    n = len(e["question_patterns"])
    if not (5 <= n <= 8):
        errs.append(f"{e['id']} question_patterns count = {n}")

# 4. forbidden words for outward text (工單/建單) across faq
forbidden = ["工單","建單","開單","tickets","ticket"]
allcorpus = json.dumps(faq, ensure_ascii=False) + json.dumps(kbi, ensure_ascii=False)
for w in forbidden:
    if w in allcorpus:
        errs.append(f"forbidden outward word found: {w}")

# 5. must contain 真人客服 in every faq fallback
for e in faq:
    if "真人客服" not in e["fallback_message"]:
        errs.append(f"{e['id']} fallback missing 真人客服")

# 6. PII scan across faq + kb_index (author-controlled files)
email_re = re.compile(r"[\w.\-+]+@[\w.\-]+\.\w+")
phone_re = re.compile(r"(?<!\d)09\d{8}(?!\d)")
# allow official hiskio emails/urls only
def pii_scan(obj, where):
    s = json.dumps(obj, ensure_ascii=False)
    for m in set(email_re.findall(s)):
        if not m.lower().endswith("hiskio.com"):
            errs.append(f"PII email in {where}: {m}")
    for m in set(phone_re.findall(s)):
        errs.append(f"PII phone in {where}: {m}")
pii_scan(faq, "faq.json")
pii_scan(kbi, "kb_index.json")

# 7. category values sanity
cats = set(e["category"] for e in kbi) | set(e["category"] for e in faq)
print("categories in use:", cats)

if errs:
    print("\n!!! ISSUES:")
    for e in errs:
        print("  -", e)
else:
    print("\nALL CHECKS PASSED")

# quick counts
from collections import Counter
print("\nKB category dist:", Counter(e["category"] for e in kbi))
print("FAQ category dist:", Counter(e["category"] for e in faq))
