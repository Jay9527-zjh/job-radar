from pathlib import Path
from datetime import datetime, date
from urllib.parse import urljoin, urlparse
import hashlib
import json
import re
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
SOURCES_PATH = ROOT / "sources.json"
JOBS_PATH = ROOT / "jobs.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 Chrome/140 Safari/537.36"
}

# Only titles that look like real recruitment notices are accepted.
GOOD_PHRASES = (
    "招聘启事", "招聘公告", "招聘计划", "校园招聘", "招聘简章",
    "招聘信息", "招聘启动", "招聘正式启动", "招聘重磅开启",
    "公开招聘", "招聘岗位", "诚聘", "招聘需求", "人才招聘计划"
)

# Navigation / admissions / publicity items that are NOT job postings.
BAD_PHRASES = (
    "人才概况", "人才队伍", "专家队伍", "人才梯队", "领军人才",
    "正高级岗位", "副高级岗位", "高级岗位",
    "招聘网站", "招聘官网", "招聘及公告", "人才招聘公告",
    "人力资源", "招生简章", "招生信息", "研究生招生", "博士研究生招生",
    "硕士研究生招生", "复试", "录取", "拟录用", "拟聘", "公示名单",
    "接收公示", "落户公示", "采购", "招标", "寻源公告", "中标",
    "创新团队拟入选公示", "单项冠军公示", "白皮书"
)

TARGET_WORDS = (
    "博士", "应届", "2027", "航空发动机", "燃气轮机",
    "动力工程", "工程热物理", "流体", "机械", "叶轮机械",
    "转子", "密封", "摩擦", "轴承", "可靠性", "航空航天",
    "科研", "研发", "结构", "控制", "航天科工", "航天科技",
    "导弹", "制导", "总体", "气动", "飞行控制", "卫星", "火箭",
    "防御技术", "飞航", "运载", "仿真", "热控", "电气"
)

STRONG_TARGET_WORDS = tuple(
    w for w in TARGET_WORDS
    if w not in ("科研", "应届", "2027")
)

FIELD_MAP = {
    "国防军工": ("航天科工", "航天科技", "导弹", "制导", "防御技术", "飞航", "运载", "军工"),
    "航空航天": ("航空", "航天", "航空发动机", "燃气轮机", "飞行器", "高超声速"),
    "能源动力": ("动力工程", "工程热物理", "能源", "燃烧", "叶轮机械", "涡轮", "压缩机", "储能"),
    "机械装备": ("机械", "装备", "转子", "密封", "摩擦", "轴承", "可靠性", "结构", "制造"),
    "电子半导体": ("电子", "半导体", "芯片", "集成电路", "雷达", "通信", "微电子")
}

BEIJING_WORDS = (
    "北京市", "北京海淀", "北京朝阳", "北京丰台", "北京昌平",
    "北京大兴", "北京顺义", "北京怀柔", "北京石景山", "北京"
)

LONG_TERM_PHRASES = ("长期有效", "长期招聘", "常年招聘")

UNSUPPORTED_URL_EXTENSIONS = (
    ".doc", ".docx", ".pdf", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".rar", ".7z", ".jpg", ".jpeg", ".png", ".gif"
)

OFF_TARGET_TITLE_PHRASES = (
    "驾驶员", "司机", "财务助理", "财务管理", "基建财务", "财务处",
    "综合事务", "办公室主任", "纪监审", "宣传主管", "中层领导",
    "领导人员", "管理岗位人员", "劳务派遣"
)

TODAY = date.today()

def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()

def normalize_spaced_digits(s):
    # Fix OCR/web formatting such as "2 0 26年4月3 0日".
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r"(?<=\d)\s+(?=\d)", "", s)
    return s

def get(url, timeout=22):
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding
    return r.text

def is_real_job_title(title):
    t = clean(title)
    if not t or len(t) < 6:
        return False
    if any(x in t for x in BAD_PHRASES):
        return False
    if not any(x in t for x in GOOD_PHRASES):
        return False
    # Avoid a generic navigation tab named only "招聘信息"/"人才招聘".
    if t in ("招聘", "招聘信息", "人才招聘", "校园招聘", "公开招聘"):
        return False
    return True

