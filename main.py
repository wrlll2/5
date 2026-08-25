#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Market Anomaly Monitor & Sentiment Notifier
- 监测 A股、美股三大指数、日经225、韩国KOSPI 及美股市值 Top 10 股票
- 当任一标的单日涨跌幅绝对值 >= 3% 时，抓取 StockTwits 与 Reddit (r/stocks) 外网金融热搜并发送 Bark 提醒
- 否则静默退出 (exit 0)
"""

import os
import sys
import requests
import yfinance as yf
from datetime import datetime

# 确保控制台支持 UTF-8 输出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# 监测标的列表
TARGET_ASSETS = [
    # --- 亚太与中国市场指数 ---
    {"symbol": "000001.SS", "name": "上证指数 (A股)", "type": "Index"},
    {"symbol": "^N225", "name": "日经225 (Nikkei 225)", "type": "Index"},
    {"symbol": "^KS11", "name": "韩国综合指数 (KOSPI)", "type": "Index"},

    # --- 美股三大指数 ---
    {"symbol": "^GSPC", "name": "标普500 (S&P 500)", "type": "Index"},
    {"symbol": "^DJI", "name": "道琼斯指数 (Dow Jones)", "type": "Index"},
    {"symbol": "^IXIC", "name": "纳斯达克 (Nasdaq)", "type": "Index"},

    # --- 美股市值 Top 10 巨头股票 ---
    {"symbol": "NVDA", "name": "英伟达 (NVDA)", "type": "Stock"},
    {"symbol": "AAPL", "name": "苹果 (AAPL)", "type": "Stock"},
    {"symbol": "MSFT", "name": "微软 (MSFT)", "type": "Stock"},
    {"symbol": "AMZN", "name": "亚马逊 (AMZN)", "type": "Stock"},
    {"symbol": "GOOGL", "name": "谷歌 (GOOGL)", "type": "Stock"},
    {"symbol": "META", "name": "Meta (META)", "type": "Stock"},
    {"symbol": "TSLA", "name": "特斯拉 (TSLA)", "type": "Stock"},
    {"symbol": "BRK-B", "name": "伯克希尔 (BRK-B)", "type": "Stock"},
    {"symbol": "AVGO", "name": "博通 (AVGO)", "type": "Stock"},
    {"symbol": "LLY", "name": "礼来 (LLY)", "type": "Stock"},
]

ALERT_THRESHOLD = 3.0  # 涨跌幅绝对值阈值 (3%)


def get_market_data(target):
    """
    获取单个标的的最新价格及单日涨跌幅
    """
    symbol = target["symbol"]
    name = target["name"]
    asset_type = target["type"]

    try:
        ticker = yf.Ticker(symbol)
        # 获取近 5 个交易日的日K线数据
        hist = ticker.history(period="5d", interval="1d")
        
        if hist is not None and len(hist) >= 2:
            prev_close = float(hist["Close"].iloc[-2])
            curr_close = float(hist["Close"].iloc[-1])
            pct_change = ((curr_close - prev_close) / prev_close) * 100.0
            return {
                "symbol": symbol,
                "name": name,
                "type": asset_type,
                "price": curr_close,
                "prev_close": prev_close,
                "pct_change": pct_change,
                "status": "ok"
            }
        elif hist is not None and len(hist) == 1:
            # 降级尝试 fast_info
            fast_info = ticker.fast_info
            curr_close = float(fast_info.last_price or hist["Close"].iloc[-1])
            prev_close = float(fast_info.previous_close)
            if prev_close > 0:
                pct_change = ((curr_close - prev_close) / prev_close) * 100.0
                return {
                    "symbol": symbol,
                    "name": name,
                    "type": asset_type,
                    "price": curr_close,
                    "prev_close": prev_close,
                    "pct_change": pct_change,
                    "status": "ok"
                }
    except Exception as e:
        print(f"[WARN] 获取标的 {name} ({symbol}) 数据失败: {e}", file=sys.stderr)

    return {
        "symbol": symbol,
        "name": name,
        "type": asset_type,
        "status": "error"
    }


def fetch_stocktwits_trending(limit=6):
    """
    抓取 StockTwits 当前外网金融热搜标的并附带详情页链接
    链接格式: https://stocktwits.com/symbol/{symbol}
    """
    url = "https://api.stocktwits.com/api/2/trending/symbols.json"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    trending_list = []
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            symbols = data.get("symbols", [])[:limit]
            for item in symbols:
                sym = item.get("symbol", "").strip()
                title = item.get("title", "").strip()
                if sym:
                    trending_list.append({
                        "symbol": sym,
                        "title": title,
                        "url": f"https://stocktwits.com/symbol/{sym}"
                    })
    except Exception as e:
        print(f"[WARN] 抓取 StockTwits 热搜失败: {e}", file=sys.stderr)

    return trending_list


def fetch_reddit_hot_topics(limit=5):
    """
    抓取 Reddit r/stocks 热门讨论帖并附带完整链接 (https://www.reddit.com + permalink)
    优先使用 old.reddit.com JSON 接口，若受限则降级使用 RSS Feed
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    }
    hot_posts = []

    # 方案 1: old.reddit.com JSON 接口
    try:
        url = "https://old.reddit.com/r/stocks/hot.json?limit=15"
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            children = data.get("data", {}).get("children", [])
            for child in children:
                post = child.get("data", {})
                if post.get("stickied"):  # 过滤置顶每日闲聊帖
                    continue
                title = post.get("title", "").strip()
                score = post.get("score", 0)
                permalink = post.get("permalink", "").strip()
                post_url = f"https://www.reddit.com{permalink}" if permalink.startswith("/") else permalink
                if title and post_url:
                    hot_posts.append({
                        "title": title,
                        "score": score,
                        "url": post_url
                    })
                if len(hot_posts) >= limit:
                    return hot_posts
    except Exception as e:
        print(f"[WARN] Reddit JSON 抓取失败，尝试 RSS: {e}", file=sys.stderr)

    # 方案 2: RSS Feed 降级解析
    try:
        rss_url = "https://www.reddit.com/r/stocks/.rss"
        resp = requests.get(rss_url, headers=headers, timeout=10)
        if resp.status_code == 200:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(resp.content)
            # Atom 命名空间
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            entries = root.findall("atom:entry", ns)
            for entry in entries:
                title_elem = entry.find("atom:title", ns)
                link_elem = entry.find("atom:link", ns)
                if title_elem is not None and title_elem.text:
                    title = title_elem.text.strip()
                    post_url = link_elem.attrib.get("href", "").strip() if link_elem is not None else ""
                    if "Daily Discussion" not in title and "Rate My Portfolio" not in title:
                        hot_posts.append({
                            "title": title,
                            "score": "-",
                            "url": post_url
                        })
                if len(hot_posts) >= limit:
                    break
    except Exception as e:
        print(f"[WARN] Reddit RSS 抓取失败: {e}", file=sys.stderr)

    return hot_posts


