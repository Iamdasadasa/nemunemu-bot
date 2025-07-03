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

# --- Discord共通設定 ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True  
intents.reactions = True
bot = discord.Bot(intents=intents)

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

# # # # # # # # # # # # # # # # # # # # # # # # # 
#↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓自動対応系↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓#
#↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓#
# # # # # # # # # # # # # # # # # # # # # # # # # 

# --- 新規メンバーのお知らせ（特定チャンネル)　---    
    REPRESENTATIVE_COUNCIL_CHANNEL_ID = 1388357389886951616
    #1389881477033885716
    # --- 管理チャンネルに新規メンバー通知　---    
    @bot.event
    async def on_member_join(member):
        channel = bot.get_channel(REPRESENTATIVE_COUNCIL_CHANNEL_ID)
        if channel:
            username = member.display_name  # サーバー上での表示名（ニックネームがあればそれ）
            await channel.send(f"管理メンバーの皆さま、お手数ですが新たに\n【 {username}】\nさんがサーバーに参加されました。\n"
    "もしお時間のある際にログインされることがありましたら、サークルの承認対応をしていただけますと幸いです。\nよろしくお願いいたします。")

# --- サーバー参加時に「初めてロール」を付与 --- 
    # 対象のロールIDとログを送信するチャンネルID
    ROLE_ID = 1390261208782868590
    REPRESENTATIVE_COUNCIL_CHANNEL_ID = 1388357389886951616
    GUIDE_CHANNEL_ID = 1389290096498315364  # 👈 案内メッセージを送るチャンネルIDに書き換えてね！

@bot.event
async def on_member_join(member):
    guild = member.guild
    role = guild.get_role(ROLE_ID)
    log_channel = guild.get_channel(REPRESENTATIVE_COUNCIL_CHANNEL_ID)
    guide_channel = guild.get_channel(GUIDE_CHANNEL_ID)

    # --- ロール付与処理 ---
    if role:
        try:
            await member.add_roles(role)
            log_msg = f"✅ {member.display_name} さんにロール「{role.name}」を付与しました。"
            print(log_msg)
            if log_channel:
                await log_channel.send(log_msg)
        except discord.Forbidden:
            log_msg = "⚠️ 権限不足でロールを付与できませんでした。"
            print(log_msg)
            if log_channel:
                await log_channel.send(log_msg)
        except Exception as e:
            log_msg = f"❌ エラーが発生しました: {e}"
            print(log_msg)
            if log_channel:
                await log_channel.send(log_msg)
    else:
        log_msg = f"⚠️ ID {ROLE_ID} のロールが見つかりません。"
        print(log_msg)
        if log_channel:
            await log_channel.send(log_msg)

 
 # --- 案内メッセージ送信 ---
    WELCOME_MESSAGE_EXTRA = os.getenv("WELCOME_MESSAGE_EXTRA", "")  # Renderの環境変数から取得
    if guide_channel:
        try:
            guide_msg = ""

            # Renderの環境変数が設定されていたら先頭に追加
            if WELCOME_MESSAGE_EXTRA.strip():
                guide_msg += f"{WELCOME_MESSAGE_EXTRA.strip()}\n\n"

            guide_msg += (
                f"👋 ようこそ {member.mention} さん！\n\n"
                "こちらは初めての方向けの案内チャンネルです。\n"
                "このメッセージにリアクションしていただくことで、次のステップへ進めます。\n"
                "不明点があればお気軽にお尋ねください！"
            )

            await guide_channel.send(guide_msg)

        except Exception as e:
            if log_channel:
                await log_channel.send(f"⚠️ 案内メッセージ送信に失敗しました: {e}")

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


# # # # # # # # # # # # # # # # # # # # # # # # # 
#↓↓↓↓↓↓↓↓↓↓↓↓↓↓スラッシュコマンド系↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓#
#↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓#
# # # # # # # # # # # # # # # # # # # # # # # # # 

