#!/usr/bin/env python3
"""株価の取得 → J-Quants公式データとの照合 → data.json / import.csv を出力する。

使い方:
  pip install yfinance requests
  export JQUANTS_API_KEY=xxxx     # J-Quantsダッシュボードで発行(Freeプランで可)
  export STOOQ_API_KEY=xxxx       # 任意。Yahoo取得に失敗した時の予備
  python fetch_data.py

照合の仕組み:
  最新値は Yahoo(予備:Stooq)から取得し、J-Quants無料プランが持つ
  「12週間以上前」の期間の終値と突き合わせる。大きくずれる銘柄
  (分割の反映漏れなど)は quality=warn とし、import.csv から除外する。
"""
import csv, datetime as dt, io, json, os, statistics, sys, time
import requests

# ★ 対象銘柄は自由に編集してください(例示であり推奨ではありません)
TICKERS = [("7203", "トヨタ自動車"), ("6758", "ソニーG"), ("8306", "三菱UFJ"),
           ("9984", "ソフトバンクG"), ("6861", "キーエンス"), ("8035", "東京エレクトロン"),
           ("9432", "NTT"), ("4502", "武田薬品")]
KEEP = 160          # 保存する日数
TOL = 0.01          # 終値のずれの許容(1%)
JQ = "https://api.jquants.com/v2"
today = dt.date.today()


def fetch_yahoo(code):
    import yfinance as yf
    # auto_adjust=False の Close は分割調整のみ(配当調整なし)=J-Quantsと同じ基準
    h = yf.Ticker(f"{code}.T").history(period="1y", auto_adjust=False)
    h = h.dropna(subset=["Close"])
    return [(d.strftime("%Y-%m-%d"), float(r["Close"]), int(r["Volume"])) for d, r in h.iterrows()]


def fetch_stooq(code):
    # 注意: 2026年初めからAPIキーが必要。パラメータ名は最新の公式案内で要確認
    p = {"s": f"{code}.jp", "i": "d"}
    if os.getenv("STOOQ_API_KEY"):
        p["apikey"] = os.environ["STOOQ_API_KEY"]
    r = requests.get("https://stooq.com/q/d/l/", params=p, timeout=30)
    r.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(r.text)))
    return [(x["Date"], float(x["Close"]), int(float(x.get("Volume") or 0))) for x in rows if x.get("Close")]


def jq_get(path, params, key):
    out, pk = [], None
    while True:
        q = dict(params)
        if pk:
            q["pagination_key"] = pk
        for i in range(3):
            r = requests.get(JQ + path, params=q, headers={"x-api-key": key}, timeout=30)
            if r.status_code == 429:      # レート制限 → 待って再試行
                time.sleep(5 * (i + 1)); continue
            break
        r.raise_for_status()
        j = r.json()
        out += j.get("data", [])
        pk = j.get("pagination_key")
        if not pk:
            return out


def fetch_jquants(code, key):
    # Freeプランは「直近12週間を除く2年分」。取得できる最新側に合わせて期間を決める
    to = today - dt.timedelta(days=85)
    frm = today - dt.timedelta(days=360)
    rows = jq_get("/equities/bars/daily", {"code": code, "from": frm.strftime("%Y%m%d"),
                                            "to": to.strftime("%Y%m%d")}, key)
    out = {}
    for r in rows:
        c = r.get("AdjC") if r.get("AdjC") is not None else r.get("C")   # 調整済み終値を優先
        if c is not None:
            out[str(r["Date"])[:10]] = float(c)
    return out


def validate(series, jq):
    if not jq:
        return "unverified", "J-Quantsデータなし(照合できず)"
    last = dt.date.fromisoformat(series[-1][0])
    if (today - last).days > 5:
        return "warn", f"最終日が古い({last})"
    mine = {d: c for d, c, _ in series}
    common = [d for d in mine if d in jq]
    if len(common) < 20:
        return "unverified", f"照合できた日数が少ない({len(common)}日)"
    bad = [d for d in common if abs(mine[d] / jq[d] - 1) > TOL]
    if len(bad) / len(common) > 0.05:
        return "warn", f"{len(bad)}/{len(common)}日で{TOL:.0%}超のずれ(分割反映漏れの可能性)"
    med = statistics.median(abs(mine[d] / jq[d] - 1) for d in common)
    return "ok", f"{len(common)}日を照合、問題なし(ずれ中央値 {med:.3%})"


def main():
    key = os.getenv("JQUANTS_API_KEY")
    if not key:
        print("⚠ JQUANTS_API_KEY が未設定のため、照合なしで取得します", file=sys.stderr)
    stocks = []
    tickers = list(TICKERS)                      # 環境変数 HOLDINGS の銘柄も自動で取得対象に追加
    known = {c for c, _ in tickers}
    try:
        for h in json.loads(os.getenv("HOLDINGS") or "[]"):
            c = str(h["code"])
            if c not in known:
                tickers.append((c, h.get("name", c))); known.add(c)
    except Exception:
        print("HOLDINGSの形式エラー(無視して続行)", file=sys.stderr)
    for code, name in tickers:
        series, src = [], None
        for fn, label in ((fetch_yahoo, "yahoo"), (fetch_stooq, "stooq")):
            try:
                series = fn(code)
                if len(series) >= 60:
                    src = label; break
            except Exception as e:
                print(f"  {code}: {label}失敗 {e}", file=sys.stderr)
            time.sleep(1)
        if not src:
            stocks.append({"code": code, "name": name, "quality": {"status": "error", "detail": "取得失敗"}})
            continue
        try:
            jq = fetch_jquants(code, key) if key else {}
        except Exception as e:
            print(f"  {code}: J-Quants失敗 {e}", file=sys.stderr); jq = {}
        status, detail = validate(series, jq)
        s = series[-KEEP:]
        stocks.append({"code": code, "name": name, "source": src,
                       "dates": [x[0] for x in s], "close": [round(x[1], 1) for x in s],
                       "volume": [x[2] for x in s], "quality": {"status": status, "detail": detail}})
        print(f"{code} {name}: {src} / {status} / {detail}")
        time.sleep(1)

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump({"generated": dt.datetime.now().isoformat(timespec="seconds"), "stocks": stocks},
                  f, ensure_ascii=False)
    # 既存アプリの「CSV取込」にそのまま貼れる形式(warn/errorは除外)
    with open("import.csv", "w", encoding="utf-8") as f:
        for s in stocks:
            if s.get("quality", {}).get("status") in ("ok", "unverified"):
                f.write(",".join([s["code"], s["name"]] + [str(v) for v in s["close"]]) + "\n")
    print("→ data.json / import.csv を出力しました")


if __name__ == "__main__":
    main()
