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

# --- 募集メッセージ管理（リアクション参加用） ---
# { message_id: {
#   "owner_id": int, "channel_id": int,
#   "time_text": str, "content_text": str, "vc_name": str|None,
#   "limit": int|None, "participants": set[int], "closed": bool
# } }
RECRUITS: dict[int, dict] = {}

EMOJI_JOIN  = "✋"   # 参加
EMOJI_LEAVE = "↩️"   # 参加取り消し
EMOJI_CLOSE = "⛔"   # 募集停止（作成者 or 管理者のみ）

# 注意喚起の過剰送信防止用（(message_id, user_id, code) -> last_ts）
WARN_COOLDOWNS: dict[tuple[int, int, str], float] = {}
WARN_COOLDOWN_SEC = 60.0

# ロールID（設定済みかもだけど確認）
ROLE_FIRST_TIMER = 1390261208782868590  # 初めてロール
ROLE_GENERAL = 1390261772853837907      # 一般ロール ←適切なIDに変えて

# 環境変数 TEMP_VC_SENTINEL_ROLE_ID に BotTempVC のロールIDを設定してください

# --- 一時VC判定用センチネルロール（存在するだけでOK。誰にも付与しない想定） ---
# 使い方:
# 1) Discordサーバーに空のロール（例: BotTempVC）を作る
# 2) そのロールIDを環境変数 TEMP_VC_SENTINEL_ROLE_ID に設定
# 3) Botが作成する一時VCにはこのロールの権限上書きを付ける（本コードで自動）
# → クリーンアップはこの上書きがあるVCを検出して削除できる
TEMP_VC_SENTINEL_ROLE_ID = int(os.getenv("TEMP_VC_SENTINEL_ROLE_ID", "0"))

def _get_sentinel_role(guild: discord.Guild) -> discord.Role | None:
    if not TEMP_VC_SENTINEL_ROLE_ID:
        return None
    return guild.get_role(TEMP_VC_SENTINEL_ROLE_ID)

WELCOME_MESSAGE_EXTRA = os.getenv("WELCOME_MESSAGE_EXTRA", "")
VC_CATEGORY_ID = int(os.getenv("VC_CATEGORY_ID", "0"))
REPRESENTATIVE_COUNCIL_CHANNEL_ID = int(os.getenv("REPRESENTATIVE_COUNCIL_CHANNEL_ID"))
# 挨拶（自己紹介）チャンネルID（環境変数 INTRO_CHANNEL_ID を推奨。未設定時は 0）
INTRO_CHANNEL_ID = int(os.getenv("INTRO_CHANNEL_ID", "0"))
GUIDE_CHANNEL_ID = 1389290096498315364
# --- 管理者ログチャンネルID ---
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
            f"管理メンバーの皆さま、新たに{member.mention} さんがサーバーに参加されました。\n"
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