def strip_page_noise(soup):
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "form"]):
        tag.decompose()
    # Common sidebars / menus.
    for sel in (
        ".nav", ".menu", ".sidebar", ".side", ".footer", ".header",
        ".crumb", ".breadcrumb", ".location", ".channel", ".left"
    ):
        for node in soup.select(sel):
            try:
                node.decompose()
            except Exception:
                pass
    return soup

def article_text(html):
    soup = BeautifulSoup(html, "lxml")
    soup = strip_page_noise(soup)

    # Prefer likely article bodies.
    selectors = (
        "article", ".TRS_Editor", ".article-content", ".article_content",
        ".content-detail", ".news-content", ".news_content",
        ".detail-content", ".detail", "#zoom", ".zw"
    )
    candidates = []
    for sel in selectors:
        for node in soup.select(sel):
            txt = clean(node.get_text(" ", strip=True))
            if len(txt) >= 100:
                candidates.append(txt)
    if candidates:
        # Largest candidate is usually the actual notice body.
        return normalize_spaced_digits(max(candidates, key=len))

    body = soup.body or soup
    return normalize_spaced_digits(clean(body.get_text(" ", strip=True)))

def find_article_title(html, fallback):
    soup = BeautifulSoup(html, "lxml")
    for tag in ("h1", "h2"):
        node = soup.find(tag)
        if node:
            t = clean(node.get_text(" ", strip=True))
            if is_real_job_title(t):
                return t
    return clean(fallback)

def parse_iso(y, m, d):
    try:
        return date(int(y), int(m), int(d))
    except Exception:
        return None

def parse_date_from_title_or_url(title, url):
    for s in (title or "", url or ""):
        for p in (
            r"(20\d{2})[年/\-.](\d{1,2})[月/\-.](\d{1,2})",
            r"(20\d{2})(\d{2})(\d{2})",
        ):
            m = re.search(p, s)
            if m:
                d = parse_iso(*m.groups())
                if d:
                    return d
    return None

def extract_publish_date(text, fallback_title=""):
    s = normalize_spaced_digits(fallback_title + " " + text[:1800])
    patterns = (
        r"发布时间[：:\s]*(20\d{2})[年/\-.](\d{1,2})[月/\-.](\d{1,2})",
        r"时间[：:\s]*(20\d{2})[年/\-.](\d{1,2})[月/\-.](\d{1,2})",
        r"(20\d{2})[年/\-.](\d{1,2})[月/\-.](\d{1,2})"
    )
    for p in patterns:
        m = re.search(p, s)
        if m:
            d = parse_iso(*m.groups())
            if d:
                return d
    return None

def extract_deadline(text):
    s = normalize_spaced_digits(text)
    if "长期有效" in s or "长期招聘" in s or "常年招聘" in s:
        return None, True

    patterns = (
        r"(?:简历接收截止日期|报名截止日期|报名截止时间|截止日期|截止时间|报名截止)"
        r"[：:\s，,]*?(20\d{2})[年/\-.](\d{1,2})[月/\-.](\d{1,2})",
        r"(?:截至|截止至)[：:\s，,]*?(20\d{2})[年/\-.](\d{1,2})[月/\-.](\d{1,2})"
    )
    for p in patterns:
        m = re.search(p, s)
        if m:
            d = parse_iso(*m.groups())
            if d:
                return d, False
    return None, False

def is_long_term_notice(title, text):
    title = clean(title)
    if any(x in title for x in LONG_TERM_PHRASES):
        return True

    # Body text can contain unrelated links/sidebar snippets, so only trust
    # long-term wording near the top of the article.
    head = clean(text)[:1200]
    return any(x in head for x in LONG_TERM_PHRASES)

def is_stale_notice(title, published, long_term):
    if not published:
        return False
    if (TODAY - published).days <= 550:
        return False
    # Keep old pages only when their own title clearly says the opportunity is
    # long-running. This avoids stale yearly notices surviving because a page
    # sidebar mentions "长期招聘".
    return not (long_term and any(x in title for x in LONG_TERM_PHRASES))

