import os
import discord

# 環境変数からトークンを取得（すでに TOKEN はセットされている想定）
TOKEN = os.getenv("TOKEN")

# Botインスタンスの作成（必要最低限のインテント）
intents = discord.Intents.default()
bot = discord.Bot(intents=intents)

# 起動時にログを出すイベント
@bot.event
async def on_ready():
    print("✅ on_ready() に入りました！")
    print(f"✅ ログインユーザー: {bot.user} (ID: {bot.user.id})")

# スラッシュコマンド1つ（動作確認用）
@bot.slash_command(name="hello", description="挨拶を返します")
async def hello(ctx):
    await ctx.respond("こんにちは！Botは正常に動作しています。")

# Botの起動
if __name__ == "__main__":
    bot.run(TOKEN)