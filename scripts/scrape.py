#!/usr/bin/env python3
"""抓取米其林指南全球餐厅（三星、二星、一星、必比登、入选），维护 data/restaurants.json。

米其林网站对普通 HTTP 请求返回人机验证页，所以用 Playwright 无头浏览器抓取。

两个阶段：
  1. 列表阶段：抓全部列表页（约 400 页），得到每家的坐标、星级、价位、菜系、城市、国家。
     距上次列表抓取不足 LIST_INTERVAL_DAYS 天时跳过（除非 --list）。
  2. 详情阶段：给还没有地址的餐厅补抓详情页的地址和电话，在时间预算内尽量多抓，
     中国内地、港澳台、日韩、新加坡优先。

安全措施：列表抓到的数量少于上次的 60% 时不覆盖名单，以非零状态退出。

用法:
  python scripts/scrape.py               # 按需刷新列表 + 补抓详情
  python scripts/scrape.py --list        # 强制刷新列表
  python scripts/scrape.py --details 30  # 详情阶段最多运行 30 分钟
"""
import argparse
import datetime as dt
import html as htmllib
import json
import re
import sys
import time
from pathlib import Path

BASE = "https://guide.michelin.com"
SITE = BASE + "/sg/zh_CN"
LIST_URL = SITE + "/restaurants"
DATA = Path(__file__).resolve().parent.parent / "data" / "restaurants.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
TZ = dt.timezone(dt.timedelta(hours=8))
TODAY = dt.datetime.now(TZ).date().isoformat()
LIST_INTERVAL_DAYS = 25
PRIORITY = ["cn", "hk", "mo", "tw", "jp", "kr", "sg", "th", "my", "vn", "ph"]

FIELDS = ["id", "name", "dist", "green", "price", "cuisine", "city", "loc", "cc",
          "lat", "lng", "path", "addr", "phone", "added"]
DIST = {"THREE_STARS": "3", "TWO_STARS": "2", "ONE_STAR": "1", "BIB_GOURMAND": "b"}


def log(*a):
    print(*a, flush=True)


def clean(s):
    return re.sub(r"\s+", " ", htmllib.unescape(s or "")).strip()


# ---------------------------------------------------------------- 浏览器
class Browser:
    def __init__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._b = self._pw.chromium.launch()
        self._new_page()

    def _new_page(self):
        ctx = self._b.new_context(user_agent=UA, locale="zh-CN", viewport={"width": 1280, "height": 900})
        # 不加载图片字体，加快速度、减轻对方服务器负担
        ctx.route(re.compile(r".*\.(png|jpe?g|webp|gif|svg|woff2?|ttf|mp4)(\?.*)?$"), lambda r: r.abort())
        self.page = ctx.new_page()

    def get(self, url, ready_selector, tries=3):
        last = None
        for attempt in range(tries):
            try:
                resp = self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
                for _ in range(30):  # 等人机验证自动通过、内容出现
                    if self.page.query_selector(ready_selector):
                        break
                    if resp is not None and resp.status == 404:
                        return 404, ""
                    self.page.wait_for_timeout(1000)
                html = self.page.content()
                if self.page.query_selector(ready_selector):
                    return 200, html
                last = f"内容未出现 (HTTP {resp.status if resp else '-'})"
            except Exception as e:
                last = str(e)[:200]
            time.sleep(5 * (attempt + 1))
            if attempt == 1:
                try:
                    self.page.context.close()
                except Exception:
                    pass
                self._new_page()
        raise RuntimeError(f"抓取失败 {url}: {last}")

    def close(self):
        try:
            self._b.close()
            self._pw.stop()
        except Exception:
            pass


