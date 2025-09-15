import asyncio
import enum
import json
import os
from xml.sax.expatreader import ExpatLocator

import aiofiles
from discord.ext import tasks
from google import genai
from google.genai.errors import ClientError

with open("geminikey.txt") as f:
    gemini_client = genai.Client(api_key=f.read())
with open("prompt.txt") as f:
    prompt = f.read()


class Exclusion(enum.IntEnum):
    NONE = 0
    CHAIN = 1
    MENTION = 2
    ALL = 3


model_name = 'gemini-2.0-flash'

MIN_MESSAGES = 25
MAX_MESSAGES = 100

import discord

intents = discord.Intents.default()
intents.message_content = True

discord_client = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(discord_client)


@discord_client.event
async def on_ready():
    await tree.sync()
    print(f'We have logged in as {discord_client.user}')


exclusions = {}


def get_exclusion(user: discord.User):
    return Exclusion(exclusions.get(str(user.id), Exclusion.NONE))


async def set_exclusion(user: discord.User, level: Exclusion):
    exclusions[str(user.id)] = level
    async with aiofiles.open('exclusions.json', mode='w+') as f:
        await f.write(json.dumps(exclusions))


def init_exclusions():
    if os.path.isfile('exclusions.json'):
        with open('exclusions.json', mode='r') as f:
            global exclusions
            exclusions = json.load(f)


class ExcludeButton(discord.ui.Button):
    def __init__(self, level: Exclusion):
        self.level = level
        super().__init__(label=level.name)

    async def callback(self, interaction: discord.Interaction):
        await set_exclusion(interaction.user, self.level)
        await interaction.response.send_message(f"Set exclusion level to {self.level.name}", ephemeral=True)


@tree.command()
async def exclude(interaction: discord.Interaction):
    """
    Set your level of exclusion from the gork bot
    """
    try:
        view = discord.ui.View()
        for e in Exclusion:
            view.add_item(ExcludeButton(e))
        await interaction.response.send_message(
            f"""
By default, gork collects {MIN_MESSAGES}-{MAX_MESSAGES} messages of message context whenever it is invoked (collects more to follow reply chains).
Keep in mind gork nor gemini never permanently store any user messages.
If you would like to opt out of this, there are 4 levels of exclusion:
    **NONE**: Included in automatic channel context **(default)**
    **CHAIN**: Only included in reply chains (this was the old default)
    **MENTION**: Only included when you directly mention/reply to gork
    **ALL**: Completely excluded from gork (you can no longer mention gork)
Your current exclusion level is **{get_exclusion(interaction.user).name}**
""", view=view, ephemeral=True)
    except Exception as e:
        print(e)


# message_fetch_cache = {}
#
#
# async def fetch_and_cache(msg: discord.Message):
#     if msg.id in message_fetch_cache:
#         return message_fetch_cache[msg.id]
#     else:
#         res = await msg.fetch()
#         message_fetch_cache[msg.id] = res
#         return res
#
#
# async def crawl_replies(message: discord.Message):
#     replies = []
#     if message.type == discord.MessageType.reply:
#         if ref := message.reference:
#             res = ref.cached_message
#             if not res:
#                 res = await fetch_and_cache(ref.resolved)
#             if isinstance(res, discord.Message):
#                 replies = await crawl_replies(res)
#     return replies + [message]

def reply_to_string(ref: discord.Message):
    return (f"Replying to:\n"
            f"{quote(f"@{ref.author.display_name} says:\n"
                     f"{format_content(ref, False)}")}")


def get_reply(message: discord.Message, level: Exclusion = Exclusion.NONE):
    if (message.reference
            and message.reference.resolved
            and message.type == discord.MessageType.reply
            and get_exclusion(message.reference.resolved.author) <= level
    ):
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


def message_to_string(message: discord.Message, model: bool, level: Exclusion = Exclusion.NONE):
    replyheader = reply_to_string(reply) + "\n\n" \
        if (reply := get_reply(message,
                               # maintain chain if we up chaining, otherwise its just exists
                               Exclusion.CHAIN if level >= Exclusion.CHAIN else Exclusion.NONE
                               )) \
        else ""

    userheader = "" if model else f"@{message.author.display_name} says:\n"

    attachment_footer = f"\n[{len(message.attachments)} attachments]" if len(message.attachments) > 0 else ""
    embed_footer = f"\n[{len(message.embeds)} attachments]" if len(message.embeds) > 0 else ""

    return f"{replyheader}{userheader}{format_content(message, model)}{attachment_footer}{embed_footer}"


@discord_client.event
async def on_message(message: discord.Message):
    if discord_client.user in message.mentions and get_exclusion(message.author) < Exclusion.ALL:
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
                nonlocal chain
                model = cmsg.author == discord_client.user
                user_excl = get_exclusion(cmsg.author)  # should always be 0 for model
                if cmsg == message:
                    excl_level = Exclusion.MENTION
                elif cmsg.id in chain:
                    excl_level = Exclusion.CHAIN
                else:
                    excl_level = Exclusion.NONE

                if user_excl <= excl_level:
                    parts.append(genai.types.Content(
                        role='model' if model else 'user',
                        parts=[genai.types.Part.from_text(text=message_to_string(cmsg, model, excl_level))],
                    ))
                    messages += 1
                else:
                    print(f"Excluded message {cmsg.author} says {cmsg.content}")

            chain = [message.id]

            if reply := get_reply(message):
                chain.append(reply.id)

            handle_message(message)
            oldest_reply = message

            async for cmsg in message.channel.history(before=message, limit=MAX_MESSAGES):
                in_chain = cmsg.id in chain
                if reply := get_reply(cmsg, Exclusion.CHAIN if in_chain else Exclusion.NONE):
                    if in_chain:
                        chain.append(reply.id)
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

            print("\nPARTS:")
            print(parts)
            print()

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
    init_exclusions()
    await discord_client.start(token=dtoken)


gen_queue = asyncio.Queue()
asyncio.run(run())