# --- リアクション処理（オンボーディング + 募集参加） ---
@bot.event
async def on_raw_reaction_add(payload):
    if payload.user_id == bot.user.id:
        return

    user_id = payload.user_id
    message_id = payload.message_id
    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return

    # ===== ① オンボーディング（案内メッセージ） =====
    try:
        if (user_id in guide_messages) and (guide_messages[user_id] == message_id):
            member = guild.get_member(user_id)
            if not member:
                return
            role_first = guild.get_role(ROLE_FIRST_TIMER)
            role_general = guild.get_role(ROLE_GENERAL)
            channel = bot.get_channel(payload.channel_id)

            # ロール更新
            if role_first in member.roles:
                await member.remove_roles(role_first)
            if role_general:
                await member.add_roles(role_general)

            # 案内メッセージ削除
            try:
                msg = await channel.fetch_message(message_id)
                await msg.delete()
            except Exception:
                pass
            guide_messages.pop(user_id, None)

            # 歓迎メッセージ＋スレッド
            try:
                intro_ch = guild.get_channel(INTRO_CHANNEL_ID) if INTRO_CHANNEL_ID else None
                if intro_ch and isinstance(intro_ch, (discord.TextChannel, discord.ForumChannel)):
                    welcome_text = (
                        f"🎉 新メンバーが来てくれました！\n"
                        f"{member.mention} さん、これからよろしくね！\n\n"
                        "よければこの投稿からつながるスレッドで、軽く『こんにちは〜』『好きな武器』など一言どうぞ 🙌\n"
                        "※挨拶は任意です。読む専でもOK！"
                    )
                    post = await intro_ch.send(welcome_text)
                    thread_name = f"👋 歓迎：{member.display_name}"
                    created_thread = await intro_ch.create_thread(
                        name=thread_name,
                        message=post,
                        auto_archive_duration=180,
                        type=discord.ChannelType.public_thread
                    )
                    try:
                        await created_thread.send("🎉 みんなも新メンバーに挨拶してね！")
                    except Exception:
                        pass
            except Exception as e:
                log_channel = guild.get_channel(REPRESENTATIVE_COUNCIL_CHANNEL_ID)
                if log_channel:
                    await log_channel.send(f"⚠️ 歓迎メッセージ/スレッド作成に失敗しました: {e}")
            return
    except Exception as e:
        log_channel = guild.get_channel(REPRESENTATIVE_COUNCIL_CHANNEL_ID)
        if log_channel:
            await log_channel.send(f"⚠️ リアクション処理(オンボ)で例外: {e}")

    # ===== ② 募集メッセージのリアクション参加 =====
    if message_id not in RECRUITS:
        return

    data = RECRUITS[message_id]
    emoji = str(payload.emoji)
    member = guild.get_member(user_id)
    if not member or member.bot:
        return

    # Util: リアクションを取り消す
    async def _undo(emoji_to_remove: str):
        try:
            ch = guild.get_channel(data["channel_id"])
            msg = await ch.fetch_message(message_id)
            await msg.remove_reaction(emoji_to_remove, member)
        except Exception:
            pass

    updated = False

    if emoji == EMOJI_JOIN:
        # 募集停止中
        if data.get("closed"):
            await _undo(EMOJI_JOIN)
            await _warn_once(member, message_id, "closed", "⛔ 現在この募集は停止中です。再開をお待ちください。")
            return
        # すでに参加済み
        if member.id in data["participants"]:
            await _undo(EMOJI_JOIN)
            await _warn_once(member, message_id, "dup_join", "ℹ️ すでに参加登録されています。")
            return
        # 上限チェック
        limit = data.get("limit")
        if (limit is not None) and (len(data["participants"]) >= limit):
            await _undo(EMOJI_JOIN)
            await _warn_once(member, message_id, "full", f"📮 満員です（{limit}人）。空きが出たらもう一度お試しください。")
            return
        # 参加登録
        data["participants"].add(member.id)
        updated = True

    elif emoji == EMOJI_LEAVE:
        if member.id not in data["participants"]:
            await _undo(EMOJI_LEAVE)
            await _warn_once(member, message_id, "not_joined", "ℹ️ まだ参加登録されていません。")
            return
        data["participants"].remove(member.id)
        updated = True

    elif emoji == EMOJI_CLOSE:
        if (member.id == data["owner_id"]) or (member.guild_permissions.administrator):
            data["closed"] = not data.get("closed", False)
            updated = True
        else:
            await _undo(EMOJI_CLOSE)
            await _warn_once(member, message_id, "no_auth", "⚠️ この募集の停止/再開を切り替えられるのは、作成者か管理者のみです。")
            return

    if updated:
        await _update_recruit_embed(guild, message_id)

# --- リアクションが外れたときも同期 ---
@bot.event
async def on_raw_reaction_remove(payload):
    message_id = payload.message_id
    if message_id not in RECRUITS:
        return
    guild = bot.get_guild(payload.guild_id)
    if guild:
        await _update_recruit_embed(guild, message_id)
# --- 募集メッセージ埋め込み・警告ヘルパ ---
#
# --- 募集停止/再開コントロール（作成者/管理者のみ） ---
class StopToggleView(discord.ui.View):
    def __init__(self, guild_id: int, message_id: int, timeout: float | None = 600):
        super().__init__(timeout=timeout)
        self.guild_id = guild_id
        self.message_id = message_id

    @discord.ui.button(label="⛔ 募集停止 / 再開", style=discord.ButtonStyle.danger)
    async def toggle_stop(self, button: discord.ui.Button, interaction: discord.Interaction):
        guild = interaction.client.get_guild(self.guild_id)
        if not guild:
            await interaction.response.send_message("⚠️ サーバー情報を取得できませんでした。", ephemeral=True)
            return
        data = RECRUITS.get(self.message_id)
        if not data:
            await interaction.response.send_message("⚠️ 対象の募集情報が見つかりませんでした。", ephemeral=True)
            return

        # 権限チェック：作成者 or 管理者のみ
        if (interaction.user.id != data["owner_id"]) and (not interaction.user.guild_permissions.administrator):
            await interaction.response.send_message("⚠️ この操作は募集の作成者または管理者のみ可能です。", ephemeral=True)
            return

        # トグルして更新
        data["closed"] = not data.get("closed", False)
        await _update_recruit_embed(guild, self.message_id)
        status = "停止" if data["closed"] else "再開"
        await interaction.response.send_message(f"✅ 募集を**{status}**しました。", ephemeral=True)

