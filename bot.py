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
import re

import sys, logging

# --- Stdout immediate flush & logging setup ---
try:
    # Render等の環境でログを即時出力
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,  # 必要に応じて DEBUG
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
try:
    # discord.py/py-cord の内部ログも出す
    discord.utils.setup_logging(level=logging.INFO)
except Exception:
    pass

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

# ---- 共通：環境変数のリストをパースする小道具 ----
def _parse_env_list(raw: str) -> list[str]:
    """
    カンマ区切り または 改行区切りの文字列をリストに変換。
    空要素は除去し、前後の空白はトリム。
    """
    if not raw:
        return []
    # カンマも改行もセパレータとして扱う
    parts = [p.strip() for p in re.split(r"[,\n]+", raw) if p.strip()]
    return parts

# ---- 日本語有効/無効・bool解釈ヘルパ ----
def _ja_bool(val):
    """
    "有効"/"無効"（str）や bool を受け取り、True/False を返す。
    """
    if isinstance(val, str):
        return val.strip() == "有効"
    return bool(val)

# ---- 武器リスト（環境変数優先・なければ既定） ----
_DEFAULT_WEAPONS = [
    "大剣","太刀","片手剣","双剣","ハンマー","狩猟笛",
    "ランス","ガンランス","スラッシュアックス","チャージアックス",
    "操虫棍","ライトボウガン","ヘビィボウガン","弓"
]
WEAPON_LIST_RAW = os.getenv("WEAPON_LIST", "")
WEAPONS = _parse_env_list(WEAPON_LIST_RAW) or _DEFAULT_WEAPONS

# ---- エリアリスト（環境変数依存・既定は無し） ----
AREA_LIST_RAW = os.getenv("AREA_LIST", "")
AREAS = _parse_env_list(AREA_LIST_RAW)

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
VC_CATEGORY_ID = int(os.getenv("VC_CATEGORY_ID", "0"))
REPRESENTATIVE_COUNCIL_CHANNEL_ID = int(os.getenv("REPRESENTATIVE_COUNCIL_CHANNEL_ID"))
GUIDE_CHANNEL_ID = 1389290096498315364

ADMIN_LOG_CHANNEL_ID = int(os.getenv("ADMIN_LOG_CHANNEL_ID", "0"))

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

# --- Gateway 状態ログ ---
@bot.event
async def on_connect():
    print("[GATEWAY] on_connect (ソケット接続は確立)", flush=True)

@bot.event
async def on_resumed():
    print("[GATEWAY] on_resumed (セッション再開)", flush=True)

@bot.event
async def on_disconnect():
    print("[GATEWAY] on_disconnect (切断)", flush=True)

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

@bot.slash_command(name="203_モンスター抽選", description="モンスターをランダムに教えてくれるよ！")
async def monster(ctx):
    if MONSTERS:
        name = random.choice(MONSTERS)
        await ctx.respond(f"あなたのモンスターは… 🐲 **{name}** だ！")
    else:
        await ctx.respond("モンスターが見つからなかったよ😢")

@bot.slash_command(name="202_モンスターリスト更新", description="モンスターリストを更新するよ")
async def update_monsters(ctx):
    await ctx.respond("🔄 モンスターリストを更新中…")
    global MONSTERS
    MONSTERS = fetch_monsters()
    await ctx.send_followup(f"🆙 モンスターリストを更新したよ！現在の数：{len(MONSTERS)}体")


@bot.slash_command(name="201_メンバー分け", description="参加リアクションからランダムにパーティを編成するよ！")
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

# --- エリア抽選（便利ツール系） ---
@bot.slash_command(name="205_エリア抽選", description="環境変数 AREA_LIST からエリアをランダム抽選します")
async def area_draw(
    ctx,
    数: discord.Option(int, description="抽選する個数（1以上）", required=False, default=1),
    重複許可: discord.Option(str, description="同じエリアが複数回出てもよい（有効/無効）", choices=["有効", "無効"], required=False, default="無効")
):
    重複許可 = _ja_bool(重複許可)
    if not AREAS:
        await ctx.respond(
            "❌ エリア一覧が空です。Renderの環境変数 `AREA_LIST` にエリア名をカンマまたは改行で設定してください。\n"
            "例: 草原, 砂漠, 雪山\n再デプロイ後にお試しください。",
            ephemeral=True
        )
        return

    if 数 < 1:
        await ctx.respond("抽選個数は1以上にしてね❌", ephemeral=True)
        return

    if 重複許可:
        picks = [random.choice(AREAS) for _ in range(数)]
    else:
        if 数 > len(AREAS):
            await ctx.respond(f"重複なしでは最大 {len(AREAS)} 個までです❌", ephemeral=True)
            return
        picks = random.sample(AREAS, k=数)

    if len(picks) == 1:
        await ctx.respond(f"🗺️ 本日のエリアは… **{picks[0]}**！")
    else:
        lines = "\n".join([f"- {a}" for a in picks])
        await ctx.respond(f"🗺️ 抽選結果 ({数}件)\n{lines}")

