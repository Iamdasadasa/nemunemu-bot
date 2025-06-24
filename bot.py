# bot.py
import discord
import random
import requests
from bs4 import BeautifulSoup
import os
import threading
import socket

# 🔧 Render向けのダミーサーバー（PORTバインド回避用）
def dummy_server():
    port = int(os.environ.get("PORT", 10000))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("0.0.0.0", port))
        s.listen(1)
        while True:
            conn, _ = s.accept()
            conn.close()

# 🔑 Discord Botトークン（Renderでは環境変数から）
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

# 起動時に取得
MONSTERS = fetch_monsters()

# 🤖 Bot起動
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

# 🔄 再取得（管理者限定）
@bot.slash_command(name="update_monsters", description="モンスターリストを更新するよ（管理者限定）")
async def update_monsters(ctx):
    if not ctx.author.guild_permissions.administrator:
        await ctx.respond("このコマンドは管理者だけが使えるよ❌", ephemeral=True)
        return

    await ctx.defer()
    global MONSTERS
    MONSTERS = fetch_monsters()
    await ctx.followup.send(f"🆙 モンスターリストを更新したよ！現在の数：{len(MONSTERS)}体")

# 🔁 ダミーサーバー起動
threading.Thread(target=dummy_server, daemon=True).start()

# 🚀 起動
bot.run(TOKEN)




