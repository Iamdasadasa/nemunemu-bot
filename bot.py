import os
import random
import requests
from bs4 import BeautifulSoup
from flask import Flask
import threading
import discord
from discord.ext import commands
import asyncio


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
#bot = discord.Bot()
intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
bot = discord.Bot(intents=intents)


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

# 🧩 スラッシュコマンド：パーティ編成（リアクションで参加を募って自動グループ分け）
@bot.slash_command(name="party", description="参加リアクションからランダムにパーティを編成するよ！※60秒後に締め切ります")
async def party(ctx, size: int = 4):
    import asyncio
    if size < 1:
        await ctx.respond("パーティ人数は1人以上にしてね❌", ephemeral=True)
        return

    msg = await ctx.respond(f"🙋‍♂️ パーティ編成！参加したい人はリアクションしてね！（{size}人ずつ）")
    original = await msg.original_response()
    await original.add_reaction("🙋")

    await asyncio.sleep(60)  # 60秒待機

    updated = await ctx.channel.fetch_message(original.id)
    users = await updated.reactions[0].users().flatten()
    users = [u for u in users if not u.bot]

    if len(users) < size:
        await ctx.followup.send("😢 参加者が足りなかったよ…")
        return

    random.shuffle(users)

    # 均等に分けるグループ数を決定
    group_count = (len(users) + size - 1) // size  # ceiling division
    base_size = len(users) // group_count
    remainder = len(users) % group_count

    groups = []
    start = 0
    for i in range(group_count):
        extra = 1 if i < remainder else 0  # 最初のremainder個のグループに1人追加
        end = start + base_size + extra
        groups.append(users[start:end])
        start = end

    result = "\n\n".join(
        [f"🧩 パーティ {i+1}:\n" + "\n".join([f"- {u.mention}" for u in g]) for i, g in enumerate(groups)]
    )
    await ctx.followup.send(f"✅ パーティ編成完了！\n{result}")



# 🧵 Flask起動（Render用）
threading.Thread(target=run_flask, daemon=True).start()

# 🚀 Bot起動
bot.run(TOKEN)