# --- エリアリロード（管理者専用） ---
@bot.slash_command(
    name="299_エリアリロード",
    description="エリア一覧を再読み込みします（管理者専用）",
    default_member_permissions=discord.Permissions(administrator=True),
    dm_permission=False
)
async def area_reload(ctx):
    if not ctx.author.guild_permissions.administrator:
        await ctx.respond("❌ このコマンドは管理者のみ実行できます。", ephemeral=True)
        return

    global AREAS
    new_raw = os.getenv("AREA_LIST", "")
    AREAS = _parse_env_list(new_raw)

    await ctx.respond(
        "🔄 エリア一覧を再読み込みしました。\n"
        f"現在の登録数: {len(AREAS)} 件\n"
        "※ Renderでは環境変数の変更は通常、再デプロイ後に反映されます。",
        ephemeral=True
    )

# --- 武器抽選（便利ツール系） ---
@bot.slash_command(name="204_武器抽選", description="武器一覧からランダムに選びます")
async def weapon_draw(
    ctx,
    数: discord.Option(int, description="抽選する個数（1以上）", required=False, default=1),
    重複許可: discord.Option(str, description="同じ武器が複数回出てもよい（有効/無効）", choices=["有効", "無効"], required=False, default="無効")
):
    重複許可 = _ja_bool(重複許可)
    if not WEAPONS:
        await ctx.respond(
            "❌ 武器一覧が空です。Renderの環境変数 `WEAPON_LIST` に武器名をカンマまたは改行で設定してください。\n"
            "例: 大剣, 太刀, 片手剣\n再デプロイ後にお試しください。",
            ephemeral=True
        )
        return

    if 数 < 1:
        await ctx.respond("抽選個数は1以上にしてね❌", ephemeral=True)
        return

    if 重複許可:
        picks = [random.choice(WEAPONS) for _ in range(数)]
    else:
        if 数 > len(WEAPONS):
            await ctx.respond(f"重複なしでは最大 {len(WEAPONS)} 個までです❌", ephemeral=True)
            return
        picks = random.sample(WEAPONS, k=数)

    if len(picks) == 1:
        await ctx.respond(f"🎲 本日の武器は… **{picks[0]}**！")
    else:
        lines = "\n".join([f"- {w}" for w in picks])
        await ctx.respond(f"🎲 抽選結果 ({数}件)\n{lines}")

# --- 武器リロード（管理者専用） ---
@bot.slash_command(
    name="299_武器リロード",
    description="武器一覧を再読み込みします（管理者専用）",
    default_member_permissions=discord.Permissions(administrator=True),
    dm_permission=False
)
async def weapon_reload(ctx):
    if not ctx.author.guild_permissions.administrator:
        await ctx.respond("❌ このコマンドは管理者のみ実行できます。", ephemeral=True)
        return

    global WEAPONS
    new_raw = os.getenv("WEAPON_LIST", "")
    WEAPONS = _parse_env_list(new_raw) or _DEFAULT_WEAPONS

    await ctx.respond(
        "🔄 武器一覧を再読み込みしました。\n"
        f"現在の登録数: {len(WEAPONS)} 件\n"
        "※ Renderでは環境変数の変更は通常、再デプロイ後に反映されます。",
        ephemeral=True
    )

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

@bot.slash_command(name="301_イベント開催中", description="現在開催中のイベント一覧を表示します")
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

