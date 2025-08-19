import os
import random
import requests
import threading
import discord
from bs4 import BeautifulSoup
from flask import Flask, request
from discord.ext import commands, tasks
from datetime import time as dtime, timedelta, timezone
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

# ---- VC 管理用の一時ストア ----
JST = timezone(timedelta(hours=9))
# Botが作った一時VCの記録: {vc_id: {"owner_id": int, "thread_id": int, "created_at": datetime}}
TEMP_VCS: dict[int, dict] = {}
# スレッドID→VCID 逆引き
THREAD_TO_VC: dict[int, int] = {}
# パスコード→VCID（シンプル版：コードは平文でメモリ保持）
VC_PASSCODES: dict[str, int] = {}

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
        mention_link = f"<@{member.id}>"  # メンションリンク（通知なし）
        await log_channel.send(
            f"管理メンバーの皆さま、お手数ですが新たに\n\\{mention_link}\nさんがサーバーに参加されました。\n"
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
@bot.slash_command(name="狩り募集", description="クエスト募集メッセージを投稿します（必要ならVCも同時作成）")
async def quest_post(
    # === 必須（required=True）===
    時間: discord.Option(str, description="集合・出発時間を入力（例: 21時～）", required=True),
    募集テンプレ内容: discord.Option(
        str,
        description="よくある募集内容から選んでね（カスタムがあれば優先）",
        choices=["バウンティ消化", "クエストお手伝い", "HR上げ", "素材集め", "金策", "写真撮りたい！", "募集カスタムに記載"],
        required=True
    ),
    人数: discord.Option(str, description="募集人数（例: 4人, 5名）", required=True),

    # === 任意（required=False）===
    場所: discord.Option(discord.VoiceChannel, description="既存VCを使う場合はここで選択", required=False, default=None),
    募集カスタム内容: discord.Option(str, description="自由メモ（テンプレを上書き）", required=False, default=""),
    VC_作成: discord.Option(bool, description="募集と同時に一時VCを作成しますか？", required=False, default=False),
    VC_名称: discord.Option(str, description="作成するVCの名前（未指定なら自動）", required=False, default=""),
    VC_人数上限: discord.Option(int, description="VCの人数上限（1〜99）", required=False, default=0),
    VC_プライベート: discord.Option(bool, description="一般には見せず入室制にする", required=False, default=True),
    VC_パスワード: discord.Option(str, description="入室パスコード（任意・指定した人だけ入れる）", required=False, default="")
):
    await ctx.defer()

    内容 = 募集カスタム内容 if 募集カスタム内容 else 募集テンプレ内容

    embed = discord.Embed(title=f"🎯 クエスト募集（by {ctx.author.mention}）", color=0x4CAF50)
    embed.add_field(name="⏰ 時間", value=f"→ __{時間}__", inline=False)
    embed.add_field(name="📝 内容", value=f"→ __{内容}__", inline=False)

    created_vc = None
    used_vc = 場所  # 既存VCが指定されたらそれを使う

    # ---- VC自動作成 ----
    if vc_create:
        parent_category = ctx.channel.category

        overwrites = {}
        if vc_private or vc_passcode:
            # みんなは接続不可
            overwrites[ctx.guild.default_role] = discord.PermissionOverwrite(view_channel=False, connect=False)
            # 発起人は入れる
            overwrites[ctx.author] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True)

        # VC名
        name = vc_name.strip() if vc_name.strip() else f"募集VC：{ctx.author.name}"

        created_vc = await ctx.guild.create_voice_channel(
            name=name,
            category=parent_category,
            overwrites=overwrites or None,
            user_limit=(vc_limit if 1 <= vc_limit <= 99 else None),
            reason=f"{ctx.author} の募集に合わせてBotが作成"
        )
        used_vc = created_vc

        # パスコード接続を有効化（保持）
        if vc_passcode.strip():
            VC_PASSCODES[vc_passcode.strip()] = created_vc.id

    # ---- 埋め込みにVC情報反映 ----
    if used_vc:
        embed.add_field(name="📍 場所", value=f"→ __{used_vc.name}__", inline=False)
    else:
        embed.add_field(name="📍 場所", value="→ __テキスト募集（VC指定なし）__", inline=False)

    embed.add_field(name="👥 人数", value=f"→ __{人数}__", inline=False)
    if 募集カスタム内容:
        embed.add_field(name="💬 補足", value=f"→ {募集カスタム内容}", inline=False)

    resp = await ctx.respond(embed=embed)
    original_msg = await resp.original_response()

    # 募集スレッドを作る
    thread = await original_msg.create_thread(
        name=f"{ctx.author.name}の募集スレッド",
        auto_archive_duration=60  # 1時間
    )

    # VCとスレッドのひも付け（Bot作成VCのみ）
    if created_vc:
        TEMP_VCS[created_vc.id] = {
            "owner_id": ctx.author.id,
            "thread_id": thread.id,
            "created_at": discord.utils.utcnow()
        }
        THREAD_TO_VC[thread.id] = created_vc.id

        # パスコード案内
        if vc_passcode.strip():
            await thread.send(
                f"🔐 このVCはパスコード制です。\n"
                f"入室したい方は `/vc入室 code:{vc_passcode.strip()}` を実行してください。\n"
                f"（実行した人だけ、このVCへの接続許可が自動で付きます）"
            )

