#!/usr/bin/env python3
"""抓取米其林指南广州必比登名单，更新 data/restaurants.json。

用法:
  python scripts/scrape.py            # 先用 requests 抓取
  python scripts/scrape.py --browser  # 用 Playwright 无头浏览器抓取（网站拦截普通请求时）

安全措施: 抓到的餐厅数明显少于上次（< 60%）时不写文件并以非零状态退出，
避免网站改版导致名单被清空。
"""
import argparse
import datetime as dt
import html
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

BASE = "https://guide.michelin.com"
LIST_URL = BASE + "/sg/zh_CN/guangdong/guangzhou_1026985/restaurants/bib-gourmand"
DATA = Path(__file__).resolve().parent.parent / "data" / "restaurants.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
TODAY = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()


class Blocked(Exception):
    pass


# ---------------------------------------------------------------- 抓取
class Fetcher:
    def __init__(self, browser=False):
        self.browser = browser
        self._page = None
        if not browser:
            import requests
            self.s = requests.Session()
            self.s.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
                                   "Accept": "text/html,application/xhtml+xml"})

    def get(self, url):
        if self.browser:
            return self._get_browser(url)
        last = None
        for attempt in range(3):
            try:
                r = self.s.get(url, timeout=30)
                if r.status_code in (403, 429, 503):
                    raise Blocked(f"HTTP {r.status_code} {url}")
                r.raise_for_status()
                r.encoding = "utf-8"
                if "/restaurant/" not in r.text and "restaurant" not in r.text:
                    raise Blocked("页面内容异常，可能被拦截: " + url)
                return r.text
            except Blocked:
                raise
            except Exception as e:  # 网络抖动重试
                last = e
                time.sleep(3 * (attempt + 1))
        raise RuntimeError(f"抓取失败 {url}: {last}")

    def _get_browser(self, url):
        if self._page is None:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            self._b = self._pw.chromium.launch()
            self._page = self._b.new_page(user_agent=UA, locale="zh-CN")
        self._page.goto(url, wait_until="domcontentloaded", timeout=60000)
        self._page.wait_for_timeout(1500)
        return self._page.content()

    def close(self):
        if self._page is not None:
            self._b.close()
            self._pw.stop()


# ---------------------------------------------------------------- 解析
def clean(s):
    return re.sub(r"\s+", " ", html.unescape(s or "")).strip()


def short_addr(a):
    a = clean(a)
    a = re.sub(r",\s*(Guangzhou|广州).*$", "", a)
    return a.strip(" ,")