def is_supported_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    if "http://" in parsed.path or "https://" in parsed.path:
        return False
    path = parsed.path.lower()
    if "/channels/" in path:
        return False
    return not path.endswith(UNSUPPORTED_URL_EXTENSIONS)

def is_target_relevant(title, text, degree):
    title = clean(title)
    hay = title + " " + clean(text[:2500])

    if any(x in title for x in OFF_TARGET_TITLE_PHRASES):
        return False

    # Match the user's goal: Beijing/official research-heavy roles around PhD,
    # aerospace, energy power, machinery, controls, and similar R&D tracks.
    if "博士" in degree or "博士" in hay:
        return True
    return any(w in hay for w in STRONG_TARGET_WORDS)

def is_obsolete_campus(title):
    # By late summer 2026, older campus cohorts are stale; keep 2027届 and later.
    m = re.search(r"(20\d{2})(?:届|年度).*校园招聘", title)
    if not m:
        return False
    cohort = int(m.group(1))
    if cohort <= TODAY.year and TODAY.month >= 7:
        return True
    return False

def infer_location(text, default_location):
    s = normalize_spaced_digits(text)
    # Look first around explicit work-location labels.
    m = re.search(r"(?:工作地点|工作地址|工作地)[：:\s]*([^。；;\n]{1,60})", s)
    if m:
        loc_line = m.group(1)
        if any(w in loc_line for w in BEIJING_WORDS):
            return "北京"
        if "全国" in loc_line or "多地" in loc_line:
            return "全国"
    return default_location or "待确认"

def infer_location_from_short_text(text, default_location):
    s = clean(text)
    if any(w in s for w in BEIJING_WORDS):
        return "北京"
    m = re.search(r"((?:北京|上海|天津|重庆|深圳|广州|武汉|西安|成都|南京|无锡|长沙|柳州|保定|呼和浩特|孝感|宜昌)[^，,；;|\s]*)", s)
    if m:
        return m.group(1)
    return default_location or "待确认"

def infer_degree(text, default_degree):
    s = text
    # Use recruiting-specific wording, not the institute's degree-granting background.
    near = []
    for key in ("招聘对象", "学历要求", "任职要求", "应聘条件", "招聘范围"):
        pos = s.find(key)
        if pos >= 0:
            near.append(s[pos:pos+900])
    target = " ".join(near) if near else s[:2000]

    has_phd = any(x in target for x in ("博士、博士后", "博士/博士后", "博士学位", "应届博士", "博士毕业"))
    has_master = any(x in target for x in ("硕士", "研究生"))
    if has_phd and has_master:
        return "博士/硕士"
    if has_phd:
        return "博士"
    if has_master:
        return "硕士及以上"
    return default_degree or "学历见公告"

def infer_industry(text, fallback):
    best, n = fallback or "综合", 0
    for name, words in FIELD_MAP.items():
        val = sum(text.count(w) for w in words)
        if val > n:
            best, n = name, val
    return best

def infer_apply_url(html, page_url, source):
    soup = BeautifulSoup(html, "lxml")
    preferred_hosts = ("hotjob.cn", "zhiye.com", "iguopin.com", "campus.iguopin.com")
    for a in soup.find_all("a", href=True):
        href = urljoin(page_url, a["href"])
        host = urlparse(href).netloc.lower()
        if any(h in host for h in preferred_hosts):
            return href
    return source.get("apply_url") or page_url

def extract_jgrc_date(text):
    m = re.search(r"发布时间[：:\s]*(20\d{2})-(\d{1,2})-(\d{1,2})", text)
    if m:
        return parse_iso(*m.groups())
    return None

def make_summary(text):
    s = clean(text)
    # Start from a useful recruitment section when possible.
    for key in ("招聘对象", "招聘岗位", "需求专业", "招聘范围", "岗位职责", "任职要求"):
        pos = s.find(key)
        if pos >= 0:
            return s[pos:pos+260]
    return s[:260]

