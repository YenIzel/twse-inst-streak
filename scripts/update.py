#!/usr/bin/env python3
"""
三大法人連續買賣超追蹤 — 資料更新腳本

資料來源（臺灣證券交易所）：
  外資及陸資 TWT38U  https://www.twse.com.tw/zh/trading/foreign/twt38u.html
  投信       TWT44U  https://www.twse.com.tw/zh/trading/foreign/twt44u.html
  自營商     TWT43U  https://www.twse.com.tw/zh/trading/foreign/twt43u.html

流程：
  1. 從今天往回找，補抓本地還沒有的交易日（已抓過的不重抓）
  2. 每個交易日存成 data/raw/YYYYMMDD.json
  3. 計算每檔個股、每個法人「到最新交易日為止」的連續買超／賣超天數
  4. 輸出 docs/data/streaks.json 給前端使用

只用 Python 標準函式庫，不需安裝套件。
"""
from __future__ import annotations

import datetime as dt
import json
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

# ── 設定 ────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("Asia/Taipei")

HISTORY = 20        # 保留幾個交易日（＝最長可算到的連續天數）
WINDOW = 10         # 輸出給前端的每日明細天數（需 ≥ 最大的 N）
MAX_LOOKBACK = 60   # 往回找交易日最多幾個日曆天
SLEEP = 3.0         # 每次請求間隔秒數（證交所有流量限制，太快會被暫時封鎖）

# key 順序即前端陣列順序：0 外資、1 投信、2 自營商
SOURCES = [
    ("foreign", "外資及陸資", "TWT38U"),
    ("trust",   "投信",       "TWT44U"),
    ("dealer",  "自營商",     "TWT43U"),
]
URL = "https://www.twse.com.tw/rwd/zh/fund/{ep}?date={date}&response=json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (inst-streak-tracker)",
    "Accept": "application/json",
}


def paths(root: Path = None):
    root = root or ROOT
    return {
        "raw": root / "data" / "raw",
        "closed": root / "data" / "closed_days.json",
        "out": root / "docs" / "data" / "streaks.json",
    }


# ── 抓取與解析 ──────────────────────────────────────────────────────
class FetchError(RuntimeError):
    pass


def fetch_json(ep: str, date: str) -> dict:
    """抓一張表；網路錯誤或被擋（回傳非 JSON）時重試。"""
    url = URL.format(ep=ep, date=date)
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
            return json.loads(body)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as e:
            last = e
            wait = 10 * (attempt + 1) + random.uniform(0, 3)
            print(f"  ! {ep} {date} 第 {attempt + 1} 次失敗：{e}，{wait:.0f} 秒後重試", file=sys.stderr)
            time.sleep(wait)
    raise FetchError(f"{ep} {date} 抓取失敗：{last}")


def to_int(s) -> int:
    s = str(s).replace(",", "").strip()
    if s in ("", "-", "--"):
        return 0
    return int(float(s))


WARRANT_PREFIXES = ("03", "04", "05", "06", "07", "08")


def is_warrant(code: str) -> bool:
    """上市權證、牛熊證：6 碼且 03～08 開頭（如 080001、04000T）。股票、ETF（00 開頭）、TDR（91 開頭）都會保留。"""
    return len(code) == 6 and code.startswith(WARRANT_PREFIXES)


def parse_table(payload: dict) -> tuple[dict, dict]:
    """
    回傳 ({代號: 買賣超股數}, {代號: 名稱})。
    以欄位名稱定位，不寫死欄位位置：
      - 代號、名稱：「證券代號」「證券名稱」
      - 買賣超：最後一個「買賣超股數」欄（TWT38U＝外資及陸資合計、TWT43U＝自營商合計）
    """
    fields = [f.strip() for f in payload.get("fields", [])]
    try:
        i_code = fields.index("證券代號")
        i_name = fields.index("證券名稱")
    except ValueError:
        raise FetchError(f"欄位格式不符：{fields}")
    net_cols = [i for i, f in enumerate(fields) if "買賣超" in f]
    if not net_cols:
        raise FetchError(f"找不到買賣超欄位：{fields}")
    i_net = net_cols[-1]

    net, names = {}, {}
    for row in payload.get("data", []):
        if len(row) <= max(i_code, i_name, i_net):
            continue
        code = str(row[i_code]).strip()
        if not code or is_warrant(code):
            continue
        net[code] = net.get(code, 0) + to_int(row[i_net])
        names[code] = str(row[i_name]).strip()
    return net, names


def fetch_day(date: str) -> dict | None:
    """
    抓某日三張表。
    回傳 None 表示當天沒有資料（休市，或當天資料尚未公布）。
    """
    day = {"date": date, "names": {}, "net": {}}
    tables = []
    for idx, (key, label, ep) in enumerate(SOURCES):
        if idx > 0:
            time.sleep(SLEEP + random.uniform(0, 1))
        payload = fetch_json(ep, date)
        if payload.get("stat") != "OK" or str(payload.get("date", date)) != date:
            if idx == 0:
                return None  # 外資表沒資料 → 視為非交易日，不必再抓另外兩張
            raise FetchError(f"{date} {label} 無資料（{payload.get('stat')}），但外資有資料；稍後重試")
        net, names = parse_table(payload)
        tables.append(net)
        day["names"].update(names)

    codes = sorted(set().union(*tables))
    day["net"] = {c: [t.get(c, 0) for t in tables] for c in codes}
    return day


