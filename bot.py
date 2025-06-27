import os
import random
import requests
import threading
import discord
from bs4 import BeautifulSoup
from flask import Flask, request
from discord.ext import commands
from discord import option
import google.generativeai as genai
import tweepy

# --- Flaskアプリ共通 ---
app = Flask(__name__)

# --- Gemini 設定 ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
PROMPT = os.getenv("PROMPT_TEXT")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(model_name="models/gemini-2.0-flash")

# --- X (v2 API) 認証 ---
client = tweepy.Client(
    consumer_key=os.getenv("API_KEY"),
    consumer_secret=os.getenv("API_SECRET"),
    access_token=os.getenv("ACCESS_TOKEN"),
    access_token_secret=os.getenv("ACCESS_TOKEN_SECRET")
)

# --- 固定ハッシュタグ ---
HASHTAGS = """
#モンハンワイルズ
#モンハン
#モンスターハンター
#MHWilds
#モンスターハンターワイルズ
#モンハンワイルズ募集
"""

# --- Flaskエンドポイント ---
@app.route("/")
def home():
    return "👋 統合Bot is alive!", 200

# --- Xポスト　---
@app.route("/webhook", methods=["POST"])
def webhook_handler():
    if not PROMPT:
        return "❌ PROMPT_TEXT の環境変数が設定されていません。", 500
    try:
        # Gemini で文章生成
        response = model.generate_content(PROMPT)
        result = response.text.strip()
        tweet = f"{result}\n{HASHTAGS.strip()}"

        # X (v2) に投稿
        client.create_tweet(text=tweet)
        print(f"✅ 投稿成功:\n{tweet}")
        return f"✅ ツイート完了:\n{tweet}"
    except Exception as e:
        print(f"❌ 投稿失敗: {e}")
        return str(e), 500

# --- Xポスト規制内容表示　---
@app.route("/ratelimit", methods=["GET"])
def check_rate_limit():
    try:
        url = "https://api.twitter.com/2/tweets?ids=20"
        auth = client.session.auth
        res = requests.get(url, auth=auth)
        limit = res.headers.get("x-rate-limit-limit", "N/A")
        remaining = res.headers.get("x-rate-limit-remaining", "N/A")
        reset = res.headers.get("x-rate-limit-reset", "N/A")
        return f"""✅ Rate Limit Info:
- limit: {limit}
- remaining: {remaining}
- reset: {reset} (Unix time)
""", 200
    except Exception as e:
        return f"❌ レート情報の取得に失敗しました: {e}", 500

# --- Flaskをバックグラウンドで実行 ---
def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_flask, daemon=True).start()