def parse_listing(page):
    """返回 [(slug, name, price, cuisine, lat, lng)]，保持页面顺序。"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(page, "html.parser")
    out, seen = [], set()
    for h in soup.find_all(["h3", "h2"]):
        a = h.find("a", href=re.compile(r"/restaurant/[^/?#]+"))
        if not a:
            continue
        m = re.search(r"/restaurant/([^/?#]+)", a["href"])
        slug = urllib.parse.quote(urllib.parse.unquote(m.group(1)), safe="-_.~")
        if slug in seen:
            continue
        # 只要广州的卡片（页面底部有新加坡推荐卡）
        if "guangzhou" not in a["href"]:
            continue
        seen.add(slug)
        # 向上找到恰好包住这一张卡片（含价位与菜系）的容器
        card = h
        for _ in range(7):
            parent = card.parent
            if parent is None or parent.name in ("body", "html"):
                break
            if len(parent.find_all(["h3", "h2"])) > 1:
                break
            card = parent
            if card.has_attr("data-lat") or re.search(r"[¥$]{1,4}\s*·", card.get_text(" ")):
                break
        text = clean(card.get_text(" "))
        price, cuisine = "", ""
        pm = re.search(r"(¥{1,4}|\${1,4})\s*·\s*([^\s·]+)", text)
        if pm:
            price, cuisine = pm.group(1).replace("$", "¥"), pm.group(2)
        lat = lng = None
        holder = card if card.has_attr("data-lat") else (card.find(attrs={"data-lat": True}) or h.find_parent(attrs={"data-lat": True}))
        if holder is not None:
            try:
                lat, lng = float(holder["data-lat"]), float(holder.get("data-lng") or holder.get("data-lon"))
            except (TypeError, ValueError, KeyError):
                lat = lng = None
        out.append((slug, clean(a.get_text()), price, cuisine, lat, lng))
    return out


def iter_jsonld(page):
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
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


def parse_detail(page):
    d = {}
    for o in iter_jsonld(page):
        t = o.get("@type")
        t = t if isinstance(t, list) else [t]
        if "Restaurant" not in t and "FoodEstablishment" not in t:
            continue
        d["name"] = clean(o.get("name"))
        adr = o.get("address")
        if isinstance(adr, dict):
            d["addr"] = clean(adr.get("streetAddress") or "")
        elif isinstance(adr, str):
            d["addr"] = clean(adr)
        d["phone"] = clean(o.get("telephone"))
        cui = o.get("servesCuisine")
        if isinstance(cui, list):
            cui = cui[0] if cui else ""
        d["cuisine"] = clean(cui)
        geo = o.get("geo") or {}
        lat = o.get("latitude", geo.get("latitude") if isinstance(geo, dict) else None)
        lng = o.get("longitude", geo.get("longitude") if isinstance(geo, dict) else None)
        try:
            d["lat"], d["lng"] = float(lat), float(lng)
        except (TypeError, ValueError):
            pass
        break

    if "lat" not in d:
        for pat in (r'lat=([0-9.]+)&(?:amp;)?lon=([0-9.]+)',
                    r'data-lat="([0-9.]+)"[^>]*?data-lng="([0-9.]+)"',
                    r'"latitude"\s*:\s*"?([0-9.]+)"?\s*,\s*"longitude"\s*:\s*"?([0-9.]+)'):
            m = re.search(pat, page)
            if m:
                d["lat"], d["lng"] = float(m.group(1)), float(m.group(2))
                break

    if not d.get("addr"):
        m = re.search(r'google\.com/maps/embed[^"\']*?[?&](?:amp;)?q=([^&"\']+)', page)
        if m:
            d["addr"] = clean(urllib.parse.unquote_plus(m.group(1)))
    if not d.get("phone"):
        m = re.search(r'href="tel:([^"]+)"', page)
        if m:
            d["phone"] = clean(m.group(1))
    if d.get("addr"):
        d["addr"] = short_addr(d["addr"])
    if d.get("phone"):
        p = re.sub(r"[^\d+]", "", d["phone"])
        m = re.match(r"\+86(20)(\d{4})(\d{4})$", p) or re.match(r"\+86(1\d{2})(\d{4})(\d{4})$", p)
        d["phone"] = f"+86 {m.group(1)} {m.group(2)} {m.group(3)}" if m else d["phone"]
    return d


def in_gz(lat, lng):
    return lat is not None and lng is not None and 22.4 < lat < 23.95 and 112.9 < lng < 114.1


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--browser", action="store_true")
    args = ap.parse_args()

    old = json.loads(DATA.read_text("utf-8")) if DATA.exists() else {"restaurants": []}
    prev = {r["slug"]: r for r in old.get("restaurants", [])}

    f = Fetcher(browser=args.browser)
    try:
        cards, seen = [], set()
        for n in range(1, 11):
            url = LIST_URL if n == 1 else f"{LIST_URL}/page/{n}"
            page = f.get(url)
            got = [c for c in parse_listing(page) if c[0] not in seen]
            print(f"列表第 {n} 页: {len(got)} 家")
            if not got:
                break
            cards += got
            seen.update(c[0] for c in got)
            if not re.search(r'/bib-gourmand/page/%d["?]' % (n + 1), page):
                break
            time.sleep(1)

        floor = max(20, int(len(prev) * 0.6))
        if len(cards) < floor:
            print(f"只抓到 {len(cards)} 家，低于安全下限 {floor}，不更新。", file=sys.stderr)
            sys.exit(2)

        out, failed = [], []
        for slug, name, price, cuisine, lat, lng in cards:
            url = f"{BASE}/sg/zh_CN/guangdong-province/guangzhou/restaurant/{slug}"
            try:
                d = parse_detail(f.get(url))
            except Blocked:
                raise
            except Exception as e:
                print(f"  详情失败 {slug}: {e}", file=sys.stderr)
                d = {}
            p = prev.get(slug, {})
            r = {
                "slug": slug,
                "name": name or d.get("name") or p.get("name", ""),
                "price": price or p.get("price", ""),
                "cuisine": cuisine or p.get("cuisine") or d.get("cuisine", ""),
                "addr": d.get("addr") or p.get("addr", ""),
                "phone": d.get("phone") or p.get("phone", ""),
                "lat": None, "lng": None,
                "url": url,
                "added": p["added"] if slug in prev else TODAY,
            }
            for la, ln in ((d.get("lat"), d.get("lng")), (lat, lng), (p.get("lat"), p.get("lng"))):
                if in_gz(la, ln):
                    r["lat"], r["lng"] = round(la, 7), round(ln, 7)
                    break
            if not r["addr"]:
                failed.append(slug)
            out.append(r)
            time.sleep(0.6)
    finally:
        f.close()

    new_slugs = [r["slug"] for r in out if r["slug"] not in prev]
    gone = [s for s in prev if s not in {r["slug"] for r in out}]
    data = {"updated": TODAY, "source": LIST_URL, "count": len(out), "restaurants": out}
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(f"完成: 共 {len(out)} 家，新增 {len(new_slugs)} {new_slugs}，下榜 {len(gone)} {gone}")
    if failed:
        print(f"缺少地址: {failed}", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except Blocked as e:
        print(f"被网站拦截: {e}", file=sys.stderr)
        sys.exit(3)
