# -*- coding: utf-8 -*-
import json, re, os, collections

SCRATCH = r"C:\Users\User\AppData\Local\Temp\claude\C--Users-User-Desktop-Adam-lab\b86a0480-30e4-415d-a33f-b78320bfa073\scratchpad"

with open("data/conversations.json", encoding="utf-8") as f:
    data = json.load(f)

# ---------- de-identification ----------
def deidentify(text, name=None):
    if not text:
        return text
    t = text
    t = re.sub(r"[\w.\-+]+@[\w.\-]+\.\w+", "[EMAIL]", t)
    t = re.sub(r"(?<!\d)(?:\+?886|0)9\d{2}[\-\s]?\d{3}[\-\s]?\d{3}(?!\d)", "[PHONE]", t)
    # order-like: digit runs len>=6, or digits+letters
    t = re.sub(r"\b\d{4,}[A-Za-z]{2,}\b", "[ORDER]", t)
    t = re.sub(r"(?<!\d)\d{6,}(?!\d)", "[NUM]", t)
    if name:
        for nm in {name, name.strip()}:
            if nm and len(nm) >= 2:
                t = t.replace(nm, "[NAME]")
    return t.strip()

# ---------- noise detection ----------
NOISE_B2B = ["合作", "邀約", "洽談", "業配", "上架", "異業", "廠商", "代理商", "貴平台",
             "貴公司", "貴司", "推廣合作", "廣告合作", "導流", "聯盟行銷", "分潤合作",
             "我們是一家", "我司", "敝公司", "我們公司想", "尋求合作", "商務合作",
             "行銷合作", "團購合作", "窗口", "承辦", "採購合作", "策略聯盟", "置入"]
NOISE_SPAM = ["加賴", "加line", "投資理財", "博弈", "娛樂城", "微信", "賺錢管道", "包養"]
GREET_ONLY = re.compile(r"^(hi|hello|哈囉|你好|您好|在嗎|請問|嗨|哈嘍|test|測試|安安|有人嗎|你們好)+[\s,.!?！。？~]*$", re.I)

def is_noise(text):
    if not text:
        return True
    tl = text.lower()
    if GREET_ONLY.match(text.strip()):
        return True
    for kw in NOISE_B2B:
        if kw in text:
            return True
    for kw in NOISE_SPAM:
        if kw.lower() in tl:
            return True
    # external promo links (non hiskio)
    if re.search(r"https?://(?!\S*hiskio)", tl) and len(text) < 400 and ("http" in tl and text.count("http") >= 1) and any(k in text for k in ["優惠", "點擊", "免費領", "加入", "報名表單"])==False and ("課程" not in text):
        # be conservative: only flag if looks promo
        pass
    return False