def send_bark_notification(bark_key, title, body):
    """
    通过 Bark API 发送推送通知
    """
    if not bark_key:
        print("[WARN] 未检测到 BARK_KEY 环境变量，跳过发送 Bark 消息。")
        return False

    url = "https://api.day.app/push"
    payload = {
        "device_key": bark_key,
        "title": title,
        "body": body,
        "group": "MarketAlert",
        "sound": "alarm",
        "icon": "https://img.icons8.com/fluency/96/stock-share.png"
    }

    try:
        resp = requests.post(url, json=payload, headers={"Content-Type": "application/json; charset=utf-8"}, timeout=10)
        if resp.status_code == 200:
            print("[INFO] Bark 异动提醒推送成功！")
            return True
        
        # 备用方案：GET 请求接口
        encoded_title = requests.utils.quote(title)
        encoded_body = requests.utils.quote(body)
        fallback_url = f"https://api.day.app/{bark_key}/{encoded_title}/{encoded_body}?group=MarketAlert"
        fb_resp = requests.get(fallback_url, timeout=10)
        if fb_resp.status_code == 200:
            print("[INFO] Bark 备用接口推送成功！")
            return True

        print(f"[WARN] Bark 推送返回异常: {resp.status_code} - {resp.text}", file=sys.stderr)
    except Exception as e:
        print(f"[ERROR] 发送 Bark 提醒异常: {e}", file=sys.stderr)

    return False


