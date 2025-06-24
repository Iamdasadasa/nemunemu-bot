import os
import random
import requests
from bs4 import BeautifulSoup
from flask import Flask
import threading
import discord
from discord.ext import commands

# Flask for Render uptime ping
app = Flask(__name__)

@app.route("/")
def home():
    return "👋 ねむねむBot is alive!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# Botのトークン
TOKEN = os.getenv("TOKEN")

# モンスター取得
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

MONSTERS = fetch_monsters()

# Bot設定
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ {bot.user} でログインしました！")

@bot.command(name="monster")
async def monster(ctx):
    if MONSTERS:
        name = random.choice(MONSTERS)
        await ctx.send(f"あなたのモンスターは… 🐲 **{name}** だ！")
    else:
        await ctx.send("モンスターが見つからなかったよ😢")

# Flask起動（Render対策）
threading.Thread(target=run_flask, daemon=True).start()

# Bot起動
bot.run(TOKEN)