# --- モンスター関連コマンド ---
async def _warn_once(member: discord.Member, message_id: int, code: str, text: str):
    """
    同じ内容の警告DMを短時間に何度も送らないための補助。
    """
    now = time.time()
    key = (message_id, member.id, code)
    last = WARN_COOLDOWNS.get(key, 0.0)
    if now - last < WARN_COOLDOWN_SEC:
        return
    WARN_COOLDOWNS[key] = now
    try:
        await member.send(text)
    except Exception:
        # DMが閉じられている場合は無視
        pass

async def _update_recruit_embed(guild: discord.Guild, message_id: int):
    """募集メッセージの埋め込みを最新化する"""
    data = RECRUITS.get(message_id)
    if not data:
        return
    ch = guild.get_channel(data["channel_id"])
    if not ch:
        return
    try:
        msg = await ch.fetch_message(message_id)
    except Exception:
        return

    limit = data["limit"]
    members = [guild.get_member(uid) for uid in data["participants"]]
    members = [m for m in members if m is not None]
    count = len(members)

    members_text = "\n".join([f"- {m.mention}" for m in members]) if members else "（まだいません）"

    title = f"🎯 クエスト募集（by <@{data['owner_id']}>)"
    color = 0xAAAAAA if data.get("closed") else 0x4CAF50
    embed = discord.Embed(title=title, color=color)
    embed.add_field(name="⏰ 時間", value=f"→ __{data['time_text']}__", inline=False)
    embed.add_field(name="📝 内容", value=f"→ __{data['content_text']}__", inline=False)
    if data["vc_name"]:
        embed.add_field(name="📍 場所", value=f"→ __{data['vc_name']}__", inline=False)
    embed.add_field(name="👥 人数", value=f"→ __{limit if limit else '指定なし'}__", inline=False)
    embed.add_field(name="📊 募集状況", value=f"__{count}__ / __{limit if limit else '∞'}__", inline=True)
    embed.add_field(name="🧑‍🤝‍🧑 参加者一覧", value=members_text, inline=False)
    if data.get("closed"):
        embed.set_footer(text="⛔ この募集は停止中です")

    try:
        await msg.edit(embed=embed)
    except Exception:
        pass

