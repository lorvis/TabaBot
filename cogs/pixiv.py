import asyncio
import emoji
import json
import modules.date as date
import os
import random
import time

from PIL import Image
from config import pixiv_show_embed_illust, use_selenium
from nextcord import Embed, File, slash_command, SlashOption
from nextcord.colour import Colour
from nextcord.ext import commands
from io import BytesIO
from modules.pixiv_auth import refresh_token, selenium_login
from pixivpy3 import *


class BetterAppPixivAPI(AppPixivAPI):
    def __init__(self, token, **requests_kwargs):
        super(AppPixivAPI, self).__init__(**requests_kwargs)
        self.hosts = 'https://app-api.pixiv.net'
        self.auth(refresh_token=token)

    def download(self, url, prefix='', path=os.path.curdir, name=None, replace=False, fname=None,
                 referer='https://app-api.pixiv.net/'):
        with self.requests_call('GET', url, headers={'Referer': referer}, stream=True) as response:
            return Image.open(response.raw)

    def search_autocomplete(self, word, req_auth=True):
        url = '%s/v2/search/autocomplete' % self.hosts
        params = {
            'word': word,
        }
        r = self.no_auth_requests_call('GET', url, params=params, req_auth=req_auth)
        return self.parse_result(r)


class PixivCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.api = {}
        self.channels = {}
        self.timers = {}
        self.tokens = {}
        self.last_query = {}
        self.last_type = {}
        self.token_expiration_time = None
        self.spoilers = {}
        self.load()

    def save(self, channels=False, timers=False, tokens=False):
        if channels:
            with open('json/auto_pixiv_channels.json', 'w') as file:
                file.write(json.dumps(self.channels))
        if timers:
            with open('json/auto_pixiv_timers.json', 'w') as file:
                file.write(json.dumps(self.timers))
        if tokens:
            with open('json/pixiv_tokens.json', 'w') as file:
                file.write(json.dumps(self.tokens))

    def load(self):
        try:
            with open('json/auto_pixiv_channels.json', 'r') as file:
                self.channels = json.load(file)
            with open('json/auto_pixiv_timers.json', 'r') as file:
                self.timers = json.load(file)
            with open('json/pixiv_tokens.json', 'r') as file:
                self.tokens = json.load(file)
            for guild, item in self.tokens.items():
                self.api[item["value"]] = BetterAppPixivAPI(token=item["value"])
        except:
            pass

    async def send_illust(self, api, illust, url, chat, num=None, show_title=False, color=None):
        img = api.download(url)
        img_type = url.split('.')[-1]
        filename = f'{str(illust.id)}.{img_type}'
        if num is not None:
            filename = f'{num}_{filename}'
        if self.spoilers and illust.sanity_level >= 6:
            filename = f'SPOILER_{filename}'
        with BytesIO() as image_binary:
            img.save(image_binary, img.format)
            image_binary.seek(0)
            file = File(fp=image_binary, filename=filename)
        if show_title:
            title = illust.title
            if pixiv_show_embed_illust:
                embed = Embed(description=f'Title: [{title}](https://www.pixiv.net/en/artworks/{illust.id})',
                              color=color)
                embed.set_image(url=f'attachment://{filename}')
                message = await chat.send(embed=embed, file=file)
            else:
                text = f'Title: {title}'
                if len(illust.meta_pages) > 0:
                    text += f', {len(illust.meta_pages)} images'
                message = await chat.send(text, file=file)
        else:
            message = await chat.send(file=file)
        if illust.is_bookmarked:
            await message.add_reaction(emoji.emojize(':red_heart:'))

    async def show_illust(self, api, illust_id, chat):
        try:
            illust = api.illust_detail(illust_id).illust
            await chat.send(embed=Embed(title=f'Fetching illustration {illust.title} in original quality...',
                                        color=Colour.green()), delete_after=5.0)
            if len(illust.meta_single_page) > 0:
                url = illust.meta_single_page.original_image_url
                await self.send_illust(api, illust, url, chat)
            for idx, item in enumerate(illust.meta_pages):
                url = item.image_urls.original
                await self.send_illust(api, illust, url, chat, idx)
        except:
            await chat.send(embed=Embed(title=f'Fail', color=Colour.red()), delete_after=5.0)
            return None

    async def show_page(self, api, query, chat, limit=30, minimum_views=None, minimum_rate=None, max_sanity=6):
        if chat is None or query is None or query.illusts is None or len(query.illusts) == 0:
            return 0, 0, False
        shown = 0
        print('fetched', len(query.illusts), 'images')
        color = Colour.random()
        for illust in query.illusts:
            if shown == limit:
                break
            if minimum_views is not None and illust.total_view < minimum_views:
                continue
            if minimum_rate is not None and illust.total_bookmarks / illust.total_view * 100 < minimum_rate:
                continue
            if max_sanity < illust.sanity_level:
                continue
            await self.send_illust(api, illust, illust.image_urls.large, chat, show_title=True, color=color)
            shown += 1
        return shown, len(query.illusts), True

    async def show_page_embed(self, api, query, query_type, chat, limit=None, save_query=True, user_id=None):
        shown, total, result = await self.show_page(api, query, chat, limit)
        if result:
            if save_query:
                self.last_query[user_id] = query
                self.last_type[user_id] = query_type
            return Embed(title="Illustrations loaded", color=Colour.green())
        else:
            return Embed(title="Pixiv API didn't give any illustrations", color=Colour.red())

    def get_api(self, server_id):
        server_id = str(server_id)
        if server_id in self.tokens:
            return self.api[self.tokens[server_id]['value']]
        else:
            return None

    @slash_command(name="pixiv_logout", description="Remove pixiv token from bot db")
    async def pixiv_logout(self, ctx):
        server = str(ctx.guild.id)
        if server in self.tokens:
            self.tokens.pop(server)
            embed = Embed(title="Successfully logged out", color=Colour.green())
        else:
            embed = Embed(title="This server is not logged in", color=Colour.gold())
        await ctx.send(embed=embed, delete_after=10.0)

    @slash_command(name="pixiv_token", description="Log in to Pixiv via refresh token")
    async def pixiv_token(self, ctx,
                          token: str = SlashOption(description="Your pixiv login", required=True)):
        await ctx.response.defer()
        try:
            a = BetterAppPixivAPI(token=token)
            server = str(ctx.guild.id)
            self.api[token] = a
            self.tokens[server] = {"value": token, "time": str(0)}
            embed = Embed(title="Successfully logged in :)", color=Colour.green())
            self.save(tokens=True)
        except:
            embed = Embed(title="Can't login with given token :(", color=Colour.red())
        await ctx.send(embed=embed, delete_after=10.0)

    if use_selenium:
        @slash_command(name="pixiv_login", description="Log in to Pixiv")
        async def pixiv_login(self, ctx,
                              login: str = SlashOption(description="Your pixiv login", required=True),
                              password: str = SlashOption(description="Your pixiv password", required=True)):
            await ctx.response.defer()
            try:
                token = await selenium_login(login, password)
                if token is None:
                    await ctx.send(embed=Embed(title="Can't log in. Captcha required :(\n"
                                                     "Try pixiv token instead",
                                               color=Colour.red()),
                                   delete_after=10.0)
                    return
                a = BetterAppPixivAPI(token=token)
                server = str(ctx.guild.id)
                self.api[token] = a
                self.tokens[server] = {"value": token, "time": str(0)}
                embed = Embed(title="Successfully logged in :)", color=Colour.green())
                self.save(tokens=True)
            except:
                embed = Embed(title="Can't log in with given token :(", color=Colour.red())
            await ctx.send(embed=embed, delete_after=10.0)

    @slash_command(name="pixiv_status", description="Show pixiv connection status")
    async def pixiv_status(self, ctx):
        await ctx.response.defer()
        server = str(ctx.guild.id)
        try:
            self.api[self.tokens[server]['value']].trending_tags_illust()
            embed = Embed(title="You logged in and API is working fine", color=Colour.green())
        except:
            embed = Embed(title="Either you are not connected or there is a problem with the API", color=Colour.red())
        await ctx.send(embed=embed, delete_after=10.0)

    @slash_command(name="start_auto_pixiv", description="Add channel to the auto Pixiv list")
    async def add_auto_pixiv(self, ctx,
                             refresh_time: int = SlashOption(
                                 description="Delay between auto update in minutes (default = 180)",
                                 required=False,
                                 default=180,
                                 min_value=60
                             ),
                             limit: int = SlashOption(
                                 description="Amount of pictures will be displayed (default = 20)",
                                 required=False,
                                 default=20,
                                 max_value=60
                             )):
        channel_id = str(ctx.channel.id)
        if channel_id in self.channels.keys():
            embed = Embed(title="Auto Pixiv already running on this channel", color=Colour.gold())
        else:
            self.channels[channel_id] = {"refresh_time": refresh_time * 60, "limit": limit}
            embed = Embed(title="Auto Pixiv is now running on this channel", color=Colour.green())
        await ctx.send(embed=embed)
        self.save(channels=True)

    @slash_command(name="stop_auto_pixiv", description="Delete channel from the auto Pixiv list")
    async def delete_auto_pixiv(self, ctx):
        channel_id = str(ctx.channel.id)
        if channel_id in self.channels.keys():
            self.channels.pop(channel_id)
            if channel_id in self.timers.keys():
                self.timers.pop(channel_id)
            embed = Embed(title="Channel has been deleted", color=Colour.green())
        else:
            embed = Embed(title="This channel not exist in the list", color=Colour.gold())
        await ctx.send(embed=embed)
        self.save(channels=True, timers=True)

    @slash_command(name="spoil_nsfw", description="Spoil NSFW in this server")
    async def change_spoiler(self, ctx):
        server_id = ctx.guild.id
        if server_id not in self.spoilers:
            self.spoilers[server_id] = False
        self.spoilers[server_id] = not self.spoilers
        if self.spoilers[server_id]:
            embed = Embed(title="NSFW pictures now will be spoiled", color=Colour.green())
        else:
            embed = Embed(title="NSFW spoiler feature turned off", color=Colour.red())
        await ctx.send(embed=embed, delete_after=5.0)

    @slash_command(name='next', description="Show the next page of your last 'best' or 'recommended' query")
    async def next(self, ctx, limit: int = SlashOption(
                                 description="Amount of pictures will be displayed (default = 20)",
                                 required=False,
                                 default=20,
                                 max_value=30
                             )):
        await ctx.response.defer()
        api = self.get_api(ctx.guild.id)
        if ctx.user.id in self.last_query:
            next_qs = api.parse_qs(self.last_query[ctx.user.id].next_url)
            query = None
            if self.last_type[ctx.user.id] == 'recommended':
                query = api.illust_recommended(**next_qs)
            elif self.last_type[ctx.user.id] == 'best':
                query = api.illust_ranking(**next_qs)
            embed = await self.show_page_embed(api, query, self.last_type[ctx.user.id], ctx.channel,
                                               limit, save_query=True, user_id=ctx.user.id)
        else:
            embed = Embed(title="Previous request not found", color=Colour.red())
        await ctx.send(embed=embed, delete_after=5.0)

    @slash_command(name='recommended', description="Show [10] recommended Pixiv illustrations")
    async def recommended(self, ctx, limit: int = SlashOption(
                                 description="Amount of pictures will be displayed (default = 10)",
                                 required=False,
                                 default=10,
                                 max_value=30
                             )):
        await ctx.response.defer()
        api = self.get_api(ctx.guild.id)
        try:
            query = api.illust_recommended()
            embed = await self.show_page_embed(api, query, 'recommended', ctx.channel, limit,
                                               save_query=True, user_id=ctx.user.id)
        except:
            embed = Embed(title="Authentication required!\nCall /pixiv_login first for more info", color=Colour.red())
        await ctx.send(embed=embed, delete_after=5.0)

    @slash_command(name='best', description='Find best [10] rated illustrations with specific mode and date')
    async def best(self, ctx,
                   mode: str = SlashOption(description="Specify one of the types",
                                           choices={"Day": "day",
                                                    "Week": "week",
                                                    "Month": "month",
                                                    "Day Male likes": "day_male",
                                                    "Day Female likes": "day_female",
                                                    "Day Ecchi": "day_r18",
                                                    "Day Male likes Ecchi": "day_male_r18",
                                                    "Week Ecchi": "week_r18",
                                                    },
                                           required=False,
                                           default="month"
                                           ),
                   limit: int = SlashOption(
                       description="Amount of pictures will be displayed (default = 10)",
                       required=False,
                       default=10,
                       max_value=30
                   ),
                   from_date: str = SlashOption(
                       description="Date of sample in format: YYYY-MM-DD",
                       required=False,
                       default=None
                   )):
        await ctx.response.defer()
        api = self.get_api(ctx.guild.id)
        try:
            query = api.illust_ranking(mode=mode, date=from_date, offset=None)
            embed = await self.show_page_embed(api, query, 'best', ctx.channel, limit,
                                               save_query=True, user_id=ctx.user.id)
        except:
            embed = Embed(title="Authentication required!\nCall /pixiv_login first for more info", color=Colour.red())
        await ctx.send(embed=embed, delete_after=5.0)

    @slash_command(name='when', description='Show remaining time until new illustrations')
    async def time_to_update(self, ctx):
        channel_id = str(ctx.channel.id)
        if channel_id in self.timers.keys() and channel_id in self.channels:
            refresh_time = self.channels[channel_id]['refresh_time']
            last_update_time = self.timers[channel_id]
            delta = int(refresh_time + last_update_time - time.time())
            embed = Embed(title=str(delta // 60) + ' min ' + str(delta % 60) + ' secs until autopost',
                          color=Colour.green())
        else:
            embed = Embed(title="Timer has not started", color=Colour.gold())
        await ctx.send(embed=embed, delete_after=10.0)

    @slash_command(name='tag', description='Get tagged name of word')
    async def tag(self, ctx,
                  word: str = SlashOption(description="Word, that will be translated to most popular related tag",
                                          required=True)):
        api = self.get_api(ctx.guild.id)
        try:
            query = api.search_autocomplete(word)
            tags = ''
            for i, tag in enumerate(query.tags):
                tags += str(i) + '. ' + tag.name
                if tag.translated_name is not None:
                    tags += ' - ' + tag.translated_name
                tags += '\n'
            embed = Embed(title="Tags on word '" + word + "' sorted from high to low popularity:",
                          description=tags, color=Colour.gold())
        except:
            embed = Embed(title="Authentication required!\nCall /pixiv_login first for more info", color=Colour.red())
        await ctx.send(embed=embed, delete_after=30.0)

    @slash_command(name='find', description='Find illustrations that satisfy the filters from random point of time')
    async def find(self, ctx,
                   word: str = SlashOption(
                       description="Find by specific word",
                       required=True
                   ),
                   match: str = SlashOption(
                       description="Word match rule (default = exact_match_for_tags)",
                       default="exact_match_for_tags",
                       choices={
                           "Partial match for tags": "partial_match_for_tags",
                           "Exact match for tags": "exact_match_for_tags",
                           "Title and caption": "title_and_caption"
                       },
                       required=False
                   ),
                   limit: int = SlashOption(
                       description="Maximum amount of pictures that will be shown (default 5, maximum = 20)",
                       default=5,
                       min_value=1,
                       max_value=20,
                       required=False
                   ),
                   views: int = SlashOption(
                       description="Required minimum amount of views (default = 10000)",
                       default=10000,
                       min_value=0,
                       max_value=100000,
                       required=False
                   ),
                   rate: float = SlashOption(
                       description="Required minimum percent of views/bookmarks (default = 10)",
                       default=10,
                       min_value=0,
                       max_value=75,
                       required=False
                   ),
                   max_sanity_level: int = SlashOption(
                       description="Filter illusts to a specified sanity level (default = 5, min = 2, max = 6)",
                       default=5,
                       min_value=2,
                       max_value=6,
                       required=False
                   ),
                   period: str = SlashOption(
                       description="Random Date period (no impact if from_date given)",
                       choices={
                           "new": date.back_in_months(1 * 12),
                           "2 years range": date.back_in_months(2 * 12),
                           "3 years range": date.back_in_months(3 * 12),
                           "6 years range": date.back_in_months(6 * 12),
                           "9 years range": date.back_in_months(9 * 12),
                           "all time period" : date.back_in_months(14 * 12)
                       },
                       default=date.back_in_months(4 * 12),
                       required=False
                   ),
                   from_date: str = SlashOption(
                       description="Fixed date in format YYYY-MM-DD from which search will be initialized",
                       default=None,
                       required=False
                   )):
        await ctx.response.defer()
        api = self.get_api(ctx.guild.id)
        channel = ctx.channel
        try:
            word = api.search_autocomplete(word).tags[0].name
        except:
            pass
        selected_date = date.random(period, date.current(), random.random())
        if date.is_valid(from_date):
            selected_date = from_date
        fetched, shown, offset, alive = 0, 0, 0, True
        try:
            while shown < limit and alive and fetched < 500:
                query = api.search_illust(word, search_target=match,
                                          end_date=selected_date, offset=offset)
                if len(query.illusts) == 0 and selected_date < date.current():
                    selected_date = date.next_year(selected_date)
                    offset = 0
                    continue
                good, total, alive = await self.show_page(api, query, channel, limit - shown,
                                                          views, rate, max_sanity_level)
                shown += good
                fetched += total
                if alive:
                    offset += total
            embed = Embed(title="Find with word " + word + " called",
                          description=str(fetched) + " images fetched in total",
                          color=Colour.green())
        except:
            embed = Embed(title="Authentication required!\nCall /pixiv_login first for more info", color=Colour.red())
        await ctx.send(embed=embed, delete_after=10.0)

    @slash_command(name='illust', description='Get pixiv illustration by ID')
    async def get_illust(self, ctx,
                         idx: str = SlashOption(description="Illustration ID", required=True)):
        await ctx.response.defer()
        await self.show_illust(self.get_api(ctx.guild.id), idx, ctx)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        api = self.get_api(payload.guild_id)
        channel = self.bot.get_channel(payload.channel_id)
        message = await channel.fetch_message(payload.message_id)

        user = self.bot.get_user(payload.user_id)

        try:
            demojized = emoji.demojize(payload.emoji.name)
        except TypeError:
            demojized = None
        if payload.user_id != self.bot.user.id:
            if message.attachments is not None and len(message.attachments) == 1 and \
                    message.author == self.bot.user:
                illust_id = message.attachments[0].filename.split('.')[0].split('_')[-1]
                emojis = {':red_heart:', ':growing_heart:', ':magnifying_glass_tilted_left:', ':seedling:',
                          ':broken_heart:', ':red_question_mark:', ':elephant:', ':face_vomiting:'}
                if demojized in emojis:
                    await message.remove_reaction(payload.emoji, user)

                if demojized == ':red_heart:' or demojized == ':elephant:':
                    try:
                        api.illust_bookmark_add(illust_id)
                        await message.add_reaction(emoji.emojize(':red_heart:'))
                    except:
                        await message.add_reaction(emoji.emojize(':thumbs_down:'))
                elif demojized == ':growing_heart:':
                    try:
                        api.illust_bookmark_add(illust_id)
                        await message.add_reaction(emoji.emojize(':red_heart:'))
                        query = api.illust_related(illust_id)
                        await self.show_page(api, query, message.channel, limit=5)
                    except:
                        await message.add_reaction(emoji.emojize(':thumbs_down:'))
                elif demojized == ':magnifying_glass_tilted_left:':
                    r = await self.show_illust(api, illust_id, message.channel)
                    if r:
                        await message.add_reaction(emoji.emojize(':thumbs_up:'))
                    else:
                        await message.add_reaction(emoji.emojize(':thumbs_down:'))
                elif demojized == ':seedling:':
                    try:
                        await message.add_reaction(emoji.emojize(':thumbs_up:'))
                        query = api.illust_related(illust_id)
                        await self.show_page(api, query, message.channel, limit=5)
                    except:
                        await message.add_reaction(emoji.emojize(':thumbs_down:'))
                elif demojized == ':face_vomiting:':
                    await message.delete()
                elif demojized == ':broken_heart:':
                    try:
                        api.illust_bookmark_delete(illust_id)
                        for r in message.reactions:
                            if r.me:
                                await r.remove(self.bot.user)
                        await message.add_reaction(emoji.emojize(':broken_heart:'))
                    except:
                        await message.add_reaction(emoji.emojize(':thumbs_down:'))
                elif demojized == ':red_question_mark:':
                    try:
                        await message.add_reaction(emoji.emojize(':thumbs_up:'))
                        illust = api.illust_detail(illust_id).illust
                        tags = ""
                        for tag in illust.tags:
                            tags += f'{tag.name}'
                            if tag.translated_name is not None:
                                tags += f' - {tag.translated_name}'
                            tags += ', '
                        tags = tags[:-2]
                        embed = Embed(title="Illustration info:",
                                      description=f'Title: [{illust.title}](https://www.pixiv.net/en/artworks/{illust.id})'
                                                  f', ID: {illust.id}'
                                                  f'\n\nViews: {illust.total_view}, Bookmarks: {illust.total_bookmarks}'
                                                  f'\n\nTags: {tags}',
                                      color=Colour.green())
                        await message.edit(embed=embed, suppress=False)
                        await asyncio.sleep(20)
                        await message.edit(suppress=True)
                    except:
                        await message.add_reaction(emoji.emojize(':thumbs_down:'))
        elif demojized in [':broken_heart:', ':thumbs_up:', ':thumbs_down:']:
            await asyncio.sleep(5)
            await message.remove_reaction(payload.emoji, user)

    async def auto_draw(self):
        try:
            for channel_id, options in self.channels.items():
                channel = self.bot.get_channel(int(channel_id))
                if channel is None:
                    continue
                api = self.get_api(channel.guild.id)
                refresh_time = options['refresh_time']
                limit = options['limit']
                timestamp = time.time()
                if channel_id not in self.timers.keys() or timestamp - self.timers[channel_id] > refresh_time:
                    self.timers[str(channel_id)] = timestamp
                    query = api.illust_recommended()
                    await self.show_page(api, query, channel, limit=limit)
                    self.save(timers=True)
        except RuntimeError:
            pass

    async def auto_refresh_tokens(self):
        timestamp = time.time()
        for server_id in self.tokens:
            if int(self.tokens[server_id]['time']) - timestamp < 1000:
                token, ttl = refresh_token(self.tokens[server_id]['value'])
                self.tokens[server_id]['time'] = str(int(timestamp + ttl))
                self.api[token].auth(refresh_token=token)
                print('pixiv token updated')

    @commands.Cog.listener()
    async def on_ready(self):
        while True:
            await asyncio.sleep(30)
            await self.auto_refresh_tokens()
            await self.auto_draw()
            self.save(channels=True, timers=True)


def setup(bot):
    bot.add_cog(PixivCog(bot))