@bot.slash_command(name="302_イベント開催予定", description="今後開催予定のイベント一覧を表示します")
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
@bot.slash_command(name="101_狩り募集", description="クエスト募集メッセージを投稿します（必要ならVCも同時作成）")
async def quest_post(
    ctx,
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
    ボイスルーム_作成: discord.Option(str, description="募集と同時に一時VCを作成しますか？（有効/無効）", choices=["有効", "無効"], required=False, default="無効"),
    ボイスルーム_名称: discord.Option(str, description="作成するVCの名前（未指定なら自動）", required=False, default=""),
    ボイスルーム_パスワード: discord.Option(str, description="入室パスコード（任意・指定した人だけ入れる）", required=False, default="")
):
    await ctx.defer()

    内容 = 募集カスタム内容 if 募集カスタム内容 else 募集テンプレ内容
    ボイスルーム_作成 = _ja_bool(ボイスルーム_作成)

    embed = discord.Embed(title=f"🎯 クエスト募集（by {ctx.author.mention}）", color=0x4CAF50)
    embed.add_field(name="⏰ 時間", value=f"→ __{時間}__", inline=False)
    embed.add_field(name="📝 内容", value=f"→ __{内容}__", inline=False)

    created_vc = None
    used_vc = 場所  # 既存VCが指定されたらそれを使う

    # 人数（必須）からVC上限を推定（"4人", "5名" などから数値を抽出）
    def _extract_limit(s: str) -> int | None:
        m = re.search(r"\d+", s)
        if not m:
            return None
        n = int(m.group())
        if 1 <= n <= 99:
            return n
        return None

    vc_limit = _extract_limit(人数)

    # ---- VC自動作成 ----
    if ボイスルーム_作成:
        parent_category = ctx.guild.get_channel(VC_CATEGORY_ID) if VC_CATEGORY_ID else ctx.channel.category

        # 作成者がそのVCを管理できるようにするオーバーライド（編集/削除に必要な権限を付与）
        author_overwrite = discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            move_members=True,
            mute_members=True,
            deafen_members=True,
            manage_channels=True,      # このチャンネルの編集/削除
            manage_permissions=True    # このチャンネルの権限編集
        )

        # パスコード指定時は一般公開にせず、作成者だけ見える/入れる
        if ボイスルーム_パスワード and ボイスルーム_パスワード.strip():
            overwrites = {
                ctx.guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False),
                ctx.author: author_overwrite,
            }
        else:
            # 公開VCだが、作成者には管理権限を与える
            overwrites = {
                ctx.author: author_overwrite,
            }

        # VC名
        name = ボイスルーム_名称.strip() if ボイスルーム_名称.strip() else f"募集VC：{ctx.author.name}"

        created_vc = await ctx.guild.create_voice_channel(
            name=name,
            category=parent_category,
            overwrites=overwrites,
            user_limit=vc_limit,
            reason=f"{ctx.author} の募集に合わせてBotが作成"
        )
        used_vc = created_vc

        # パスコード接続を有効化（保持）
        if ボイスルーム_パスワード.strip():
            VC_PASSCODES[ボイスルーム_パスワード.strip()] = created_vc.id

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
        if ボイスルーム_パスワード.strip():
            await thread.send(
                f"🔐 このVCはパスコード制です。\n"
                f"入室したい方は `/102_パス付きボイスルーム入室 code:{ボイスルーム_パスワード.strip()}` を実行してください。\n"
                f"（実行した人だけ、このVCへの接続許可が自動で付きます）"
            )

@bot.slash_command(name="102_パス付きボイスルーム入室", description="パスコードを入力して、対象VCへの接続権限を付与します")
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


# --- VC削除コマンド ---
@bot.slash_command(name="103_vc削除", description="Botが作った一時VCを削除します（作成者または管理者）")
async def vc_delete(
    ctx,
    対象: discord.Option(discord.VoiceChannel, description="削除するVC（未指定なら現在地かスレッド関連を自動推定）", required=False, default=None)
):
    # 推定ロジック：
    target_ch = 対象

    # 1) 未指定なら、実行者が今いるVC
    if target_ch is None and isinstance(ctx.author, discord.Member) and ctx.author.voice and ctx.author.voice.channel:
        if isinstance(ctx.author.voice.channel, discord.VoiceChannel):
            target_ch = ctx.author.voice.channel

    # 2) それでも無ければ、実行されたチャンネルがスレッドで、紐づくVCがあればそれ
    if target_ch is None and isinstance(ctx.channel, discord.Thread):
        vc_id = THREAD_TO_VC.get(ctx.channel.id)
        if vc_id:
            ch = ctx.guild.get_channel(vc_id)
            if isinstance(ch, discord.VoiceChannel):
                target_ch = ch

    if target_ch is None or not isinstance(target_ch, discord.VoiceChannel):
        await ctx.respond("❌ 対象のVCが特定できません。オプションでVCを指定するか、VCに入ってから実行してください。", ephemeral=True)
        return

    # Botが作ったVCか確認
    meta = TEMP_VCS.get(target_ch.id)
    if not meta:
        await ctx.respond("⚠️ このVCはBot管理対象ではありません（手動作成か、既にメタ情報が破棄されています）。", ephemeral=True)
        return

    owner_id = meta.get("owner_id")
    is_admin = ctx.author.guild_permissions.administrator
    if not (is_admin or ctx.author.id == owner_id):
        await ctx.respond("❌ このVCを削除できるのは作成者か管理者のみです。", ephemeral=True)
        return

    # 削除実行
    try:
        await target_ch.delete(reason=f"{ctx.author} による /103_vc削除 実行")
    except discord.Forbidden:
        await ctx.respond("⚠️ 権限不足で削除できませんでした（Botに『チャンネルを管理』権限が必要です）。", ephemeral=True)
        return
    except Exception as e:
        await ctx.respond(f"❌ 削除中にエラー: {e}", ephemeral=True)
        return

    # メタ掃除
    TEMP_VCS.pop(target_ch.id, None)
    # 逆引き
    for th_id, vcid in list(THREAD_TO_VC.items()):
        if vcid == target_ch.id:
            THREAD_TO_VC.pop(th_id, None)
    # パスコード紐付けも掃除
    for code, vcid in list(VC_PASSCODES.items()):
        if vcid == target_ch.id:
            VC_PASSCODES.pop(code, None)

    await ctx.respond("🗑️ VCを削除しました。", ephemeral=True)

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

