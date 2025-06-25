import os
import random
import requests
from bs4 import BeautifulSoup
from flask import Flask
import threading
import discord

# 🌐 Flaskサーバー（RenderのHTTPチェック用）
app = Flask(__name__)

@app.route("/")
def home():
    return "👋 ねむねむBot is alive!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# 🔑 Discord Botトークン
TOKEN = os.getenv("TOKEN")

# 📦 モンスター取得関数
def fetch_monsters():
    url = "https://gamewith.jp/mhwilds/452222"
    res = requests.get(url)
    soup = BeautifulSoup(res.content, "html.parser")
    names = []
    for li in soup.select("ol.monster_weak_list li[data-name]"):
        name = li.get("data-name", "").strip()
        if name:
            names.append(name)
    return names

# 初期モンスターリスト取得
MONSTERS = fetch_monsters()

# 🤖 Discord Bot設定（スラッシュコマンド対応）
bot = discord.Bot()

@bot.event
async def on_ready():
    print(f"✅ {bot.user} でログインしました！")

# 🎲 スラッシュコマンド：モンスター表示
@bot.slash_command(name="monster", description="モンスターをランダムに教えてくれるよ！")
async def monster(ctx):
    if MONSTERS:
        name = random.choice(MONSTERS)
        await ctx.respond(f"あなたのモンスターは… 🐲 **{name}** だ！")
    else:
        await ctx.respond("モンスターが見つからなかったよ😢")

# 🔄 スラッシュコマンド：モンスターリスト更新（誰でも可）
@bot.slash_command(name="update_monsters", description="モンスターリストを更新するよ")
async def update_monsters(ctx):
    await ctx.respond("🔄 モンスターリストを更新中…")
    global MONSTERS
    MONSTERS = fetch_monsters()
    await ctx.send_followup(f"🆙 モンスターリストを更新したよ！現在の数：{len(MONSTERS)}体")

@bot.command(name="start_party")
async def start_party(ctx):
    # 参加者募集メッセージ送信
    message = await ctx.send("🎉 パーティを作るよ！参加したい人はこのメッセージに ✋ をつけてね！")
    await message.add_reaction("✋")

    # 20秒待機（リアクションを集める時間）
    await discord.utils.sleep_until(discord.utils.utcnow() + discord.utils.timedelta(seconds=20))
    
    # 再取得（キャッシュでなく最新のリアクションを読むため）
    message = await ctx.channel.fetch_message(message.id)

    # ✋リアクションを押したユーザーを取得（Botは除外）
    users = [user async for user in message.reactions[0].users() if not user.bot]

    if not users:
        await ctx.send("😢 参加者がいなかったよ…")
        return

    # パーティ編成（1組あたり最大4人）
    random.shuffle(users)
    party_size = 4
    parties = [users[i:i + party_size] for i in range(0, len(users), party_size)]

    # 結果表示
    result = "🎮 パーティ編成完了！\n\n"
    for i, party in enumerate(parties):
        members = " ".join(member.mention for member in party)
        if len(party) == party_size:
            result += f"パーティ{i+1}：{members}\n"
        else:
            result += f"補欠：{members}\n"

    await ctx.send(result)

# 🧵 Flask起動（Render用）
threading.Thread(target=run_flask, daemon=True).start()

# 🚀 Bot起動
bot.run(TOKEN)
