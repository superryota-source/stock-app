#!/usr/bin/env python3
"""保有銘柄(環境変数 HOLDINGS)の売り判定を行い、該当があれば ntfy でスマホにプッシュ通知する。
  HOLDINGS 例: [{"code":"7203","name":"トヨタ","q":100,"cost":2500}]  (GitHub Secretsに保存)
  NTFY_TOPIC : 推測されにくい長い文字列(ntfyアプリで同じトピックを購読)
  ※ 公開リポジトリのログに保有内容が出ないよう、ログには件数しか出さない。"""
import json, os, sys
import requests


def sma(a, n):
    return sum(a[-n:]) / n if len(a) >= n else None


def ema(a, n):
    k, e, out = 2 / (n + 1), a[0], [a[0]]
    for v in a[1:]:
        e = v * k + e * (1 - k); out.append(e)
    return out


def rsi(a, n=14):
    g = l = 0.0
    for i in range(len(a) - n, len(a)):
        d = a[i] - a[i - 1]
        if d > 0: g += d
        else: l -= d
    return 100.0 if l == 0 else 100 - 100 / (1 + g / l)


def macd_hist(a):
    m = [x - y for x, y in zip(ema(a, 12), ema(a, 26))]
    return m[-1] - ema(m, 9)[-1]


def judge(a, cost):
    last, s20, s50 = a[-1], sma(a, 20), sma(a, 50)
    pl = (last / cost - 1) * 100
    rs = []
    if pl <= -8: rs.append("損切りライン(-8%)到達")
    if last < s50: rs.append("50日線割れ")
    if last < s20 and macd_hist(a) < 0: rs.append("短期トレンド悪化")
    if rsi(a) > 75: rs.append("RSI過熱")
    v = "売り推奨" if pl <= -8 or last < s50 else ("一部利確/注意" if rs else "保有継続")
    return v, pl, rs


def main():
    try:
        hold = json.loads(os.getenv("HOLDINGS") or "[]")
        data = {s["code"]: s for s in json.load(open("data.json", encoding="utf-8"))["stocks"]}
    except Exception as e:
        print(f"入力エラー ({type(e).__name__})", file=sys.stderr); return
    sells, cautions, pending = [], [], []
    for h in hold:
        code = str(h.get("code")); s = data.get(code)
        label = f"{code} {h.get('name') or (s or {}).get('name', '')}".strip()
        if not s or not s.get("close") or len(s["close"]) < 60 or s.get("quality", {}).get("status") not in ("ok", "unverified"):
            pending.append(label); continue
        v, pl, rs = judge(s["close"], float(h["cost"]))
        line = f"{label}: {v} 損益{pl:+.1f}%" + (f"({'・'.join(rs)})" if rs else "")
        if v == "売り推奨": sells.append(line)
        elif v != "保有継続": cautions.append(line)
    parts = []
    if sells: parts += ["【売り推奨】"] + sells
    if cautions: parts += ["【注意】"] + cautions
    if pending: parts += ["【データ要確認で判定保留】"] + pending
    print(f"保有{len(hold)}件 / 売り推奨{len(sells)} / 注意{len(cautions)} / 保留{len(pending)}")
    if not (sells or cautions or pending):
        return
    msg = "\n".join(parts)
    if "--dry" in sys.argv:
        print(msg); return
    topic = os.getenv("NTFY_TOPIC")
    if not topic:
        print("NTFY_TOPIC未設定のため通知をスキップ"); return
    r = requests.post(os.getenv("NTFY_SERVER", "https://ntfy.sh"), timeout=30,
                      json={"topic": topic, "title": "株アプリ:保有銘柄のお知らせ", "message": msg,
                            "priority": 4 if sells else 3, "tags": ["chart_with_downwards_trend"]})
    r.raise_for_status()
    print("通知を送信しました")


if __name__ == "__main__":
    main()
