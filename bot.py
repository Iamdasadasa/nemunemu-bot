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

# 🔄 スラッシュコマンド：モンスターリスト更新（管理者限定）
@bot.slash_command(name="update_monsters", description="モンスターリストを更新するよ（管理者専用）")
async def update_monsters(ctx):
    if not ctx.author.guild_permissions.administrator:
        await ctx.respond("このコマンドは管理者だけが使えるよ❌", ephemeral=True)
        return

    await ctx.respond("🔄 モンスターリストを更新中…")
    global MONSTERS
    MONSTERS = fetch_monsters()
    await ctx.send_followup(f"🆙 モンスターリストを更新したよ！現在の数：{len(MONSTERS)}体")

# 🧵 Flask起動（Render用）
threading.Thread(target=run_flask, daemon=True).start()

# 🚀 Bot起動
bot.run(TOKEN)
