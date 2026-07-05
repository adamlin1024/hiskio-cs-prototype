import json, os, re, io

ROOT = "data/kb_source/crisp"
OUT_KB = "data/kb"
SCRATCH = r"C:\Users\User\AppData\Local\Temp\claude\C--Users-User-Desktop-Adam-lab\b86a0480-30e4-415d-a33f-b78320bfa073\scratchpad"

with open(os.path.join(ROOT, "_index.json"), encoding="utf-8") as f:
    idx = json.load(f)

articles = idx["articles"]
print("articles:", len(articles))

def split_frontmatter(text):
    # text starts with ---\n ... \n---\n
    if text.startswith("---"):
        parts = text.split("---", 2)
        # parts[0]="" parts[1]=frontmatter parts[2]=body
        fm = parts[1]
        body = parts[2]
        return fm, body
    return "", text

def clean_body(body):
    lines = body.split("\n")
    out = []
    for ln in lines:
        if ln.strip().startswith("有關的文章："):
            continue
        out.append(ln)
    # collapse leading blank lines
    txt = "\n".join(out)
    txt = txt.lstrip("\n")
    # collapse 3+ blank lines to 2 (source has lots of double-space blank lines)
    txt = re.sub(r"\n[ \t]+\n", "\n\n", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return txt.rstrip() + "\n"

mapping_lines = ["# KB Mapping", "",
    "記錄 `data/kb/kb_0NN.md`（系統檔）對應的 Crisp 原始文章。",
    "由本次重建（2026-07-04）以真實 Crisp 幫助文章建立。", "",
    "| id | title | category | crisp_id | source_url |",
    "|----|-------|----------|----------|------------|"]

digest = []
for i, a in enumerate(articles, 1):
    kid = f"kb_{i:03d}"
    apath = os.path.join(ROOT, a["path"], "article.md")
    with open(apath, encoding="utf-8") as f:
        raw = f.read()
    fm, body = split_frontmatter(raw)
    title = a["title"]
    category = a["category"]
    cleaned = clean_body(body)

    new_fm = (
        f"---\n"
        f"id: {kid}\n"
        f"title: {title}\n"
        f"category: {category}\n"
        f"last_updated: 2026-07-04\n"
        f"---\n\n"
    )
    out_text = new_fm + cleaned
    with open(os.path.join(OUT_KB, f"{kid}.md"), "w", encoding="utf-8") as f:
        f.write(out_text)

    mapping_lines.append(f"| {kid} | {title} | {category} | {a['crisp_id']} | {a['public_url']} |")

    # digest for authoring index: title, category, body text (strip images/links markup lightly)
    plain = re.sub(r"!\[\]\([^)]*\)", "", cleaned)   # drop images
    plain = re.sub(r"\n{2,}", "\n", plain).strip()
    digest.append(f"### {kid} | {category} | {title}\n{plain}\n")

with open("data/kb_mapping.md", "w", encoding="utf-8") as f:
    f.write("\n".join(mapping_lines) + "\n")

with io.open(os.path.join(SCRATCH, "kb_digest.md"), "w", encoding="utf-8") as f:
    f.write("\n\n".join(digest))

print("KB files + kb_mapping.md written. digest ->", os.path.join(SCRATCH, "kb_digest.md"))
# category distribution
from collections import Counter
print("category dist:", Counter(a["category"] for a in articles))