# --- Discord Bot 設定 ---
    TOKEN = os.getenv("TOKEN")

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
    @bot.slash_command(name="モンスター抽選", description="モンスターをランダムに教えてくれるよ！")
    async def monster(ctx):
        if MONSTERS:
            name = random.choice(MONSTERS)
            await ctx.respond(f"あなたのモンスターは… 🐲 **{name}** だ！")
        else:
            await ctx.respond("モンスターが見つからなかったよ😢")

#スラッシュコマンド：モンスターリスト更新設定　---
    @bot.slash_command(name="モンスターリスト更新", description="モンスターリストを更新するよ")
    async def update_monsters(ctx):
        await ctx.respond("🔄 モンスターリストを更新中…")
        global MONSTERS
        MONSTERS = fetch_monsters()
        await ctx.send_followup(f"🆙 モンスターリストを更新したよ！現在の数：{len(MONSTERS)}体")

#スラッシュコマンド：パーティ設定　---
    import asyncio
    @bot.slash_command(name="メンバー分け", description="参加リアクションからランダムにパーティを編成するよ！")
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
    EVENT_URL = "https://gamewith.jp/mhwilds/484117"

    def fetch_events():
        res = requests.get(EVENT_URL)
        soup = BeautifulSoup(res.content, "html.parser")
        items = soup.find_all("div", class_="_item")  # ← div に変更

        current_events = []
        upcoming_events = []

        for item in items:
            head = item.find("div", class_="_head")
            title_tag = head.find("a") if head else None
            held_div = head.find("div", class_="_held") if head else None

            if not title_tag:
                continue

            name = title_tag.text.strip()
            link = title_tag["href"]
            status = held_div.text.strip() if held_div else "不明"

            body = item.find("div", class_="_body")
            if not body:
                continue

            info = body.find("div", class_="_info")
            if not info:
                continue

            # ラベルと値を順に取得（HTML構造に基づいて）
            labels = info.find_all("div", class_="_label-9")
            all_divs = info.find_all("div")
            values = []
            skip_next = False
            for i, div in enumerate(all_divs):
                if skip_next:
                    skip_next = False
                    continue
                if div in labels:
                    # 次のdivが値
                    if i + 1 < len(all_divs):
                        values.append(all_divs[i + 1])
                        skip_next = True

            event_info = {"タイトル": name, "URL": link}
            for label, value in zip(labels, values):
                key = label.text.strip()
                val = value.get_text(separator="\n", strip=True)
                event_info[key] = val

            if "開催中" in status:
                current_events.append(event_info)
            elif "開催予定" in status:
                upcoming_events.append(event_info)

        return current_events, upcoming_events



# スラッシュコマンド：開催中イベント
    @bot.slash_command(name="イベント--開催中--", description="現在開催中のイベント一覧を表示します")
    async def current(ctx):
        events, _ = fetch_events()
        if not events:
            await ctx.respond("現在開催中のイベントは見つかりませんでした。")
            return

        for e in events:
            msg = (
                f"🎯 **{e.get('タイトル', '')}**\n"
                f"📅 {e.get('開催期間', '')}\n"
                f"🎯 {e.get('目標', '')}\n"
                f"🎁 {e.get('目玉報酬', '')}\n"
                f"📝 {e.get('条件', '')}\n"
                f"🔗 <{e.get('URL', '')}>"
            )

            await ctx.respond(msg)

# スラッシュコマンド：開催予定イベント
    @bot.slash_command(name="イベント--開催予定--", description="今後開催予定のイベント一覧を表示します")
    async def upcoming(ctx):
        _, events = fetch_events()
        if not events:
            await ctx.respond("開催予定のイベントは見つかりませんでした。")
            return

        for e in events:
            msg = (
                F"\n"
                f"🎯 **{e.get('タイトル', '')}**\n"
                f"📅 {e.get('開催期間', '')}\n"
                f"🎯 __{e.get('目標', '')}__\n"
                f"🎁 {e.get('目玉報酬', '')}\n"
                f"📝 {e.get('条件', '')}\n"
                f"🔗 <{e.get('URL', '')}>"
            )

            await ctx.respond(msg)  

###Bot Run###
bot.run(TOKEN)