# --- 退出時：未処理の案内メッセージをクリーンアップ ---
@bot.event
async def on_member_remove(member: discord.Member):
    """
    新規参加者がリアクションせずに退出した場合、
    その人宛てに残っている案内メッセージ（guide_messagesの対象）を削除する。
    """
    # まずは退出自体を管理メンバーログに通知
    try:
        kanrilog_channel = member.guild.get_channel(REPRESENTATIVE_COUNCIL_CHANNEL_ID)
        if kanrilog_channel:
            await kanrilog_channel.send(f"🗑️{member.mention} さんがサーバーを退出しました。（ID: {member.id}）")
    except Exception:
        pass
    try:
        user_id = member.id
        msg_id = guide_messages.pop(user_id, None)
        if not msg_id:
            return  # 記録なし → 何もしない

        guild = member.guild
        guide_channel = guild.get_channel(GUIDE_CHANNEL_ID)
        if not guide_channel:
            return

        try:
            msg = await guide_channel.fetch_message(msg_id)
            await msg.delete()
            # ログに通知
            log_channel = guild.get_channel(ADMIN_LOG_CHANNEL_ID)
            if log_channel:
                await log_channel.send(f"🗑️{member.mention} さんが退出したため、案内メッセージ（ID: {msg_id}）を削除しました。")
        except discord.NotFound:
            # 既に削除済み
            pass
        except Exception as e:
            log_channel = guild.get_channel(ADMIN_LOG_CHANNEL_ID)
            if log_channel:
                await log_channel.send(f"⚠️ 退出者の案内メッセージ削除に失敗しました: {e}")
    except Exception as e:
        # ここで例外を握りつぶしてBot停止を避ける
        try:
            log_channel = member.guild.get_channel(ADMIN_LOG_CHANNEL_ID)
            if log_channel:
                await log_channel.send(f"⚠️ on_member_remove 内部エラー: {e}")
        except Exception:
            pass

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
        choices=["バウンティ消化", "お護り集め/神お護り周回", "クエストお手伝い", "HR上げ", "素材集め", "金策", "写真撮りたい！", "募集カスタムに記載"],
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

        # センチネルロールがあれば、空の上書きを付与（マーカー用途。特別な権限は与えない）
        sentinel_role = _get_sentinel_role(ctx.guild)
        if sentinel_role is not None:
            overwrites[sentinel_role] = discord.PermissionOverwrite()

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

    # Defer 後は followup.send を使う（respond ではなく）
    instruction_text = "参加者募集中  ｜  ✋ 参加　↩️ 参加取消　⛔ 募集停止（作成者のみ）"
    original_msg = await ctx.followup.send(content=instruction_text, embed=embed)

    # --- リアクション参加セットアップ ---
    try:
        await original_msg.add_reaction(EMOJI_JOIN)
        await original_msg.add_reaction(EMOJI_LEAVE)
        # await original_msg.add_reaction(EMOJI_CLOSE)  # ⛔ は公開メッセージには付けない
    except Exception:
        pass

    RECRUITS[original_msg.id] = {
        "owner_id": ctx.author.id,
        "channel_id": ctx.channel.id,
        "time_text": 時間,
        "content_text": 内容,
        "vc_name": (used_vc.name if used_vc else None),
        "limit": vc_limit,
        "participants": set(),
        "closed": False,
    }

    # 募集作成者向けコントローラ（エフェメラル）
    try:
        view = StopToggleView(ctx.guild.id, original_msg.id, timeout=3600)
        await ctx.followup.send(
            content=f"⛔ この募集を停止/再開できます → [募集メッセージへ]({original_msg.jump_url})",
            view=view,
            ephemeral=True
        )
    except Exception:
        pass

    # 募集スレッドを作る（常に作成／公開スレッド）。
    # スラコマ実行場所がすでにスレッドなら、そのスレッドを流用。
    thread = None
    try:
        if isinstance(ctx.channel, discord.Thread):
            thread = ctx.channel
        else:
            # TextChannel 側から message=original_msg を指定して作成（ライブラリ互換性が高い）
            thread = await ctx.channel.create_thread(
                name=f"{ctx.author.name}の募集スレッド",
                message=original_msg,
                auto_archive_duration=60,  # 1時間
                type=discord.ChannelType.public_thread
            )
        # スレッドに初期メッセージを投稿（要点まとめ）
        try:
            summary_lines = [
                f"⏰ 時間: **{時間}**",
                f"📝 内容: **{内容}**",
                f"👥 人数: **{人数}**",
            ]
            if used_vc:
                summary_lines.append(f"📍 場所: **{used_vc.name}**")
            if 募集カスタム内容:
                summary_lines.append(f"💬 補足: {募集カスタム内容}")
            await thread.send("\n".join(summary_lines))
        except Exception:
            pass
    except discord.Forbidden:
        # 権限不足（Create Public Threads 等）で作成できない場合
        print("[QUEST_POST] スレッド作成に失敗（Forbidden: create_thread 権限不足の可能性）", flush=True)
    except Exception as e:
        print(f"[QUEST_POST] スレッド作成に失敗: {e}", flush=True)

    # VCとスレッドのひも付け（Bot作成VCのみ）
    if created_vc and thread:
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
    start_ts = discord.utils.utcnow()
    print(f"[CLEANUP] ⏱️ 開始 {start_ts.isoformat()} (JST 8:00 トリガ)", flush=True)

    deleted_vc_count = 0
    not_found_count = 0
    error_count = 0

    # マーカー（センチネルロール）だけで判定
    target_ids = set()
    for guild in bot.guilds:
        sentinel = _get_sentinel_role(guild)
        for ch in guild.channels:
            if isinstance(ch, discord.VoiceChannel):
                if sentinel is not None and sentinel in ch.overwrites:
                    target_ids.add(ch.id)

    print(f"[CLEANUP] 対象VC数: {len(target_ids)} / ids={list(target_ids)}", flush=True)

    for vc_id in list(target_ids):
        deleted_this_id = False
        for guild in bot.guilds:
            ch = guild.get_channel(vc_id)
            if ch and isinstance(ch, discord.VoiceChannel):
                try:
                    await ch.delete(reason="日次クリーンアップ（Bot作成VC/マーカー付きVC）")
                    deleted_vc_count += 1
                    deleted_this_id = True
                    print(f"[CLEANUP] ✅ 削除 vc_id={vc_id} guild={guild.name} ch={ch.name}", flush=True)
                    break
                except Exception as e:
                    error_count += 1
                    print(f"[CLEANUP] ⚠️ 削除失敗 vc_id={vc_id} guild={guild.name} err={e}", flush=True)
        if not deleted_this_id:
            not_found_count += 1
            print(f"[CLEANUP] ❓ 見つからず/削除不可 vc_id={vc_id}", flush=True)
        # メタ情報は必ず破棄
        TEMP_VCS.pop(vc_id, None)

    # パスコード・スレッド紐付けも全消し
    pass_cnt = len(VC_PASSCODES)
    map_cnt = len(THREAD_TO_VC)
    VC_PASSCODES.clear()
    THREAD_TO_VC.clear()
    print(f"[CLEANUP] 🔑 パスコードクリア: {pass_cnt} 件 / スレッド紐付けクリア: {map_cnt} 件", flush=True)

    end_ts = discord.utils.utcnow()
    summary = (
        f"🧹 日次クリーンアップ完了\n"
        f"- 削除VC: {deleted_vc_count}\n"
        f"- 未検出/不可: {not_found_count}\n"
        f"- エラー: {error_count}\n"
        f"- 開始: {start_ts.isoformat()} / 終了: {end_ts.isoformat()}"
    )
    print(f"[CLEANUP] 完了サマリ: {summary}", flush=True)

    # 管理者ログチャンネルにも通知（設定されている場合のみ）
    if 'ADMIN_LOG_CHANNEL_ID' in globals() and ADMIN_LOG_CHANNEL_ID and ADMIN_LOG_CHANNEL_ID != 0:
        for guild in bot.guilds:
            admin_log_ch = guild.get_channel(ADMIN_LOG_CHANNEL_ID)
            if admin_log_ch:
                try:
                    await admin_log_ch.send(summary)
                except Exception as e:
                    print(f"[CLEANUP] 管理ログ送信失敗: {e}", flush=True)
                break  # 最初に見つかったチャンネルへ1回だけ