def build_alert_message(triggered_items, normal_items, stocktwits_trends, reddit_posts):
    """
    组装清晰易读的 Bark 推送内容，优化排版与链接可点击性
    """
    title = f"🚨 市场异动预警 ({len(triggered_items)} 个标的 ≥ 3%)"

    lines = []
    lines.append("⚡【异动触发标的 (≥3%)】")
    for item in triggered_items:
        sign = "+" if item["pct_change"] > 0 else ""
        lines.append(f"• {item['name']}: {sign}{item['pct_change']:.2f}% (现价: {item['price']:.2f})")

    if normal_items:
        lines.append("\n📊【其他核心标的概况】")
        for item in normal_items[:8]:  # 避免消息过长，截取前8个展示
            sign = "+" if item["pct_change"] > 0 else ""
            lines.append(f"• {item['name']}: {sign}{item['pct_change']:.2f}%")

    if stocktwits_trends:
        lines.append("\n🔥【StockTwits 趋势热搜】")
        for idx, item in enumerate(stocktwits_trends, 1):
            lines.append(f"{idx}. ${item['symbol']} ({item['title']})")
            lines.append(f"   🔗 {item['url']}")

    if reddit_posts:
        lines.append("\n💬【Reddit r/stocks 热门讨论】")
        for idx, post in enumerate(reddit_posts, 1):
            score_tag = f"[🔥 {post['score']}] " if post.get("score") and post["score"] != "-" else ""
            lines.append(f"{idx}. {score_tag}{post['title']}")
            lines.append(f"   🔗 {post['url']}")

    body = "\n".join(lines)
    return title, body


def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始监测全球核心市场及标的...")

    market_results = []
    for target in TARGET_ASSETS:
        res = get_market_data(target)
        market_results.append(res)
        if res.get("status") == "ok":
            sign = "+" if res["pct_change"] > 0 else ""
            print(f"  - {res['name']}: {sign}{res['pct_change']:.2f}% (现价: {res['price']:.2f})")
        else:
            print(f"  - {res['name']}: 获取失败")

    # 筛选异动标的
    triggered_items = [
        item for item in market_results
        if item.get("status") == "ok" and abs(item["pct_change"]) >= ALERT_THRESHOLD
    ]
    normal_items = [
        item for item in market_results
        if item.get("status") == "ok" and abs(item["pct_change"]) < ALERT_THRESHOLD
    ]

    if not triggered_items:
        print(f"\n[INFO] 所有标的单日涨跌幅绝对值均未达到 {ALERT_THRESHOLD}% 阈值。静默退出。")
        sys.exit(0)

    print(f"\n[ALERT] 触发异动报警！共有 {len(triggered_items)} 个标的涨跌幅绝对值 >= {ALERT_THRESHOLD}%:")
    for item in triggered_items:
        print(f"   * {item['name']}: {item['pct_change']:+.2f}%")

    # 抓取外网金融热搜
    print("\n正在抓取 StockTwits 与 Reddit 外网金融热搜...")
    stocktwits_trends = fetch_stocktwits_trending(limit=6)
    reddit_posts = fetch_reddit_hot_topics(limit=5)

    # 组装消息
    title, body = build_alert_message(triggered_items, normal_items, stocktwits_trends, reddit_posts)
    print("\n--- 推送内容预览 ---")
    print(f"标题: {title}")
    print(f"正文:\n{body}")
    print("-------------------")

    # 发送 Bark 提醒
    bark_key = os.getenv("BARK_KEY", "").strip()
    send_bark_notification(bark_key, title, body)


if __name__ == "__main__":
    main()