# ---------------------------------------------------------------- 解析
def parse_list(page_html):
    from bs4 import BeautifulSoup
    try:
        soup = BeautifulSoup(page_html, "lxml")
    except Exception:
        soup = BeautifulSoup(page_html, "html.parser")
    out = []
    for c in soup.select("div.card__menu.js-restaurant__list_item"):
        # 跳过页面底部“探索新精选餐厅”等推荐卡片
        if c.find_parent(class_=re.compile(r"section-nearby|nearby-restaurants")):
            continue
        a = c.select_one("h3 a[href*='/restaurant/']")
        if not a:
            continue
        bk = c.select_one(".js-bookmark-restaurant") or c.select_one("[data-dtm-id]") or c
        try:
            lat, lng = round(float(c.get("data-lat")), 6), round(float(c.get("data-lng")), 6)
        except (TypeError, ValueError):
            lat = lng = None
        scores = [clean(x.get_text(" ")) for x in c.select(".card__menu-footer--score")]
        loc = scores[0] if scores else ""
        price, cuisine = "", ""
        if len(scores) > 1:
            parts = [p.strip() for p in scores[1].split("·")]
            if len(parts) >= 2:
                price, cuisine = parts[0], "·".join(parts[1:]).strip()
            else:
                cuisine = parts[0]
        dist = DIST.get(bk.get("data-distinction") or bk.get("data-dtm-distinction") or "", "s")
        href = a["href"].split("?")[0]
        out.append({
            "id": int(c.get("data-id") or bk.get("data-dtm-id") or 0),
            "name": clean(a.get_text()),
            "dist": dist,
            "green": 1 if (bk.get("data-green-star") or "").strip() not in ("", "False", "false") else 0,
            "price": price if price.lower() != "none" else "",
            "cuisine": cuisine,
            "city": clean(bk.get("data-dtm-city")),
            "loc": loc,
            "cc": (bk.get("data-restaurant-country") or "").lower(),
            "lat": lat, "lng": lng,
            "path": href[len("/sg/zh_CN"):] if href.startswith("/sg/zh_CN") else href,
        })
    total = None
    m = re.search(r"/\s*([\d,]+)\s*家餐馆", page_html) or re.search(r"共\s*([\d,]+)\s*個餐廳", page_html)
    if m:
        total = int(m.group(1).replace(",", ""))
    pages = [int(x) for x in re.findall(r"/restaurants/page/(\d+)", page_html)]
    return out, total, (max(pages) if pages else 1)


def iter_jsonld(page_html):
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page_html, re.S | re.I):
        try:
            obj = json.loads(m.group(1).strip())
        except Exception:
            continue
        stack = [obj]
        while stack:
            o = stack.pop()
            if isinstance(o, list):
                stack.extend(o)
            elif isinstance(o, dict):
                yield o
                if "@graph" in o:
                    stack.append(o["@graph"])


def fmt_phone(p):
    raw = re.sub(r"[^\d+]", "", p or "")
    m = re.match(r"\+86(20|10|21|2\d)(\d{4})(\d{4})$", raw) or re.match(r"\+86(1\d{2})(\d{4})(\d{4})$", raw)
    if m:
        return f"+86 {m.group(1)} {m.group(2)} {m.group(3)}"
    return clean(p)


def parse_detail(page_html):
    d = {}
    for o in iter_jsonld(page_html):
        t = o.get("@type")
        t = t if isinstance(t, list) else [t]
        if "Restaurant" not in t and "FoodEstablishment" not in t:
            continue
        adr = o.get("address")
        if isinstance(adr, dict):
            d["addr"] = clean(adr.get("streetAddress") or "")
        elif isinstance(adr, str):
            d["addr"] = clean(adr)
        d["phone"] = fmt_phone(o.get("telephone"))
        break
    if not d.get("phone"):
        m = re.search(r'href="tel:([^"]+)"', page_html)
        if m:
            d["phone"] = fmt_phone(m.group(1))
    if d.get("addr"):
        d["addr"] = re.sub(r",\s*(Guangzhou|广州)(,.*)?$", "", d["addr"]).strip(" ,")
    return d


# ---------------------------------------------------------------- 数据读写
def load():
    if not DATA.exists():
        return {}, {}
    raw = json.loads(DATA.read_text("utf-8"))
    rows = {}
    if "rows" in raw:
        f = raw["fields"]
        for r in raw["rows"]:
            o = dict(zip(f, r))
            rows[o["id"]] = o
    elif "restaurants" in raw:  # 旧版广州名单：只取地址电话，按路径匹配
        for r in raw["restaurants"]:
            path = r.get("url", "").split("guide.michelin.com")[-1].replace("/sg/zh_CN", "")
            rows["old:" + path] = {"path": path, "addr": r.get("addr", ""), "phone": r.get("phone", "")}
    return raw, rows


def save(meta, rows):
    order = {"3": 0, "2": 1, "1": 2, "b": 3, "s": 4}
    rs = sorted(rows.values(), key=lambda o: (order.get(o["dist"], 9), o["cc"], o["city"], o["name"]))
    data = {
        "updated": TODAY,
        "list_updated": meta.get("list_updated", TODAY),
        "source": LIST_URL,
        "count": len(rs),
        "fields": FIELDS,
        "rows": [[o.get(k) if o.get(k) is not None else ("" if k not in ("lat", "lng") else None) for k in FIELDS] for o in rs],
    }
    if DATA.exists():
        try:
            old = json.loads(DATA.read_text("utf-8"))
            if old.get("rows") == data["rows"] and old.get("list_updated") == data["list_updated"]:
                log("名单内容没有变化，不写文件")
                return
        except Exception:
            pass
    DATA.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", "utf-8")