# --- 管理者: 日次クリーンアップ即時実行コマンド ---
@bot.slash_command(
    name="299_日次クリーン実行",
    description="Bot作成の一時VCを即時クリーン（管理者専用）",
    default_member_permissions=discord.Permissions(administrator=True),
    dm_permission=False
)
async def daily_cleanup_now(ctx):
    # パーミッション確認（念のため）
    if not ctx.author.guild_permissions.administrator:
        await ctx.respond("❌ このコマンドは管理者のみ実行できます。", ephemeral=True)
        return

    await ctx.defer(ephemeral=True)

    deleted = 0
    failed = 0

    # TEMP_VCS に記録されているVCだけを対象に削除
    for vc_id in list(TEMP_VCS.keys()):
        for guild in bot.guilds:
            ch = guild.get_channel(vc_id)
            if ch and isinstance(ch, discord.VoiceChannel):
                try:
                    await ch.delete(reason="手動クリーンアップ（管理者実行）")
                    deleted += 1
                except Exception:
                    failed += 1
        # メタ情報を掃除
        TEMP_VCS.pop(vc_id, None)

    # パスコード・紐付けもリセット
    VC_PASSCODES.clear()
    THREAD_TO_VC.clear()

    summary = f"🧹 手動クリーン完了: 削除 {deleted} 件 / 失敗 {failed} 件\nパスコードとスレッド紐付けも初期化しました。"
    await ctx.respond(summary, ephemeral=True)

    # 管理者ログチャンネルが設定されていれば投下
    if ADMIN_LOG_CHANNEL_ID:
        for guild in bot.guilds:
            log_ch = guild.get_channel(ADMIN_LOG_CHANNEL_ID)
            if log_ch:
                try:
                    await log_ch.send(summary)
                except Exception:
                    pass


# --- 手動クリーンアップ（管理者専用・即時実行） ---
@bot.slash_command(
    name="299_日次クリーン実行",
    description="Botが作成した一時VCと関連メタ情報を即時クリーンアップします（管理者専用）",
    default_member_permissions=discord.Permissions(administrator=True),
    dm_permission=False
)
async def cleanup_now(ctx):
    if not ctx.author.guild_permissions.administrator:
        await ctx.respond("❌ このコマンドは管理者のみ実行できます。", ephemeral=True)
        return

    await ctx.defer(ephemeral=True)

    start_count = len(TEMP_VCS)
    removed_channels = 0
    errors = 0
    not_found = 0

    # TEMP_VCS に記録されたVCのみを対象に削除
    for vc_id in list(TEMP_VCS.keys()):
        ch = bot.get_channel(vc_id)  # まずはグローバルキャッシュから取得
        if ch is None or not isinstance(ch, discord.VoiceChannel):
            # 念のため各ギルドにも当たってみる
            for guild in bot.guilds:
                _ch = guild.get_channel(vc_id)
                if _ch and isinstance(_ch, discord.VoiceChannel):
                    ch = _ch
                    break

        if ch and isinstance(ch, discord.VoiceChannel):
            try:
                await ch.delete(reason="手動クリーンアップ（管理者コマンド）")
                removed_channels += 1
            except Exception:
                errors += 1
        else:
            # 見つからなければ not_found としてカウント（メタだけ掃除）
            not_found += 1

        # いずれにせよメタ情報側も掃除
        TEMP_VCS.pop(vc_id, None)
        # 逆引き/パスコードも関連分を掃除
        for th_id, v_id in list(THREAD_TO_VC.items()):
            if v_id == vc_id:
                THREAD_TO_VC.pop(th_id, None)
        for code, v_id in list(VC_PASSCODES.items()):
            if v_id == vc_id:
                VC_PASSCODES.pop(code, None)

    await ctx.respond(
        f"🧹 クリーンアップ完了：\n"
        f"- 対象（開始時点）: {start_count} 件\n"
        f"- 削除成功: {removed_channels} 件\n"
        f"- 見つからずメタのみ削除: {not_found} 件\n"
        f"- エラー: {errors} 件",
        ephemeral=True
    )