def score_job(title, text, location, degree, org_type, industry, published):
    s = 30
    hay = title + " " + text
    if location == "北京":
        s += 20
    elif location == "全国":
        s += 8

    if "博士" in degree:
        s += 18
    if org_type in ("央企", "国企", "科研院所", "事业单位"):
        s += 7
    if industry in ("国防军工", "航空航天", "能源动力", "机械装备"):
        s += 8
    if "2027届" in title:
        s += 12
    if "应届" in hay:
        s += 5

    s += min(16, 2 * sum(1 for w in TARGET_WORDS if w in hay))

    if published:
        age = (TODAY - published).days
        if age <= 30:
            s += 6
        elif age <= 90:
            s += 3

    return min(99, s)

def make_id(url):
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]

def crawl_jgrc_source(source):
    jobs = []
    try:
        html = get(source["url"])
    except Exception as e:
        print("INDEX FAILED:", source["name"], e)
        return jobs

    soup = BeautifulSoup(html, "lxml")
    for item in soup.select(".position-item"):
        company_node = item.select_one(".e-company .txt")
        title_node = item.select_one(".e-basic a[href]")
        if not company_node or not title_node:
            continue

        company = clean(company_node.get_text(" ", strip=True))
        title = clean(title_node.get_text(" ", strip=True))
        detail_url = urljoin(source["url"], title_node["href"])
        extra = clean(item.get_text(" ", strip=True))
        hay = company + " " + title + " " + extra

        if not is_target_relevant(title, hay, source.get("default_degree", "")):
            continue

        published = extract_jgrc_date(extra)
        location = infer_location_from_short_text(extra, source.get("default_location"))
        degree = "博士" if "博士" in hay else ("硕士及以上" if "硕士" in hay else source.get("default_degree", "学历见公告"))
        industry = infer_industry(hay, source.get("industry"))
        score = score_job(title, hay, location, degree, source.get("org_type", ""), industry, published)

        jobs.append({
            "id": make_id(detail_url),
            "company": company,
            "title": title,
            "location": location,
            "org_type": source.get("org_type", ""),
            "industry": industry,
            "degree": degree,
            "major": "",
            "summary": extra[:260],
            "updated": (published or TODAY).isoformat(),
            "deadline": "",
            "score": score,
            "source_name": source["name"],
            "url": detail_url,
            "apply_url": detail_url,
            "tags": [w for w in TARGET_WORDS if w in hay][:10]
        })

        if len(jobs) >= int(source.get("max_links", 30)):
            break

    print(source["name"], "candidate jobs:", len(jobs))
    return jobs

def extract_links(source):
    html = get(source["url"])
    soup = BeautifulSoup(html, "lxml")
    found = []
    seen = set()

    for a in soup.find_all("a", href=True):
        title = clean(a.get_text(" ", strip=True))
        if not is_real_job_title(title):
            continue

        url = urljoin(source["url"], a["href"]).split("#")[0]
        if not is_supported_url(url):
            continue
        if url in seen:
            continue

        seen.add(url)
        found.append((title, url))
        if len(found) >= int(source.get("max_links", 30)):
            break
    return found

def crawl_source(source):
    if source.get("parser") == "jgrc":
        return crawl_jgrc_source(source)

    jobs = []
    try:
        links = extract_links(source)
    except Exception as e:
        print("INDEX FAILED:", source["name"], e)
        return jobs

    print(source["name"], "candidate links:", len(links))

    for link_title, url in links:
        if is_obsolete_campus(link_title):
            continue

        try:
            html = get(url)
            title = find_article_title(html, link_title)
            if not is_real_job_title(title):
                continue

            text = article_text(html)
            published = (
                parse_date_from_title_or_url(title, url)
                or extract_publish_date(text, title)
            )
            deadline, deadline_long_term = extract_deadline(text)
            long_term = deadline_long_term or is_long_term_notice(title, text)

            # Explicitly expired.
            if deadline and deadline < TODAY:
                continue

            # Very old notices are not useful unless their title explicitly
            # marks the opportunity as long-running.
            if is_stale_notice(title, published, long_term):
                continue

            # Old campus cohorts are discarded even if deadline was not parsed.
            if is_obsolete_campus(title):
                continue

            location = infer_location(text, source.get("default_location"))
            degree = infer_degree(text, source.get("default_degree"))
            if not is_target_relevant(title, text, degree):
                continue

            industry = infer_industry(text, source.get("industry"))
            score = score_job(
                title, text, location, degree,
                source.get("org_type", ""), industry, published
            )
            apply_url = infer_apply_url(html, url, source)

            jobs.append({
                "id": make_id(url),
                "company": source["name"],
                "title": title[:120],
                "location": location,
                "org_type": source.get("org_type", ""),
                "industry": industry,
                "degree": degree,
                "major": "",
                "summary": make_summary(text),
                "updated": (published or TODAY).isoformat(),
                "deadline": deadline.isoformat() if deadline else ("长期有效" if long_term else ""),
                "score": score,
                "source_name": source["name"] + "官网",
                "url": url,
                "apply_url": apply_url,
                "tags": [w for w in TARGET_WORDS if w in (title + " " + text)][:10]
            })
        except Exception as e:
            print("PAGE FAILED:", url, e)

    return jobs