# ---------------------------------------------------------------- 主流程
def run_list(br, prev):
    first_html = br.get(LIST_URL, "div.card__menu.js-restaurant__list_item")[1]
    cards, total, last = parse_list(first_html)
    log(f"列表共 {total} 家，{last} 页")
    got = {c["id"]: c for c in cards}
    fails = 0
    for n in range(2, last + 1):
        try:
            _, h = br.get(f"{LIST_URL}/page/{n}", "div.card__menu.js-restaurant__list_item")
            for c in parse_list(h)[0]:
                got[c["id"]] = c
        except Exception as e:
            fails += 1
            log(f"  第 {n} 页失败: {e}")
            if fails > 20:
                raise RuntimeError("列表页失败过多，放弃")
        if n % 25 == 0:
            log(f"  已抓 {n}/{last} 页，累计 {len(got)} 家")
        time.sleep(0.8)

    real_prev = {k: v for k, v in prev.items() if not str(k).startswith("old:")}
    floor = max(1000, int(len(real_prev) * 0.6))
    if len(got) < floor:
        log(f"只抓到 {len(got)} 家，低于安全下限 {floor}，不更新。")
        sys.exit(2)

    initial = len(real_prev) < 1000          # 首次全球抓取：不把所有店都标成“新上榜”
    old_by_path = {v["path"]: v for k, v in prev.items() if str(k).startswith("old:")}
    rows = {}
    for i, c in got.items():
        p = real_prev.get(i) or old_by_path.get(c["path"]) or {}
        c["addr"] = p.get("addr", "")
        c["phone"] = p.get("phone", "")
        c["added"] = p.get("added", "") if i in real_prev else ("" if initial else TODAY)
        rows[i] = c
    new = [i for i in rows if i not in real_prev]
    gone = [i for i in real_prev if i not in rows]
    log(f"列表完成：共 {len(rows)} 家，新增 {0 if initial else len(new)}，下榜 {len(gone)}")
    return rows


def run_details(br, rows, minutes):
    todo = [o for o in rows.values() if not o.get("addr") and o.get("path")]
    rank = {cc: i for i, cc in enumerate(PRIORITY)}
    dist_rank = {"3": 0, "2": 1, "1": 2, "b": 3, "s": 4}
    todo.sort(key=lambda o: (rank.get(o["cc"], 99), dist_rank.get(o["dist"], 9)))
    log(f"待补地址 {len(todo)} 家，本次最多 {minutes} 分钟")
    t_end = time.time() + minutes * 60
    done = miss = 0
    for o in todo:
        if time.time() > t_end:
            break
        try:
            status, h = br.get(SITE + o["path"], "script[type='application/ld+json']", tries=2)
            d = parse_detail(h) if status == 200 else {}
        except Exception as e:
            log(f"  详情失败 {o['path']}: {e}")
            d = {}
        if d.get("addr"):
            o["addr"] = d["addr"]
            o["phone"] = d.get("phone", "") or o.get("phone", "")
            done += 1
        else:
            miss += 1
        if (done + miss) % 100 == 0:
            log(f"  详情进度 {done + miss}，成功 {done}")
        time.sleep(0.6)
    log(f"详情完成：补到 {done} 家，失败 {miss} 家，剩余约 {len(todo) - done - miss} 家")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="强制刷新列表")
    ap.add_argument("--details", type=float, default=60, help="详情阶段时间预算（分钟），0 表示跳过")
    args = ap.parse_args()

    meta, prev = load()
    real_prev = {k: v for k, v in prev.items() if not str(k).startswith("old:")}
    need_list = args.list or len(real_prev) < 1000
    if not need_list and meta.get("list_updated"):
        age = (dt.date.fromisoformat(TODAY) - dt.date.fromisoformat(meta["list_updated"])).days
        need_list = age >= LIST_INTERVAL_DAYS

    br = Browser()
    try:
        if need_list:
            rows = run_list(br, prev)
            meta["list_updated"] = TODAY
        else:
            rows = real_prev
            log(f"列表 {meta.get('list_updated')} 刚更新过，本次只补详情")
        save(meta, rows)  # 列表结果先落盘，详情阶段中断也不丢
        if args.details > 0:
            run_details(br, rows, args.details)
            save(meta, rows)
    finally:
        br.close()


if __name__ == "__main__":
    main()
