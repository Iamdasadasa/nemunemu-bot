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
import asyncio
import time

# --- Discord共通設定 ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True
bot = discord.Bot(intents=intents)
TOKEN = os.getenv("TOKEN")

# --- Flaskアプリ ---
app = Flask(__name__)

# --- Gemini 設定 ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
PROMPT = os.getenv("PROMPT_TEXT", "")
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(model_name="models/gemini-2.0-flash")

# --- X (Twitter API) 認証 ---
client = tweepy.Client(
    consumer_key=os.getenv("API_KEY"),
    consumer_secret=os.getenv("API_SECRET"),
    access_token=os.getenv("ACCESS_TOKEN"),
    access_token_secret=os.getenv("ACCESS_TOKEN_SECRET")
)

# --- 環境変数系 ---
HASHTAGS = """
#モンハンワイルズ
#モンハン
#モンスターハンター
#MHWilds
#モンスターハンターワイルズ
#モンハンワイルズ募集
"""

# リアクション対象メッセージを記録する辞書
guide_messages = {}  # {user_id: message_id}

# ロールID（設定済みかもだけど確認）
ROLE_FIRST_TIMER = 1390261208782868590  # 初めてロール
ROLE_GENERAL = 1390261772853837907      # 一般ロール ←適切なIDに変えて

WELCOME_MESSAGE_EXTRA = os.getenv("WELCOME_MESSAGE_EXTRA", "")
REPRESENTATIVE_COUNCIL_CHANNEL_ID = int(os.getenv("REPRESENTATIVE_COUNCIL_CHANNEL_ID"))
GUIDE_CHANNEL_ID = 1389290096498315364

# --- Flaskエンドポイント ---
@app.route("/")
def home():
    return "👋 統合Bot is alive!", 200

@app.route("/webhook", methods=["POST"])
def webhook_handler():
    if not PROMPT:
        return "❌ PROMPT_TEXT の環境変数が設定されていません。", 500
    try:
        response = model.generate_content(PROMPT)
        result = response.text.strip()
        tweet = f"{result}\n{HASHTAGS.strip()}"
        client.create_tweet(text=tweet)
        print(f"✅ 投稿成功:\n{tweet}")
        return f"✅ ツイート完了:\n{tweet}"
    except Exception as e:
        print(f"❌ 投稿失敗: {e}")
        return str(e), 500

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

# --- 新規メンバー時の処理 ---
@bot.event
async def on_member_join(member):
    guild = member.guild
    role = guild.get_role(ROLE_FIRST_TIMER)
    log_channel = guild.get_channel(REPRESENTATIVE_COUNCIL_CHANNEL_ID)
    guide_channel = guild.get_channel(GUIDE_CHANNEL_ID)

    if log_channel:
        username = member.display_name
        await log_channel.send(
            f"管理メンバーの皆さま、お手数ですが新たに\n【 {username} 】\nさんがサーバーに参加されました。\n"
            "よろしくお願いいたします。"
        )

    if role:
        try:
            await member.add_roles(role)
            log_msg = f"✅ {member.display_name} さんにロール「{role.name}」を付与しました。"
            print(log_msg)
            if log_channel:
                await log_channel.send(log_msg)
        except discord.Forbidden:
            msg = "⚠️ 権限不足でロールを付与できませんでした。"
            if log_channel:
                await log_channel.send(msg)
        except Exception as e:
            if log_channel:
                await log_channel.send(f"❌ ロール付与エラー: {e}")
    else:
        if log_channel:
            await log_channel.send(f"⚠️ ID {ROLE_FIRST_TIMER} のロールが見つかりません。")

    if guide_channel:
        try:
            await asyncio.sleep(5)  # 数秒待ってから送信（アクセス権が反映されるまで待機）
            guide_msg = ""
            if WELCOME_MESSAGE_EXTRA.strip():
                guide_msg += f"{WELCOME_MESSAGE_EXTRA.strip()}\n\n"
            guide_msg += (
                f"👋 ようこそ {member.mention} さん！\n\n"
                "こちらは初めての方向けの案内チャンネルです。\n"
                "このメッセージにリアクションしていただくことで、正式メンバーとなります！。\n"
                "⚠️万が一リアクションを行なってもメンバー権限が付与されない場合はこのチャンネルにメッセージを送信してください。⚠️\n"
                "不明点があればお気軽にお尋ねください！"
            )
            sent_msg = await guide_channel.send(guide_msg)
            guide_messages[member.id] = sent_msg.id
            await sent_msg.add_reaction("✅")  # リアクション要求（任意の絵文字でOK）

        except Exception as e:
            if log_channel:
                await log_channel.send(f"⚠️ 案内メッセージ送信に失敗しました: {e}")

# --- リアクションで権限付与の処理 ---
@bot.event
async def on_raw_reaction_add(payload):
    if payload.user_id == bot.user.id:
        return  # Bot自身のリアクションは無視

    user_id = payload.user_id
    message_id = payload.message_id

    if user_id not in guide_messages:
        return  # 対象メッセージでない

    if guide_messages[user_id] != message_id:
        return  # 自分の案内メッセージじゃない

    guild = bot.get_guild(payload.guild_id)
    member = guild.get_member(user_id)
    if not member:
        return

    role_first = guild.get_role(ROLE_FIRST_TIMER)
    role_general = guild.get_role(ROLE_GENERAL)
    channel = bot.get_channel(payload.channel_id)

    try:
        if role_first in member.roles:
            await member.remove_roles(role_first)
        if role_general:
            await member.add_roles(role_general)
        msg = await channel.fetch_message(message_id)
        await msg.delete()
        del guide_messages[user_id]
    except Exception as e:
        log_channel = guild.get_channel(REPRESENTATIVE_COUNCIL_CHANNEL_ID)
        if log_channel:
            await log_channel.send(f"⚠️ リアクションによるロール変更エラー: {e}")

