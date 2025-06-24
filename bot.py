# bot.py
import discord
import random
import requests
from bs4 import BeautifulSoup

# 🌐 外部サイトからモンスター名を取得
def fetch_monsters():
    url = "https://gamewith.jp/mhwilds/452222"
    res = requests.get(url)
    soup = BeautifulSoup(res.content, "html.parser")

    names = []
    for row in soup.select("table tr")[1:]:
        cols = row.find_all("td")
        if cols and len(cols) >= 2:
            name = cols[1].get_text(strip=True)
            if name:
                names.append(name)
    return names

# 🔁 起動時に取得
MONSTERS = fetch_monsters()

# Bot定義
bot = discord.Bot()

@bot.event
async def on_ready():
    print(f'{bot.user} でログインしました！（モンスター数: {len(MONSTERS)}）')

# 🎲 ランダムモンスター出力
@bot.slash_command(name="monster", description="今日のモンスターをランダムに教えてくれるよ！")
async def monster(ctx):
    if not MONSTERS:
        await ctx.respond("モンスターリストが取得できませんでした😢")
    else:
        name = random.choice(MONSTERS)
        await ctx.respond(f"今日のモンスターは… 🐲 **{name}** だ！")

bot.run(TOKEN)