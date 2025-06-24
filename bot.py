# bot.py
import discord
import random
import requests
from bs4 import BeautifulSoup

TOKEN ='MTM4NjkzMDk0MTU0NDYzMjQ5Mg.GkdXwB.HegRcfYwUlsjUbqjZxxrcXtLAyrRWueEirqQFc'

MONSTERS = []

# 🌐 モンスター取得関数（改良版）
def fetch_monsters():
    url = "https://gamewith.jp/mhwilds/452222"
    res = requests.get(url)
    soup = BeautifulSoup(res.content, "html.parser")

    names = []
    # <ol class="... monster_weak_list"> 内の <li data-name="...">
    for li in soup.select("ol.monster_weak_list li[data-name]"):
        name = li.get("data-name", "").strip()
        if name:
            names.append(name)
    return names

# 起動時に取得
MONSTERS = fetch_monsters()

bot = discord.Bot()

@bot.event
async def on_ready():
    print(f'{bot.user} でログインしました！（モンスター数: {len(MONSTERS)}）')

# 🎲 ランダムモンスター
@bot.slash_command(name="monster", description="今日のモンスターをランダムに教えてくれるよ！")
async def monster(ctx):
    if not MONSTERS:
        await ctx.respond("モンスターリストが空だよ😢")
    else:
        name = random.choice(MONSTERS)
        await ctx.respond(f"今日のモンスターは… 🐲 **{name}** だ！")

# 🔄 再取得コマンド（管理者限定）
@bot.slash_command(name="update_monsters", description="モンスターリストを最新に更新するよ（管理者限定）")
async def update_monsters(ctx):
    if not ctx.author.guild_permissions.administrator:
        await ctx.respond("このコマンドは管理者だけが使えるよ❌", ephemeral=True)
        return

    global MONSTERS
    MONSTERS = fetch_monsters()
    await ctx.respond(f"🆙 モンスターリストを更新したよ！現在の数：{len(MONSTERS)}体")

bot.run(TOKEN)