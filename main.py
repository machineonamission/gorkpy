from google import genai

with open("geminikey.txt") as f:
    gemini_client = genai.Client(api_key=f.read())
with open("prompt.txt") as f:
    prompt = f.read()

import discord

intents = discord.Intents.default()
intents.message_content = True

discord_client = discord.Client(intents=intents)


@discord_client.event
async def on_ready():
    print(f'We have logged in as {discord_client.user}')

message_fetch_cache = {}

async def fetch_and_cache(msg:discord.Message):
    if msg.id in message_fetch_cache:
        return message_fetch_cache[msg.id]
    else:
        res = await msg.fetch()
        message_fetch_cache[msg.id] = res
        return res

async def crawl_replies(message: discord.Message):
    replies = []
    if message.type == discord.MessageType.reply:
        if ref := message.reference:
            res = ref.cached_message
            if not res:
                res = await fetch_and_cache(ref.resolved)
            if isinstance(res, discord.Message):
                replies = await crawl_replies(res)
    return replies + [message]


@discord_client.event
async def on_message(message: discord.Message):
    if discord_client.user in message.mentions:
        async with message.channel.typing():
            chain = await crawl_replies(message)
            parts = []

            for cmsg in chain:
                model = cmsg.author == discord_client.user
                content = cmsg.content
                for mention in cmsg.mentions:
                    content = content.replace(mention.mention, f"@{mention.display_name}")
                header = "" if model else f"@{cmsg.author.display_name} says:\n"
                parts.append(genai.types.Content(
                    role='model' if model else 'user',
                    parts=[genai.types.Part.from_text(text=f"{header}{content}")]
                ))

                # if cmsg.author == discord_client.user:

            response = await gemini_client.aio.models.generate_content(
                model='gemini-2.0-flash-lite',
                contents=parts,
                config=genai.types.GenerateContentConfig(
                    system_instruction=prompt,
                    temperature=2,
                    thinking_config=genai.types.ThinkingConfig(thinking_budget=0)
                ),
            )
            await message.reply(response.text)


with open("discordkey.txt") as f:
    discord_client.run(f.read())