def pinned_jobs():
    def lead(company, title, location, industry, degree, summary, updated, url, apply_url, tags, score=99):
        return {
            "id": make_id(url + title),
            "company": company,
            "title": title,
            "location": location,
            "org_type": "央企" if "中国科学院" not in company else "科研院所",
            "industry": industry,
            "degree": degree,
            "major": "",
            "summary": summary,
            "updated": updated,
            "deadline": "",
            "score": score,
            "source_name": "官方招聘入口/公开公告",
            "url": url,
            "apply_url": apply_url,
            "tags": tags[:10]
        }

    return [
        lead(
            "中国航天科工集团有限公司",
            "中国航天科工集团有限公司2027届校园招聘（651个岗位）",
            "北京/全国",
            "国防军工",
            "硕士/博士",
            "内置浏览器核验：航天科工2027届校园招聘官网当前展示651个岗位，覆盖中国航天科工防御技术研究院、飞航技术研究院、运载技术研究院、动力技术研究院等重点单位。",
            "2026-08-27",
            "https://casicjob.iguopin.com/job",
            "https://casicjob.iguopin.com/job",
            ["航天科工", "2027", "硕士", "博士", "导弹", "制导", "总体", "结构", "仿真", "控制"]
        ),
        lead(
            "中国航天科工防御技术研究院（二院）",
            "中国航天科工防御技术研究院（二院）2027届校园招聘入口",
            "北京/多地",
            "国防军工",
            "硕士/博士",
            "航天科工二院为防御技术总体研究院，已出现在航天科工2027届校园招聘官网招聘单位池中，适合关注总体、控制、电子、制导、仿真、软件与测试方向。",
            "2026-08-27",
            "https://casicjob.iguopin.com/job",
            "https://casicjob.iguopin.com/job",
            ["航天科工", "防御技术", "二院", "2027", "硕士", "博士", "制导", "控制", "仿真"]
        ),
        lead(
            "中国航天科工飞航技术研究院（三院）",
            "中国航天科工飞航技术研究院（三院）2027届校园招聘入口",
            "北京/多地",
            "国防军工",
            "硕士/博士",
            "航天科工三院已出现在航天科工2027届校园招聘官网招聘单位池中，重点关注飞航、总体、气动、结构、动力、控制、电子信息和软件研发岗位。",
            "2026-08-27",
            "https://casicjob.iguopin.com/job",
            "https://casicjob.iguopin.com/job",
            ["航天科工", "飞航", "三院", "2027", "硕士", "博士", "气动", "结构", "控制"]
        ),
        lead(
            "中国航天科工信息技术研究院（一院）",
            "中国航天科工信息技术研究院（一院）2027届校园招聘关注入口",
            "北京/全国",
            "国防军工",
            "硕士/博士",
            "航天科工一院属于航天科工重点研究院体系，建议通过航天科工招聘官网持续检索信息技术、软件、网络安全、电子信息、智能化系统等方向。",
            "2026-08-27",
            "https://casicjob.iguopin.com/job",
            "https://casicjob.iguopin.com/job",
            ["航天科工", "一院", "2027", "硕士", "博士", "软件", "电子", "控制"]
        ),
        lead(
            "中国航天科工运载技术研究院",
            "中国航天科工运载技术研究院2027届校园招聘入口",
            "湖北/北京/全国",
            "国防军工",
            "硕士/博士",
            "航天科工运载技术研究院已出现在航天科工2027届校园招聘官网招聘单位池中，适合关注运载、动力、结构强度、仿真、热控、电气与试验方向。",
            "2026-08-27",
            "https://casicjob.iguopin.com/job",
            "https://casicjob.iguopin.com/job",
            ["航天科工", "运载", "2027", "硕士", "博士", "动力工程", "结构", "热控", "电气"]
        ),
        lead(
            "中国航天科技集团有限公司",
            "中国航天科技集团有限公司2027届校园招聘岗位入口",
            "北京/全国",
            "国防军工",
            "硕士/博士",
            "内置浏览器核验：航天科技集团国聘招聘页已展示2027校园招聘岗位，含北京神舟航天软件、上海航天设备制造总厂等单位。",
            "2026-08-27",
            "https://spacechina.iguopin.com/job-campus",
            "https://spacechina.iguopin.com/job-campus",
            ["航天科技", "2027", "硕士", "博士", "火箭", "卫星", "机械", "智能制造"]
        ),
        lead(
            "中国运载火箭技术研究院（航天一院）",
            "中国运载火箭技术研究院（航天一院）2027届校园招聘关注入口",
            "北京",
            "国防军工",
            "硕士/博士",
            "航天一院是航天科技集团第一研究院，建议通过航天科技集团人才招聘平台和航天一院官网持续关注火箭总体、动力、结构、制导控制、可靠性等方向。",
            "2026-08-27",
            "https://www.calt.com/",
            "https://spacechina.iguopin.com/job-campus",
            ["航天科技", "航天一院", "火箭", "2027", "硕士", "博士", "动力工程", "结构", "控制"]
        ),
        lead(
            "中国科学院空间应用工程与技术中心",
            "中国科学院空间应用工程与技术中心2027届校园招聘启事",
            "北京",
            "航空航天",
            "硕士/博士",
            "空间应用中心2027届校园招聘面向高校毕业生，官方公告显示报名截至2026年12月31日，岗位需求详见附件和招聘平台。",
            "2026-04-21",
            "https://csu.cas.cn/gb/yjdw/rczp/202604/t20260421_8188165.html",
            "https://csu.zhiye.com/AllJob",
            ["中国科学院", "空间应用", "2027", "硕士", "博士", "航天", "科研"]
        ),
        lead(
            "中国航空发动机集团有限公司",
            "中国航空发动机集团有限公司2027届校园招聘重磅开启",
            "北京/全国",
            "航空航天",
            "本科/硕士/博士",
            "中国航发2027届校园招聘已于2026年7月30日正式发布，重点关注航空发动机、燃气轮机、动力工程、机械、材料及研发类岗位。",
            "2026-07-30",
            "https://www.aecc.cn/aecc/gggs/2026073017062513709/index.html",
            "https://aecc.iguopin.com/",
            ["2027", "航空发动机", "燃气轮机", "动力工程", "机械", "研发"]
        ),
    ]

def main():
    config = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))

    # IMPORTANT: rebuild from scratch on every run.
    # Do NOT merge the old jobs.json, otherwise V1's false positives survive forever.
    by_id = {}

    for source in config["sources"]:
        for job in crawl_source(source):
            by_id[job["id"]] = job

    # Curated high-value leads override the crawler's thin article extraction.
    for job in pinned_jobs():
        by_id[job["id"]] = job

    jobs = list(by_id.values())
    jobs.sort(key=lambda x: (x.get("score", 0), x.get("updated", "")), reverse=True)

    deduped = []
    seen_titles = set()
    for job in jobs:
        title_key = (clean(job.get("company", "")), clean(job.get("title", "")))
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        deduped.append(job)
    jobs = deduped

    result = {
        "meta": {
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "version": "V3-wide",
            "sources": [s["name"] for s in config["sources"]],
            "note": "扩展航天科工、航天科技、军工人才网和空间应用方向；保留官方入口型机会，剔除栏目导航、招生、公示和明显过期/跑偏岗位。"
        },
        "jobs": jobs[:300]
    }

    JOBS_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print("Saved real-job candidates:", len(jobs))

if __name__ == "__main__":
    main()