@bot.slash_command(name="vc入室", description="パスコードを入力して、対象VCへの接続権限を付与します")
async def vc_join(ctx, code: discord.Option(str, description="配布されたパスコード")):
    vc_id = VC_PASSCODES.get(code.strip())
    if not vc_id:
        await ctx.respond("❌ パスコードが無効です。", ephemeral=True)
        return

    channel = ctx.guild.get_channel(vc_id)
    if not channel or not isinstance(channel, discord.VoiceChannel):
        await ctx.respond("❌ 対象のVCが見つかりません。", ephemeral=True)
        return

    try:
        await channel.set_permissions(
            ctx.author,
            view_channel=True,
            connect=True,
            speak=True
        )
        await ctx.respond(f"✅ `{channel.name}` への入室権限を付与しました。", ephemeral=True)
    except discord.Forbidden:
        await ctx.respond("⚠️ 権限不足で許可を付与できませんでした。", ephemeral=True)

@bot.event
async def on_thread_update(before: discord.Thread, after: discord.Thread):
    if before.archived is False and after.archived is True:
        vc_id = THREAD_TO_VC.get(after.id)
        if not vc_id:
            return
        channel = after.guild.get_channel(vc_id)
        if channel and isinstance(channel, discord.VoiceChannel):
            try:
                await channel.delete(reason="募集スレッドのアーカイブに伴い自動削除")
            finally:
                TEMP_VCS.pop(vc_id, None)
                THREAD_TO_VC.pop(after.id, None)
                # パスコード紐付けも掃除
                for code, _vc in list(VC_PASSCODES.items()):
                    if _vc == vc_id:
                        VC_PASSCODES.pop(code, None)

# --- 日次クリーンアップタスク ---
@tasks.loop(time=dtime(hour=8, minute=0, tzinfo=JST))
async def daily_cleanup_vcs():
    # 全ギルドを横断して、TEMP_VCSに記録されたチャンネルだけ削除
    for vc_id in list(TEMP_VCS.keys()):
        for guild in bot.guilds:
            ch = guild.get_channel(vc_id)
            if ch and isinstance(ch, discord.VoiceChannel):
                try:
                    await ch.delete(reason="日次クリーンアップ（Bot作成VC）")
                except Exception:
                    pass
        TEMP_VCS.pop(vc_id, None)

    # パスコードも全消し
    VC_PASSCODES.clear()
    THREAD_TO_VC.clear()

@daily_cleanup_vcs.before_loop
async def before_cleanup():
    await bot.wait_until_ready()

# --- スラッシュコマンドはここより上へ！ ---
@bot.event
async def on_ready():
    try:
        print("✅ on_ready() に入りました！")
        print(f"✅ ログインユーザー: {bot.user} (ID: {bot.user.id})")
        await bot.sync_commands()
        print("✅ スラッシュコマンドの同期に成功しました")
        if not daily_cleanup_vcs.is_running():
            daily_cleanup_vcs.start()
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