# ---------- topic classification (aligned to 32 KB) ----------
TOPICS = [
    ("付款後課程未開通/找不到課程", ["開通", "沒有開通", "沒開通", "課程沒出現", "課程沒有出現",
        "找不到課程", "課程不見", "課程消失", "沒有課程", "我的學習沒有", "我的課程沒有",
        "付款完成", "付了款", "已付款", "已完成付款", "付完款", "看不到我買的", "買的課程不見",
        "課程沒有顯示", "沒有顯示課程"]),
    ("帳號登入/註冊/密碼", ["登入", "登不進", "登不上", "無法登入", "註冊", "忘記密碼", "重設密碼",
        "改密碼", "變更密碼", "帳號被鎖", "社群登入", "google登入", "facebook登入", "綁定帳號",
        "驗證信", "收不到驗證", "登入方式", "換email", "更改信箱", "帳號", "同一個帳號"]),
    ("退費/退款/錢包", ["退費", "退款", "退錢", "我的錢包", "錢包餘額", "刷退", "退到錢包",
        "申請退款", "可以退嗎", "能退嗎", "退貨"]),
    ("發票/統編/報帳", ["發票", "統編", "統一編號", "報帳", "抬頭", "電子發票", "捐贈發票", "載具"]),
    ("付款方式/繳費問題", ["付款方式", "刷卡", "信用卡", "分期", "atm", "超商", "匯款", "繳費",
        "繳款", "付費", "付不了", "無法付款", "刷不過", "海外刷卡", "付款失敗", "轉帳", "付款代碼",
        "繳費代碼", "付款金額"]),
    ("影片播放問題", ["播放", "卡頓", "緩衝", "黑畫面", "黑屏", "沒有聲音", "沒聲音", "沒畫面",
        "看不了影片", "影片打不開", "影片無法", "畫質", "lag", "頓", "轉圈", "load不出",
        "載入不出", "影片跑不動", "播不出", "看不了"]),
    ("抵用券/優惠/折扣", ["抵用券", "折扣", "優惠碼", "折價券", "折抵", "兌換碼", "序號", "coupon",
        "優惠連結", "優惠價", "折數券", "套用優惠", "沒有折到", "沒折到"]),
    ("課程內容諮詢/是否適合", ["適合", "課程大綱", "難度", "程度", "符合需求", "適不適合",
        "有沒有教", "會教到", "會不會太難", "適合新手", "零基礎", "需要基礎", "課程內容包含",
        "這堂課能", "這門課能", "學得會", "課程是否", "課程有沒有"]),
    ("完課證明/證書", ["完課證明", "完課證書", "結業證書", "上課證明", "證明", "證書", "結業"]),
    ("直播課程/募資課程", ["直播課", "直播課程", "募資", "開課時間", "何時開課", "預計開放",
        "什麼時候開課", "直播連結", "回放", "zoom"]),
    ("電子書", ["電子書", "電子檔", "epub", "電子書下載", "電子書閱讀"]),
    ("課程筆記", ["筆記", "做筆記", "記筆記"]),
    ("課程評價", ["評價", "寫評論", "留評價", "課程評論", "改評價"]),
    ("帳號轉換/合併", ["帳號轉換", "合併帳號", "轉換帳號", "換到另一個帳號", "課程轉移",
        "轉移課程", "併帳"]),
    ("多裝置/共享/被強制登出", ["共享", "多人觀看", "被登出", "強制登出", "兩台", "同時登入",
        "另一台", "多裝置", "一起看", "分享帳號", "重複登入"]),
    ("講師請款/分潤", ["請款", "分潤", "講師收益", "提領", "老師收款", "版稅", "出款", "講師分潤"]),
    ("學習協助/聯繫講師", ["問老師", "聯繫講師", "問題討論", "跟老師", "向老師提問", "課程問題",
        "問講師", "老師回覆"]),
    ("下載/離線觀看", ["下載課程", "離線觀看", "下載影片", "可以下載", "存到電腦", "離線"]),
    ("觀看期限/課程效期", ["觀看期限", "看多久", "永久觀看", "使用期限", "看到什麼時候",
        "限時方案", "效期", "課程期限", "觀看時間限制"]),
    ("聯繫真人客服", ["聯繫客服", "真人客服", "找客服", "轉真人", "人工客服", "跟真人"]),
]

def classify(text):
    if not text:
        return []
    tl = text.lower()
    hits = []
    for name, kws in TOPICS:
        for kw in kws:
            if kw.lower() in tl:
                hits.append(name)
                break
    return hits

# ---------- walk conversations ----------
def msg_text(m):
    if m.get("type") != "text":
        return None
    c = m.get("content")
    if not isinstance(c, str):
        return None
    c = c.strip()
    return c or None

# canned/system bot messages to ignore
BOT_SYSTEM_MARKERS = ["很抱歉，不確定您主要的問題", "您的問題是否得到解決",
    "感謝您的留言", "真人客服團隊將", "非即時回覆", "HiSKIO 感謝您的耐心"]

topic_user_qs = collections.defaultdict(list)      # topic -> [deidentified user question]
topic_human_pairs = collections.defaultdict(list)  # topic -> [(user_q, human_ans)]
topic_primary_count = collections.Counter()
topic_human_count = collections.Counter()

total_user_msgs = 0
noise_user_msgs = 0
useful_user_msgs = 0
human_answers_total = 0