# --- モンスター関連コマンド ---
def fetch_monsters():
    url = "https://gamewith.jp/mhwilds/452222"
    res = requests.get(url)
    soup = BeautifulSoup(res.content, "html.parser")
    return [li.get("data-name", "").strip() for li in soup.select("ol.monster_weak_list li[data-name]") if li.get("data-name")]

MONSTERS = fetch_monsters()

@bot.slash_command(name="モンスター抽選", description="モンスターをランダムに教えてくれるよ！")
async def monster(ctx):
    if MONSTERS:
        name = random.choice(MONSTERS)
        await ctx.respond(f"あなたのモンスターは… 🐲 **{name}** だ！")
    else:
        await ctx.respond("モンスターが見つからなかったよ😢")

@bot.slash_command(name="モンスターリスト更新", description="モンスターリストを更新するよ")
async def update_monsters(ctx):
    await ctx.respond("🔄 モンスターリストを更新中…")
    global MONSTERS
    MONSTERS = fetch_monsters()
    await ctx.send_followup(f"🆙 モンスターリストを更新したよ！現在の数：{len(MONSTERS)}体")

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

# --- イベント取得系 ---
EVENT_URL = "https://gamewith.jp/mhwilds/484117"
def fetch_events():
    res = requests.get(EVENT_URL)
    soup = BeautifulSoup(res.content, "html.parser")
    items = soup.find_all("div", class_="_item")
    current_events, upcoming_events = [], []
    for item in items:
        head = item.find("div", class_="_head")
        title_tag = head.find("a") if head else None
        held_div = head.find("div", class_="_held") if head else None
        if not title_tag: continue
        name = title_tag.text.strip()
        link = title_tag["href"]
        status = held_div.text.strip() if held_div else "不明"
        body = item.find("div", class_="_body")
        if not body: continue
        info = body.find("div", class_="_info")
        if not info: continue
        labels = info.find_all("div", class_="_label-9")
        all_divs = info.find_all("div")
        values = []
        skip_next = False
        for i, div in enumerate(all_divs):
            if skip_next:
                skip_next = False
                continue
            if div in labels:
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

@bot.slash_command(name="イベント開催中", description="現在開催中のイベント一覧を表示します")
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

@bot.slash_command(name="イベント開催予定", description="今後開催予定のイベント一覧を表示します")
async def upcoming(ctx):
    _, events = fetch_events()
    if not events:
        await ctx.respond("開催予定のイベントは見つかりませんでした。")
        return
    for e in events:
        msg = (
            f"\n🎯 **{e.get('タイトル', '')}**\n"
            f"📅 {e.get('開催期間', '')}\n"
            f"🎯 __{e.get('目標', '')}__\n"
            f"🎁 {e.get('目玉報酬', '')}\n"
            f"📝 {e.get('条件', '')}\n"
            f"🔗 <{e.get('URL', '')}>"
        )
        await ctx.respond(msg)

# --- クエスト募集スラッシュコマンド ---
@bot.slash_command(name="狩り募集", description="クエスト募集メッセージを投稿します")
async def quest_post(
    ctx,
    時間: str,
    募集テンプレ内容: discord.Option(str, choices=["バウンティ消化", "クエストお手伝い","HR上げ", "素材集め", "金策", "写真撮りたい", "募集カスタムに記載"]),
    場所: discord.Option(discord.VoiceChannel, description="VCチャンネルを選択"),
    募集カスタム内容: str = "",
    人数: str = "",
    一言: str = ""
):
    内容 = カスタム内容 if カスタム内容 else テンプレ内容

    embed = discord.Embed(title="🎯 クエスト募集のお知らせ", color=0x4CAF50)
    embed.add_field(name="⏰ 時間", value=f"→ {時間}", inline=False)
    embed.add_field(name="📝 内容", value=f"→ {内容}", inline=False)
    embed.add_field(name="📍 場所", value=f"→ {場所.name}", inline=False)
    embed.add_field(name="👥 人数", value=f"→ {人数}", inline=False)
    if 一言:
        embed.add_field(name="💬 一言", value=f"→ {一言}", inline=False)

    await ctx.respond("@everyone", embed=embed)

# --- スラッシュコマンドはここより上へ！ ---
@bot.event
async def on_ready():
    try:
        print("✅ on_ready() に入りました！")
        print(f"✅ ログインユーザー: {bot.user} (ID: {bot.user.id})")
        await bot.sync_commands()
        print("✅ スラッシュコマンドの同期に成功しました")
    except Exception as e:
        import traceback
        print(f"❌ on_ready() 内でエラー発生: {e}")
        traceback.print_exc()

# --- 起動処理 ---
if __name__ == "__main__":
    while True:
        try:
            bot.run(TOKEN)
            break  # 正常終了したらループ抜ける（必要に応じて）
        except discord.HTTPException as e:
            if "429" in str(e) or "Too Many Requests" in str(e):
                print("❌ 429 Too Many Requests 発生。1時間停止して再試行します…")
                time.sleep(3600)  # 3600秒 = 1時間
            else:
                raise