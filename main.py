import asyncio

from discord.ext import tasks
from google import genai
from google.genai.errors import ClientError

with open("geminikey.txt") as f:
    gemini_client = genai.Client(api_key=f.read())
with open("prompt.txt") as f:
    prompt = f.read()

model_name = 'gemini-2.0-flash'

MIN_MESSAGES = 10
MAX_MESSAGES = 100

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


def reply_to_string(ref: discord.Message):
    return (f"Replying to:\n"
            f"{quote(f"@{ref.author.display_name} says:\n"
                     f"{format_content(ref, False)}")}")


def get_reply(message: discord.Message):
    if message.reference and message.reference.resolved and message.type == discord.MessageType.reply:
        return message.reference.resolved
    else:
        return None

def quote(string: str):
    return "\n".join("> " + line for line in string.splitlines())

def format_content(message: discord.Message, model: bool):
    content = message.content
    # resolve mentions
    for mention in message.mentions:
        content = content.replace(mention.mention, f"@{mention.display_name}")
    for mention in message.channel_mentions:
        content = content.replace(mention.mention, f"@{mention.name}")
    for mention in message.role_mentions:
        content = content.replace(mention.mention, f"@{mention.name}")
    # quote it to make it clearer for the model
    if not model:
        content = quote(content)
    return content


def message_to_string(message: discord.Message, model: bool):
    replyheader = reply_to_string(reply) + "\n\n" \
        if (reply := get_reply(message)) \
        else ""

    userheader = "" if model else f"@{message.author.display_name} says:\n"

    return f"{replyheader}{userheader}{format_content(message, model)}"


@discord_client.event
async def on_message(message: discord.Message):
    if discord_client.user in message.mentions:
        async with message.channel.typing():
            # chain = await crawl_replies(message)
            # prompt the model to reply
            parts = [
                genai.types.Content(
                    role='model',
                    parts=[genai.types.Part.from_text(
                        text=reply_to_string(message))
                    ],
                )]

            messages = 0

            def handle_message(cmsg: discord.Message):
                nonlocal messages
                model = cmsg.author == discord_client.user
                parts.append(genai.types.Content(
                    role='model' if model else 'user',
                    parts=[genai.types.Part.from_text(text=message_to_string(cmsg, model))],
                ))
                messages += 1

            handle_message(message)

            oldest_reply = message

            async for cmsg in message.channel.history(before=message, limit=MAX_MESSAGES):
                if reply := get_reply(cmsg):
                    oldest_reply = reply
                # if we hit the min messages limit,
                #  and we havent found any older replies we need to continue going back on,
                #  end the history
                if messages >= MIN_MESSAGES and cmsg.created_at > oldest_reply.created_at:
                    break
                handle_message(cmsg)

                # if cmsg.author == discord_client.user:

            # make it newest last
            parts.reverse()

            print("PARTS:")
            print(parts)

            response = await generate(message, parts)
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
                        print(e.details)
                        for detail in e.details["error"]["details"]:
                            if detail["@type"] == "type.googleapis.com/google.rpc.RetryInfo":
                                if detail["retryDelay"][-1] == "s":
                                    delay = int(detail["retryDelay"][:-1])
                                    msg, _ = await asyncio.gather(
                                        message.reply(
                                            f"**gork is a little overloaded right now. give me {round(delay)} seconds to catch up!**"),
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
    asyncio.create_task(generate_loop())
    with open("discordkey.txt") as f:
        dtoken = f.read()
    await discord_client.start(token=dtoken)


gen_queue = asyncio.Queue()
asyncio.run(run())