for c in data:
    msgs = c.get("messages") or []
    last_user_q = None
    for m in msgs:
        sender = m.get("sender") or {}
        kind = sender.get("kind")
        txt = msg_text(m)
        if kind == "user":
            if txt is None:
                continue
            total_user_msgs += 1
            de = deidentify(txt, sender.get("name"))
            if is_noise(txt):
                noise_user_msgs += 1
                last_user_q = None
                continue
            useful_user_msgs += 1
            last_user_q = de
            for tp in classify(txt):
                topic_user_qs[tp].append(de)
            prim = classify(txt)
            if prim:
                topic_primary_count[prim[0]] += 1
        elif kind == "human":
            if txt is None:
                continue
            human_answers_total += 1
            de_ans = deidentify(txt, sender.get("name"))
            # attach to last user question topic(s)
            base = last_user_q if last_user_q else ""
            tps = classify(base) or classify(txt)
            for tp in tps:
                topic_human_pairs[tp].append((base, de_ans))
                topic_human_count[tp] += 1
        elif kind == "bot":
            # bot text: skip system markers; otherwise ignore for answers (canned/LLM)
            continue

# ---------- dedup helpers ----------
def norm(s):
    return re.sub(r"\s+", "", s).lower()

def top_unique(items, limit, minlen=4, maxlen=120):
    seen = set()
    out = []
    # sort by frequency of normalized form
    freq = collections.Counter(norm(x) for x in items)
    ordered = sorted(items, key=lambda x: (-freq[norm(x)], len(x)))
    for x in ordered:
        n = norm(x)
        if n in seen:
            continue
        if len(x) < minlen or len(x) > maxlen:
            continue
        seen.add(n)
        out.append(x)
        if len(out) >= limit:
            break
    return out

def top_answers(pairs, limit, minlen=15, maxlen=500):
    seen = set()
    out = []
    freq = collections.Counter(norm(a) for _, a in pairs)
    ordered = sorted(pairs, key=lambda pa: (-freq[norm(pa[1])], len(pa[1])))
    for q, a in ordered:
        n = norm(a)[:60]
        if n in seen:
            continue
        if len(a) < minlen or len(a) > maxlen:
            continue
        # skip generic "同學好" only openers
        seen.add(n)
        out.append({"q": q[:120], "a": a[:maxlen]})
        if len(out) >= limit:
            break
    return out

# ---------- build candidate + report ----------
candidates = []
report = []
report.append(f"總對話數: {len(data)}")
report.append(f"客人文字訊息(user/text): {total_user_msgs}  其中雜訊過濾: {noise_user_msgs}  留用: {useful_user_msgs}")
report.append(f"真人客服文字回覆(human/text)總數: {human_answers_total}")
report.append("")
report.append("=== 主題統計（依客人提問數排序）===")

for tp, cnt in topic_primary_count.most_common():
    hc = topic_human_count.get(tp, 0)
    report.append(f"\n## {tp}  | 客人提問(primary)={cnt} | 有真人回覆配對={hc}")
    qs = top_unique(topic_user_qs[tp], 10)
    report.append("  代表客人問法:")
    for q in qs:
        report.append(f"    - {q}")
    ans = top_answers(topic_human_pairs[tp], 4)
    if ans:
        report.append("  代表真人客服回覆:")
        for a in ans:
            report.append(f"    Q: {a['q']}")
            report.append(f"    A: {a['a']}")
    candidates.append({
        "topic": tp,
        "user_question_count": cnt,
        "human_answer_count": hc,
        "representative_questions": qs,
        "representative_human_answers": [a["a"] for a in ans],
        "sample_pairs": ans,
    })

with open(os.path.join(SCRATCH, "faq_candidates.json"), "w", encoding="utf-8") as f:
    json.dump(candidates, f, ensure_ascii=False, indent=2)

with open(os.path.join(SCRATCH, "faq_report.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(report))

print("\n".join(report[:6]))
print("\n--- topic table ---")
for tp, cnt in topic_primary_count.most_common():
    print(f"{cnt:4d}  human={topic_human_count.get(tp,0):3d}  {tp}")
print("\ncandidates ->", os.path.join(SCRATCH, "faq_candidates.json"))
print("report ->", os.path.join(SCRATCH, "faq_report.txt"))