# --- Discord Bot 設定 ---
TOKEN = os.getenv("TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
bot = discord.Bot(intents=intents)

#スラッシュコマンド：モンスター取得関数 ---
def fetch_monsters():
    url = "https://gamewith.jp/mhwilds/452222"
    res = requests.get(url)
    soup = BeautifulSoup(res.content, "html.parser")
    return [li.get("data-name", "").strip() for li in soup.select("ol.monster_weak_list li[data-name]") if li.get("data-name")]

MONSTERS = fetch_monsters()

@bot.event
async def on_ready():
    print(f"✅ {bot.user} でログインしました！")

#スラッシュコマンド：モンスターランダム排出　---
@bot.slash_command(name="monster", description="モンスターをランダムに教えてくれるよ！")
async def monster(ctx):
    if MONSTERS:
        name = random.choice(MONSTERS)
        await ctx.respond(f"あなたのモンスターは… 🐲 **{name}** だ！")
    else:
        await ctx.respond("モンスターが見つからなかったよ😢")

#スラッシュコマンド：モンスターリスト更新設定　---
@bot.slash_command(name="update_monsters", description="モンスターリストを更新するよ")
async def update_monsters(ctx):
    await ctx.respond("🔄 モンスターリストを更新中…")
    global MONSTERS
    MONSTERS = fetch_monsters()
    await ctx.send_followup(f"🆙 モンスターリストを更新したよ！現在の数：{len(MONSTERS)}体")

#スラッシュコマンド：パーティ設定　---
@bot.slash_command(name="party", description="参加リアクションからランダムにパーティを編成するよ！")
async def party(ctx, size: int = 4):
    if size < 1:
        await ctx.respond("パーティ人数は1人以上にしてね❌", ephemeral=True)
        return

    msg = await ctx.respond(f"🙋‍♂️ パーティ編成！参加したい人はリアクションしてね！（{size}人ずつ/※60秒後に締め切ります）")
    original = await msg.original_response()
    await original.add_reaction("🙋")

    await asyncio.sleep(60)

    updated = await ctx.channel.fetch_message(original.id)
    users = await updated.reactions[0].users().flatten()
    users = [u for u in users if not u.bot]

    if len(users) < size:
        await ctx.followup.send("😢 参加者が足りなかったよ…")
        return

    random.shuffle(users)
    group_count = (len(users) + size - 1) // size
    base_size = len(users) // group_count
    remainder = len(users) % group_count

    groups, start = [], 0
    for i in range(group_count):
        extra = 1 if i < remainder else 0
        end = start + base_size + extra
        groups.append(users[start:end])
        start = end

    result = "\n\n".join([f"🧩 パーティ {i+1}:\n" + "\n".join([f"- {u.mention}" for u in g]) for i, g in enumerate(groups)])
    await ctx.followup.send(f"✅ パーティ編成完了！\n{result}")

# --- イベント取得　---
EVNETURL = "https://gamewith.jp/mhwilds/504611"
def fetch_events():
    res = requests.get(EVNETURL)
    soup = BeautifulSoup(res.content, "html.parser")
    tables = soup.find_all("table", class_="quest_board")

    current_events = []
    upcoming_events = []

    for table in tables:
        rows = table.find_all("tr")
        event_info = {}
        for row in rows:
            th = row.find("th")
            td = row.find("td")
            if not th or not td:
                continue

            title = th.text.strip()
            value = td.get_text(separator="\n", strip=True)

            if "開催期間" in title:
                event_info["期間"] = value
            elif "ミッション" in title:
                event_info["ミッション"] = value
            elif "マップ" in title:
                event_info["マップ"] = value
            elif "目玉報酬" in title:
                event_info["報酬"] = value

        if event_info:
            if "〜" in event_info.get("期間", ""):
                current_events.append(event_info)
            elif "〜" not in event_info.get("期間", ""):
                upcoming_events.append(event_info)

    return current_events, upcoming_events

#スラッシュコマンド：開催中イベント表示
@bot.slash_command(name="開催中", description="現在開催中のイベントを表示するよ！")
async def current(ctx):
    await ctx.respond("🔍 現在開催中のイベントを取得中…")
    events, _ = fetch_events()
    if not events:
        await ctx.send_followup("現在開催中のイベントは見つかりませんでした。")
        return

    for e in events:
        msg = f"🎯 **{e.get('ミッション')}**\n📍 {e.get('マップ')}\n🎁 {e.get('報酬')}\n📅 {e.get('期間')}"
        await ctx.send_followup(msg)

#スラッシュコマンド：開催予定イベント表示
@bot.slash_command(name="開催予定", description="これから開催されるイベントを表示するよ！")
async def upcoming(ctx):
    await ctx.respond("🔍 開催予定のイベントを取得中…")
    _, events = fetch_events()
    if not events:
        await ctx.send_followup("開催予定のイベントは見つかりませんでした。")
        return

    for e in events:
        msg = f"🕒 **{e.get('ミッション')}**\n📍 {e.get('マップ')}\n🎁 {e.get('報酬')}\n📅 {e.get('期間')}"
        await ctx.send_followup(msg)

###Bot Run###
bot.run(TOKEN)