@daily_cleanup_vcs.before_loop
async def before_cleanup():
    print("[CLEANUP] 待機: bot.wait_until_ready() …", flush=True)
    await bot.wait_until_ready()
    print("[CLEANUP] bot is ready. ループ起動準備OK。", flush=True)

# --- 管理者専用: 日次クリーン実行コマンド ---
@bot.slash_command(
    name="299_日次クリーン実行",
    description="日次クリーンアップを即時実行します（管理者専用）",
    default_member_permissions=discord.Permissions(administrator=True),
    dm_permission=False
)
async def manual_daily_cleanup(ctx):
    if not ctx.author.guild_permissions.administrator:
        await ctx.respond("❌ このコマンドは管理者のみ実行できます。", ephemeral=True)
        return

    deleted_vc_count = 0

    # 対象候補を収集
    target_ids = set(TEMP_VCS.keys())
    for guild in bot.guilds:
        sentinel = _get_sentinel_role(guild)
        for ch in guild.channels:
            if isinstance(ch, discord.VoiceChannel):
                marked = False
                if sentinel is not None and sentinel in ch.overwrites:
                    marked = True
                if (ch.id in TEMP_VCS) or marked or ch.name.startswith("募集VC："):
                    target_ids.add(ch.id)

    # 削除処理
    for vc_id in list(target_ids):
        for guild in bot.guilds:
            ch = guild.get_channel(vc_id)
            if ch and isinstance(ch, discord.VoiceChannel):
                try:
                    await ch.delete(reason="管理者による日次クリーン実行（Bot作成VC/マーカー付きVC）")
                    deleted_vc_count += 1
                    break
                except Exception:
                    pass
        TEMP_VCS.pop(vc_id, None)

    # パスコード/スレッド紐付けも全消し
    VC_PASSCODES.clear()
    THREAD_TO_VC.clear()

    result_msg = (
        f"🧹 日次クリーンアップを実行しました。\n"
        f"削除VC数: {deleted_vc_count}\n"
        f"パスコード・スレッド紐付けもリセットしました。"
    )
    await ctx.respond(result_msg, ephemeral=True)

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
        print("✅ スラッシュコマンドの同期に成功しました_20251028")
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