import asyncio

from discord.ext import tasks
from google import genai
from google.genai.errors import ClientError

with open("geminikey.txt") as f:
    gemini_client = genai.Client(api_key=f.read())
with open("prompt.txt") as f:
    prompt = f.read()

model_name = 'gemini-2.0-flash'

import discord

intents = discord.Intents.default()
intents.message_content = True

discord_client = discord.Client(intents=intents)


@discord_client.event
async def on_ready():
    print(f'We have logged in as {discord_client.user}')


message_fetch_cache = {}


async def fetch_and_cache(msg: discord.Message):
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
        # while True:
        #     try:
        #         await ensure_limiter()
        #         await limiter.try_acquire_async(str(message.id))
        #         break
        #     except pyrate_limiter.BucketFullException as err:
        #         print(err)
        #         delay = await limiter.buckets()[0].waiting(err.item) / 1000
        #         if delay > 1:
        #             msg, _ = await asyncio.gather(
        #                 message.reply(f"gork is a little overloaded right now. give me {round(delay)} seconds to catch up!"),
        #                 asyncio.sleep(delay)
        #             )
        #             await msg.delete()
        async with message.channel.typing():
            try:
                response = await gemini_client.aio.models.generate_content(
                    model='gemini-2.0-flash',
                    contents=parts,
                    config=genai.types.GenerateContentConfig(
                        system_instruction=prompt,
                        temperature=2,
                        thinking_config=genai.types.ThinkingConfig(thinking_budget=0)
                    ),
                )
            except Exception as e:
                print(e)
            await message.reply(response.text)


async def generate_loop():
    while True:
        message, parts, fut = await gen_queue.get()  # sleep until item arrives
        try:
            for i in range(5):
                try:
                    response = await gemini_client.aio.models.generate_content(
                        model='gemini-2.0-flash',
                        contents=parts,
                        config=genai.types.GenerateContentConfig(
                            system_instruction=prompt,
                            temperature=2,
                            thinking_config=genai.types.ThinkingConfig(thinking_budget=0)
                        ),
                    )
                    break
                except ClientError as e:
                    if e.code == 429:
                        for detail in e.details["error"]["details"]:
                            if detail["@type"] == "type.googleapis.com/google.rpc.RetryInfo":
                                if detail["retryDelay"][-1] == "s":
                                    delay = int(detail["retryDelay"][:-1])
                                    msg, _ = await asyncio.gather(
                                        message.reply(
                                            f"gork is a little overloaded right now. give me {round(delay)} seconds to catch up!"),
                                        asyncio.sleep(delay)
                                    )
                                    await msg.delete()
                            break
                        else:
                            raise e
                    raise e
            else:
                raise Exception("Failed to get response after 5 attempts")
            fut.set_result(response)  # return to the waiter
        except Exception as e:
            fut.set_exception(e)  # propagate errors
        finally:
            gen_queue.task_done()


async def generate(message, parts):
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    await gen_queue.put((message, parts, fut))
    return await fut  # wait until looper sets result


async def run():
    with open("discordkey.txt") as f:
        dtoken = f.read()
    await discord_client.start(token=dtoken)


gen_queue = asyncio.Queue()
asyncio.run(run())