# ── 連續天數計算 ────────────────────────────────────────────────────
def streak(series: list[int]) -> int:
    """series 由新到舊。回傳帶正負號的連續天數：+n 連續買超 n 日、-n 連續賣超 n 日、0 最新一日無買賣超。"""
    if not series or series[0] == 0:
        return 0
    sign = 1 if series[0] > 0 else -1
    n = 0
    for v in series:
        if v * sign > 0:
            n += 1
        else:
            break
    return sign * n


def build_output(days: list[dict], now: dt.datetime) -> dict:
    """days 由新到舊。"""
    names = {}
    for d in reversed(days):            # 舊→新覆蓋，名稱以最新為準
        names.update(d["names"])

    latest = days[0]
    stocks = []
    for code in sorted(c for c in latest["net"] if not is_warrant(c)):
        s, v = [], []
        for k in range(len(SOURCES)):
            series = [d["net"].get(code, [0] * len(SOURCES))[k] for d in days]
            s.append(streak(series))
            v.append([round(x / 1000) for x in series[:WINDOW]])   # 股 → 張
        if any(s):
            stocks.append({"c": code, "n": names.get(code, ""), "s": s, "v": v})

    return {
        "updated": now.isoformat(timespec="seconds"),
        "latest": latest["date"],
        "dates": [d["date"] for d in days[:WINDOW]],
        "history": len(days),
        "institutions": [{"key": k, "label": lbl} for k, lbl, _ in SOURCES],
        "stocks": stocks,
    }


# ── 主程式 ──────────────────────────────────────────────────────────
def load_json(p: Path, default):
    try:
        return json.loads(p.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def main(root: Path = None, now: dt.datetime = None) -> int:
    P = paths(root)
    P["raw"].mkdir(parents=True, exist_ok=True)
    P["out"].parent.mkdir(parents=True, exist_ok=True)

    now = now or dt.datetime.now(TZ)
    today = now.date()
    closed = set(load_json(P["closed"], []))

    trading, fetched, errors = [], 0, []
    first_request = True
    for back in range(MAX_LOOKBACK):
        if len(trading) >= HISTORY:
            break
        d = today - dt.timedelta(days=back)
        ds = d.strftime("%Y%m%d")
        raw_file = P["raw"] / f"{ds}.json"

        if raw_file.exists():
            trading.append(ds)
            continue
        if ds in closed:
            continue

        if not first_request:
            time.sleep(SLEEP + random.uniform(0, 1))
        first_request = False
        try:
            day = fetch_day(ds)
        except FetchError as e:
            print(f"  ! {e}", file=sys.stderr)
            errors.append(ds)
            continue

        if day is None:
            if d < today:       # 今天沒資料可能只是還沒公布，不記為休市
                closed.add(ds)
            print(f"  - {ds} 無資料")
            continue

        raw_file.write_text(json.dumps(day, ensure_ascii=False, separators=(",", ":")), "utf-8")
        trading.append(ds)
        fetched += 1
        print(f"  + {ds} 共 {len(day['net'])} 檔")

    if not trading:
        print("找不到任何交易日資料", file=sys.stderr)
        return 1

    # 清掉超出保留範圍的舊檔
    keep = set(trading)
    oldest = min(trading)
    for f in P["raw"].glob("*.json"):
        if f.stem not in keep and f.stem < oldest:
            f.unlink()
    closed = sorted(c for c in closed if c >= oldest)
    P["closed"].write_text(json.dumps(closed), "utf-8")

    # 只用「中間沒有缺漏」的連續交易日來算，避免抓取失敗的日子讓連續天數算錯
    chain = contiguous_chain(trading, errors)
    days = [load_json(P["raw"] / f"{ds}.json", None) for ds in chain]
    out = build_output([d for d in days if d], now)
    P["out"].write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), "utf-8")

    print(f"完成：新抓 {fetched} 日，最新交易日 {out['latest']}，"
          f"可用 {out['history']} 個交易日，輸出 {len(out['stocks'])} 檔")
    if errors:
        # GitHub Actions 會把這行顯示成警告；下次執行會自動補抓
        print(f"::warning::以下日期抓取失敗，下次執行會自動補抓：{', '.join(errors)}")
    return 0


def contiguous_chain(trading: list[str], errors: list[str]) -> list[str]:
    """由新到舊，取到第一個抓取失敗的日期為止（最新幾天就失敗的話則跳過，從前一個成功日開始）。"""
    events = sorted([(d, True) for d in trading] + [(d, False) for d in errors], reverse=True)
    chain = []
    for d, ok in events:
        if ok:
            chain.append(d)
        elif chain:
            break
    return chain


if __name__ == "__main__":
    sys.exit(main())
