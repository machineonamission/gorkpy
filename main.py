import asyncio
import enum
import json
import os
import traceback

import aiofiles
import ollama

messages = [
    {
        'role': 'user',
        'content': 'Why is the sky blue?',
    },
]

with open("persist/ollamakey.txt") as f:
    client = ollama.AsyncClient(
        host="https://ollama.com",
        headers={'Authorization': 'Bearer ' + f.read().strip()}
    )
with open("prompt.txt") as f:
    prompt = f.read()


class Exclusion(enum.IntEnum):
    NONE = 0
    CHAIN = 1
    MENTION = 2
    ALL = 3


# model_name = 'gemini-2.0-flash'

MIN_MESSAGES = 10
MAX_MESSAGES = 30

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
    async with aiofiles.open('persist/exclusions.json', mode='w+') as f:
        await f.write(json.dumps(exclusions))


def init_exclusions():
    if os.path.isfile('persist/exclusions.json'):
        with open('persist/exclusions.json', mode='r') as f:
            global exclusions
            exclusions = json.load(f)


class ExcludeButton(discord.ui.Button):
    def __init__(self, level: Exclusion):
        self.level = level
        super().__init__(label=level.name)

    async def callback(self, interaction: discord.Interaction):
        await set_exclusion(interaction.user, self.level)
        print(f"Set exclusion level for {interaction.user} to {self.level.name}")
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
Keep in mind gork nor ollama never permanently store any user messages.
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
            and isinstance(message.reference.resolved, discord.Message)
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
    try:
        if discord_client.user in message.mentions and get_exclusion(message.author) < Exclusion.ALL:
            async with message.channel.typing():
                # chain = await crawl_replies(message)
                # prompt the model to reply
                parts = [
                    {
                        "role": "assistant",
                        "content": reply_to_string(message) + "\n\n"
                    }
                ]

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
                        parts.append(
                            {
                                "role": 'assistant' if model else 'user',
                                "content": message_to_string(cmsg, model, excl_level)
                            }
                        )
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

                parts.insert(0, {
                    "role": "system",
                    "content": prompt
                })

                print("\nPARTS:")
                print(parts)
                print()

                response = await generate(message, parts)
                await message.reply(response)
    except Exception as e:
        traceback.print_exception(e)
        try:
            await message.reply(f"```\n{e}\n{''.join(traceback.format_exception(e))}\n```"[:2000])
        except Exception as b:
            traceback.print_exception(b)
        # raise e


async def generate_loop():
    while True:
        message, parts, fut = await gen_queue.get()  # sleep until item arrives
        try:
            response = await client.chat(model="gemma4:31b-cloud", messages=parts, options={
                "temperature": 1.7, "num_predict": 1000
            }, stream=False, think=False)
            fut.set_result(response.message.content)  # return to the waiter
        except Exception as e:
            fut.set_exception(e)  # propagate errors
        finally:
            gen_queue.task_done()


async def generate(message, parts):
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    await gen_queue.put((message, parts, fut))
    return await fut


async def run():
    asyncio.create_task(generate_loop())
    with open("persist/discordkey.txt") as f:
        dtoken = f.read()
    init_exclusions()
    await discord_client.start(token=dtoken)


gen_queue = asyncio.Queue()
asyncio.run(run())