# --- クリーン対象の現状確認（管理者専用） ---
@bot.slash_command(
    name="299_クリーン状況",
    description="Bot管理対象の一時VCメタ情報を一覧表示（管理者専用）",
    default_member_permissions=discord.Permissions(administrator=True),
    dm_permission=False
)
async def cleanup_status(ctx):
    if not ctx.author.guild_permissions.administrator:
        await ctx.respond("❌ このコマンドは管理者のみ実行できます。", ephemeral=True)
        return

    count = len(TEMP_VCS)
    if count == 0:
        await ctx.respond("（現在管理対象の一時VCは 0 件です）", ephemeral=True)
        return

    # 最大 20 件まで表示（長くなりすぎ防止）
    lines = []
    for i, (vcid, meta) in enumerate(list(TEMP_VCS.items())[:20], start=1):
        owner = meta.get("owner_id")
        thread = meta.get("thread_id")
        created = meta.get("created_at")
        lines.append(f"{i}. VCID: {vcid} / owner: {owner} / thread: {thread} / created: {created}")

    more = ""
    if count > 20:
        more = f"\n… ほか {count-20} 件"

    await ctx.respond(f"📋 管理対象 VC: {count} 件\n" + "\n".join(lines) + more, ephemeral=True)

 # --- 起動前プリフライト: /users/@me でトークン疎通確認 & レート制限尊重 ---
def preflight_check_sync(token: str):
    url = "https://discord.com/api/v10/users/@me"
    headers = {
        "Authorization": f"Bot {token}",
        "User-Agent": "nemunemuBot/1.0 (+render)",
    }
    backoff = 30
    max_backoff = 300
    while True:
        try:
            print(f"[PREFLIGHT] GET {url} ...", flush=True)
            r = requests.get(url, headers=headers, timeout=10)
            status = r.status_code
            retry_after = r.headers.get("Retry-After") or r.headers.get("retry-after")
            date_hdr = r.headers.get("Date")
            cf_hdr = r.headers.get("CF-RAY") or r.headers.get("CF-Ray")
            body_head = (r.text or "")[:120]
            print(f"[PREFLIGHT] status={status} retry_after={retry_after} date={date_hdr} cf={cf_hdr}", flush=True)
            print(f"[PREFLIGHT] body_head={body_head!r}", flush=True)
            if status == 200:
                print("[PREFLIGHT] Discord token is valid.", flush=True)
                return
            if status == 401:
                print("❌ ボットトークンが無効（401）。TOKEN を再確認してください。", flush=True)
                raise SystemExit(1)
            # 429/403/5xx は待機して再試行
            try:
                wait = int(float(retry_after)) if retry_after is not None else None
            except Exception:
                wait = None
            if not wait:
                wait = backoff
            backoff = min((backoff * 2), max_backoff)
            print(f"[PREFLIGHT] non-fatal status {status} → {wait}s 待機して再試行", flush=True)
            time.sleep(wait)
        except requests.exceptions.Timeout:
            print("[PREFLIGHT] timeout (10s) → 30s 後に再試行", flush=True)
            time.sleep(30)
        except requests.exceptions.RequestException as e:
            print(f"[PREFLIGHT] request error: {e} → 30s 後に再試行", flush=True)
            time.sleep(30)

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

print("[TRACE] about to enter __main__ block check", flush=True)
# --- 起動処理 ---
if __name__ == "__main__":
    print("[TRACE] __main__ confirmed; running sync preflight then bot.run()", flush=True)
    if not TOKEN:
        print("❌ TOKEN が未設定です。環境変数 TOKEN を設定してください。", flush=True)
        raise SystemExit(1)
    preflight_check_sync(TOKEN)
    print("[BOOT] bot.run() を開始します…", flush=True)
    while True:
        try:
            bot.run(TOKEN)
            break  # 正常終了したらループ抜ける
        except discord.HTTPException as e:
            if "429" in str(e) or "Too Many Requests" in str(e):
                print("❌ 429 Too Many Requests 発生。1時間停止して再試行します…", flush=True)
                time.sleep(3600)  # 3600秒 = 1時間
            else:
                raise