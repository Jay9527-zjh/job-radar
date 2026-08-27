from pathlib import Path
from datetime import datetime
from urllib.parse import urljoin
import hashlib, json, re, requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parent
HEADERS={"User-Agent":"Mozilla/5.0 Personal Job Radar"}
WORDS=("招聘","校招","校园招聘","博士","人才","岗位","2027","2026")
FIELDS={
 "航空航天":("航空","航天","发动机","燃气轮机","飞行器"),
 "能源动力":("动力工程","工程热物理","叶轮","涡轮","压缩机","能源"),
 "机械装备":("机械","转子","密封","摩擦","轴承","可靠性"),
 "电子半导体":("电子","半导体","芯片","集成电路")
}
TARGET=("博士","航空发动机","燃气轮机","动力工程","工程热物理","流体","机械","叶轮机械","转子","密封","摩擦","可靠性","科研","研发")
def clean(s): return re.sub(r"\s+"," ",s or "").strip()
def gid(url,title): return hashlib.sha1((url+"|"+title).encode()).hexdigest()[:16]
def get(url):
 r=requests.get(url,headers=HEADERS,timeout=20); r.raise_for_status()
 if not r.encoding or r.encoding.lower()=="iso-8859-1": r.encoding=r.apparent_encoding
 return r.text
def infer_industry(t,fb):
 best,n=fb,0
 for k,ws in FIELDS.items():
  s=sum(t.count(w) for w in ws)
  if s>n: best,n=k,s
 return best
def score(t,loc,degree,org,ind):
 s=35+(18 if loc=="北京" else 5)+(18 if "博士" in degree else 0)+(8 if org in ("央企","国企","事业单位","科研院所") else 0)+(8 if ind in ("航空航天","能源动力","机械装备") else 0)
 s+=min(18,3*sum(w in t for w in TARGET))
 return min(99,s)
def date_from(t):
 m=re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})",t)
 if m:
  y,mo,d=map(int,m.groups())
  return f"{y:04d}-{mo:02d}-{d:02d}"
 return datetime.now().strftime("%Y-%m-%d")
def main():
 cfg=json.loads((ROOT/"sources.json").read_text(encoding="utf-8"))
 old=json.loads((ROOT/"jobs.json").read_text(encoding="utf-8"))
 merged={j.get("url"):j for j in old.get("jobs",[]) if j.get("url")}
 for src in cfg["sources"]:
  try: html=get(src["url"])
  except Exception as e:
   print("skip",src["name"],e); continue
  soup=BeautifulSoup(html,"lxml"); links=[]; seen=set()
  for a in soup.find_all("a",href=True):
   title=clean(a.get_text(" ",strip=True))
   if not title or not any(w in title for w in WORDS): continue
   url=urljoin(src["url"],a["href"])
   if url in seen: continue
   seen.add(url); links.append((title,url))
   if len(links)>=40: break
  for title,url in links:
   try:
    page=get(url); ps=BeautifulSoup(page,"lxml"); t=clean(ps.get_text(" ",strip=True))
   except Exception: t=title
   loc="北京" if any(x in t for x in ("北京","海淀","朝阳","丰台","昌平","大兴","顺义","怀柔","石景山")) else ("全国" if "全国" in t else "待确认")
   degree="博士" if any(x in t for x in ("博士","博士后","特别研究助理","高层次人才")) else ("硕士及以上" if "硕士" in t else "学历见公告")
   ind=infer_industry(t,src.get("industry","综合"))
   j={"id":gid(url,title),"company":src["name"],"title":title[:110],"location":loc,"org_type":src.get("org_type",""),"industry":ind,"degree":degree,"major":"","summary":t[:260],"updated":date_from(t),"deadline":"","score":score(t,loc,degree,src.get("org_type",""),ind),"source_name":src["name"],"url":url,"apply_url":url,"tags":[w for w in TARGET if w in t][:8]}
   merged[url]={**merged.get(url,{}),**j}
 out={"meta":{"updated_at":datetime.now().strftime("%Y-%m-%d %H:%M"),"sources":[s["name"] for s in cfg["sources"]]},"jobs":sorted(merged.values(),key=lambda j:(j.get("updated",""),j.get("score",0)),reverse=True)[:600]}
 (ROOT/"jobs.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
if __name__=="__main__": main()
