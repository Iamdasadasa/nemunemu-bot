# bot.py
import discord
import random
import requests
from bs4 import BeautifulSoup
import os
import threading
from flask import Flask

# 🌐 Flask Webサーバー（RenderのHTTP応答用）
app = Flask(__name__)

@app.route("/")
def home():
    return "👋 ねむねむBot is alive!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# 🔑 Discord Botトークン（Renderの環境変数から取得）
TOKEN = os.getenv("TOKEN")

# 🌐 モンスター取得関数
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

# モンスターリスト初期化
MONSTERS = fetch_monsters()

# 🤖 Discord Bot本体
bot = discord.Bot()

@bot.event
async def on_ready():
    print(f'{bot.user} でログインしました！（モンスター数: {len(MONSTERS)}）')

# 🎲 ランダムモンスター
@bot.slash_command(name="monster", description="モンスターをランダムに教えてくれるよ！")
async def monster(ctx):
    await ctx.defer()
    if not MONSTERS:
        await ctx.followup.send("モンスターリストが空だよ😢")
    else:
        name = random.choice(MONSTERS)
        await ctx.followup.send(f"あなたのモンスターは… 🐲 **{name}** だ！")

# 🔄 モンスター再取得（管理者限定）
@bot.slash_command(name="update_monsters", description="モンスターリストを更新するよ（管理者限定）")
async def update_monsters(ctx):
    if not ctx.author.guild_permissions.administrator:
        await ctx.respond("このコマンドは管理者だけが使えるよ❌", ephemeral=True)
        return

    await ctx.defer()
    global MONSTERS
    MONSTERS = fetch_monsters()
    await ctx.followup.send(f"🆙 モンスターリストを更新したよ！現在の数：{len(MONSTERS)}体")

# 🧵 Flaskサーバーをスレッドで起動（RenderのPORTを使う）
threading.Thread(target=run_flask, daemon=True).start()

# 🚀 Discord Bot起動
bot.run(TOKEN)
