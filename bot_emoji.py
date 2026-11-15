"""Persistent, administrator-selected Telegram custom emoji with Unicode fallback.

The installed PTB version predates button icons; api_kwargs/to_dict preserve
the Bot API field without changing the dependency lock. Eligibility is enforced
by Telegram, not inferred from a user's commercial Sonar subscription.
"""
from __future__ import annotations
import hashlib
import html
from html.parser import HTMLParser
import json
import logging
import re
from dataclasses import dataclass
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity
from telegram.error import BadRequest, TelegramError
from telegram.ext import ExtBot, ApplicationHandlerStop, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from telegram.request import HTTPXRequest
from telegram_safety import safe_callback_answer

log=logging.getLogger(__name__)
SETTING='bot_custom_emoji_theme'
DEFAULT_PACK='https://t.me/addemoji/NewsEmoji'
PACK_LINK=r'https://(?:t\.me|telegram\.me)/addemoji/([A-Za-z0-9_]{1,64})'
EMOJI=re.compile(
    r'[\U0001f1e6-\U0001f1ff]{2}|[0-9#*]\ufe0f?\u20e3|'
    r'[\U0001f300-\U0001faff\u2300-\u23ff\u2600-\u27bf]\ufe0f?'
    r'[\U0001f3fb-\U0001f3ff]?(?:\u200d[\U0001f300-\U0001faff\u2600-\u27bf]'
    r'\ufe0f?[\U0001f3fb-\U0001f3ff]?)*')
MESSAGE_ENDPOINTS=frozenset(('sendMessage','editMessageText','sendPhoto','sendDocument',
    'sendVideo','sendAnimation','sendAudio','sendVoice','editMessageCaption','editMessageReplyMarkup'))


def pack_name(link):
    match=re.fullmatch(PACK_LINK,str(link).strip())
    if not match:raise ValueError('لینک معتبر https://t.me/addemoji/PackName ارسال کنید.')
    return match[1]


def _glyph(value):return value.replace('\ufe0f','').replace('\ufe0e','')
def _utf16(value):return len(value.encode('utf-16-le'))//2
def _dict(value):return value.to_dict() if hasattr(value,'to_dict') else dict(value)


@dataclass(frozen=True)
class EmojiTheme:
    name:str
    items:tuple[tuple[str,str],...]

    @classmethod
    def from_stickers(cls,pack):
        if pack.sticker_type!='custom_emoji':raise ValueError('بسته باید از نوع ایموجی سفارشی باشد، نه استیکر.')
        items=[]
        for sticker in pack.stickers[:200]:
            ident=str(sticker.custom_emoji_id or '')
            if ident.isdigit() and len(ident)<=32:
                items.append((str(sticker.emoji or '✨')[:64],ident))
        if not items:raise ValueError('این بسته ایموجی قابل استفاده ندارد.')
        return cls(pack.name,tuple(items))

    @classmethod
    def loads(cls,value):
        data=json.loads(value)
        if not re.fullmatch(r'[A-Za-z0-9_]{1,64}',data['name']):raise ValueError('Invalid pack name')
        items=tuple((str(glyph),str(ident)) for glyph,ident in data['items'])
        if not 1<=len(items)<=200 or any(not ident.isdigit() or len(ident)>32 or not glyph or len(glyph)>64 for glyph,ident in items):
            raise ValueError('Invalid emoji theme')
        return cls(data['name'],items)

    def dumps(self):return json.dumps({'name':self.name,'items':self.items},ensure_ascii=False)

    def choose(self,glyph='',key=''):
        for candidate,ident in self.items:
            if _glyph(candidate)==_glyph(glyph):return ident
        # A stable theme icon decorates buttons even if the set has no exact
        # Unicode counterpart. Keep text labels and callback routes unchanged.
        index=int.from_bytes(hashlib.sha256((key or glyph).encode()).digest()[:4],'big')%len(self.items)
        return self.items[index][1]


def load_theme(db,admin_id):
    row=db.execute('SELECT value FROM settings WHERE owner_id=%s AND key=%s',(admin_id,SETTING),fetch='one')
    if not row or not row['value']:return None
    try:return EmojiTheme.loads(row['value'])
    except (ValueError,TypeError,KeyError):
        log.warning('Ignoring invalid saved emoji theme');return None


def save_theme(db,admin_id,theme):
    # Never route this global administrator setting through an active workspace.
    db.execute('''INSERT INTO settings(owner_id,key,value) VALUES(%s,%s,%s)
        ON CONFLICT(owner_id,key) DO UPDATE SET value=EXCLUDED.value''',
        (admin_id,SETTING,theme.dumps() if theme else ''))


def decorate_keyboard(markup,theme):
    value=_dict(markup)
    if 'inline_keyboard' not in value:return markup,False
    rows=[];changed=False
    for row in value['inline_keyboard']:
        buttons=[]
        for original in row:
            button=_dict(original)
            if not button.get('icon_custom_emoji_id'):
                text=button.get('text','')
                match=EMOJI.search(text)
                button['icon_custom_emoji_id']=theme.choose(match[0] if match else '',button.get('callback_data') or button.get('url') or text)
                # An existing leading emoji becomes the premium icon, rather
                # than showing both copies side by side.
                if match and not text[:match.start()].strip():button['text']=text[match.end():].strip() or text
                changed=True
            buttons.append(button)
        rows.append(buttons)
    return {**value,'inline_keyboard':rows},changed


def emoji_entities(text,existing,theme):
    entities=[_dict(entity) for entity in (existing or ())]
    budget=min(80,max(0,100-len(entities)))
    blocked=[(e['offset'],e['offset']+e['length']) for e in entities if e['type'] in ('code','pre','custom_emoji','text_link','url')]
    added=False
    for match in EMOJI.finditer(text):
        if budget<=0:break
        start=_utf16(text[:match.start()]);length=_utf16(match[0])
        if any(start<end and start+length>begin for begin,end in blocked):continue
        entities.append({'type':'custom_emoji','offset':start,'length':length,'custom_emoji_id':theme.choose(match[0])})
        budget-=1
        added=True
    return entities,added


class _EmojiHTML(HTMLParser):
    def __init__(self,theme,budget=80):
        super().__init__(convert_charrefs=False);self.theme=theme;self.parts=[];self.stack=[];self.changed=False
        self.budget=budget
    def handle_starttag(self,tag,attrs):
        self.parts.append(self.get_starttag_text());self.stack.append(tag)
    def handle_endtag(self,tag):
        self.parts.append('</'+tag+'>')
        if tag in self.stack:
            while self.stack.pop()!=tag:pass
    def handle_startendtag(self,tag,attrs):self.parts.append(self.get_starttag_text())
    def handle_entityref(self,name):self.parts.append('&'+name+';')
    def handle_charref(self,name):self.parts.append('&#'+name+';')
    def handle_data(self,data):
        if any(tag in self.stack for tag in ('code','pre','tg-emoji','a')):
            self.parts.append(data);return
        def replace(match):
            if self.budget<=0:return match[0]
            self.budget-=1
            self.changed=True
            return '<tg-emoji emoji-id="'+self.theme.choose(match[0])+'">'+match[0]+'</tg-emoji>'
        self.parts.append(EMOJI.sub(replace,data))


def decorate_payload(endpoint,data,theme):
    if endpoint not in MESSAGE_ENDPOINTS:return data,False,False
    out=dict(data);changed=False;markdown=False
    if data.get('reply_markup'):
        out['reply_markup'],changed=decorate_keyboard(data['reply_markup'],theme)
    field='text' if isinstance(data.get('text'),str) else 'caption'
    text=data.get(field)
    if isinstance(text,str) and EMOJI.search(text):
        mode=str(data.get('parse_mode') or '').lower()
        if mode=='html':
            formatting=len(re.findall(r'<[A-Za-z][^>]*>',text))
            parser=_EmojiHTML(theme,min(80,max(0,100-formatting)));parser.feed(text);parser.close()
            if parser.changed:out[field]=''.join(parser.parts);changed=True
        elif mode in ('markdown','markdownv2'):
            # Use Telegram's parsed entities after delivery; never guess at
            # Markdown nesting, escapes or UTF-16 offsets in formatted source.
            markdown=True
        elif not mode:
            key='entities' if field=='text' else 'caption_entities'
            entities,added=emoji_entities(text,data.get(key),theme)
            if added:out[key]=entities;changed=True
    return out,changed,markdown


def _emoji_denial(error):
    message=str(error).lower()
    return any(term in message for term in ('custom emoji','custom_emoji','premium','emoji_not_allowed','emoji document'))


class EmojiBot(ExtBot):
    __slots__=('emoji_theme','emoji_blocked')
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        object.__setattr__(self,'emoji_theme',None);object.__setattr__(self,'emoji_blocked',set())
    def set_emoji_theme(self,theme):
        object.__setattr__(self,'emoji_theme',theme);self.emoji_blocked.clear()

    def _block_emoji(self,target):
        if len(self.emoji_blocked)>=512:self.emoji_blocked.clear()
        self.emoji_blocked.add(target)

    async def _do_post(self,endpoint,data,**kwargs):
        theme=self.emoji_theme
        target=str(data.get('chat_id') or data.get('inline_message_id') or '')
        if theme is None or target in self.emoji_blocked:return await super()._do_post(endpoint,data,**kwargs)
        decorated,changed,markdown=decorate_payload(endpoint,data,theme)
        try:result=await super()._do_post(endpoint,decorated,**kwargs)
        except BadRequest as error:
            # Only a definitive custom-emoji rejection is replayed. Never retry
            # a timeout/network error: delivery may already have succeeded.
            if not changed or not _emoji_denial(error):raise
            self._block_emoji(target)
            log.warning('Custom emoji rejected; using Unicode fallback')
            return await super()._do_post(endpoint,data,**kwargs)
        if markdown and isinstance(result,dict) and result.get('message_id') and result.get('chat'):
            field='text' if 'text' in result else 'caption'
            text=result.get(field)
            if not isinstance(text,str):return result
            key='entities' if field=='text' else 'caption_entities'
            entities,added=emoji_entities(text,result.get(key),theme)
            if not added:return result
            edit={'chat_id':result['chat']['id'],'message_id':result['message_id'],field:text,key:entities}
            if decorated.get('reply_markup'):edit['reply_markup']=decorated['reply_markup']
            try:
                updated=await super()._do_post('editMessageText' if field=='text' else 'editMessageCaption',edit,**kwargs)
                if isinstance(updated,dict):result=updated
            except TelegramError as error:
                if isinstance(error,BadRequest) and _emoji_denial(error):self._block_emoji(target)
                # The original message was delivered. Never send it again.
                log.warning('Emoji formatting edit unavailable: %s',type(error).__name__)
        return result


def build_bot(token):
    return EmojiBot(token=token,request=HTTPXRequest(connection_pool_size=256,connect_timeout=60,
        read_timeout=60,write_timeout=60,pool_timeout=30),get_updates_request=HTTPXRequest(
        connect_timeout=60,read_timeout=60,write_timeout=60,pool_timeout=30))


async def themed_bot(token,db):
    """Use the same saved theme for Web-originated notification workers."""
    import asyncio
    from settings import SUPER_ADMIN_ID
    bot=build_bot(token)
    try:bot.set_emoji_theme(await asyncio.to_thread(load_theme,db,SUPER_ADMIN_ID))
    except Exception:log.warning('Notification emoji settings unavailable; using Unicode')
    return bot


def _admin(update):
    from settings import SUPER_ADMIN_ID
    return bool(SUPER_ADMIN_ID and update.effective_user and update.effective_user.id==SUPER_ADMIN_ID)


async def settings_menu(update,context):
    if not _admin(update):return
    await safe_callback_answer(update.callback_query)
    theme=getattr(context.bot,'emoji_theme',None)
    status='فعال: '+theme.name if theme else 'ایموجی معمولی'
    text=('🎨 ایموجی‌های ربات\n\nوضعیت: '+status+'\n\n'
        'برای فعال‌سازی، لینک بسته سفارشی را همین‌جا بفرستید؛ مثال:\n'+DEFAULT_PACK+'\n\n'
        'ادمین ثبت‌کننده باید Telegram Premium داشته باشد. برای نمایش توسط ربات، مالک ربات هم باید پریمیوم باشد یا ربات نام کاربری واجدشرایط Fragment داشته باشد؛ نتیجه با تلگرام آزمایش می‌شود.\n'
        'آیکون دکمه‌ها و ایموجی پیام‌های پشتیبانی‌شده خودکار تغییر می‌کنند. برای بسته فاقد معادل دقیق، آیکون ثابتی از همان بسته انتخاب می‌شود.\n'
        'در صورت لغو مجوز تلگرام، ایموجی معمولی جایگزین می‌شود.')
    menu=InlineKeyboardMarkup([[InlineKeyboardButton('✨ استفاده از NewsEmoji',callback_data='admin_emoji_news')],
        [InlineKeyboardButton('↩️ بازگشت به ایموجی معمولی',callback_data='admin_emoji_disable')],
        [InlineKeyboardButton('🔙 مدیریت',callback_data='admin_panel_main')]])
    await update.effective_message.reply_text(text,reply_markup=menu,parse_mode=None)


async def activate_pack(update,context,link):
    if not _admin(update):return False
    if not update.effective_user.is_premium:
        await update.effective_message.reply_text('برای ثبت بسته ایموجی، حساب تلگرام ادمین باید پریمیوم باشد. ایموجی معمولی فعال می‌ماند.')
        return False
    try:
        name=pack_name(link)
        pack=await context.bot.get_sticker_set(name)
        theme=EmojiTheme.from_stickers(pack)
    except (ValueError,TelegramError) as error:
        await update.effective_message.reply_text('بسته دریافت نشد؛ لینک addemoji معتبر و قابل دسترس ارسال کنید. بسته قبلی تغییر نکرد.')
        log.info('Emoji pack unavailable: %s',type(error).__name__);return False
    glyph,ident=theme.items[0]
    preview=InlineKeyboardMarkup([[InlineKeyboardButton('نمونه آیکون بسته',callback_data='admin_emoji_settings',
        api_kwargs={'icon_custom_emoji_id':ident})]])
    try:
        sent=await update.effective_message.reply_text('<tg-emoji emoji-id="'+ident+'">'+html.escape(glyph)+'</tg-emoji> نمونه بسته '+html.escape(theme.name),parse_mode='HTML',reply_markup=preview)
        if not any(entity.type==MessageEntity.CUSTOM_EMOJI for entity in sent.entities or ()):
            raise ValueError('Telegram did not retain the custom emoji')
        markup=_dict(sent.reply_markup) if sent.reply_markup else {}
        buttons=markup.get('inline_keyboard') or []
        if not buttons or _dict(buttons[0][0]).get('icon_custom_emoji_id')!=ident:
            raise ValueError('Telegram did not retain the custom button icon')
    except (TelegramError,ValueError):
        await update.effective_message.reply_text('تلگرام استفاده این ربات از ایموجی سفارشی را تأیید نکرد. پریمیوم بودن مالک ربات یا مجوز Fragment را بررسی کنید. تنظیم قبلی حفظ شد.')
        return False
    from database import Database
    from settings import SUPER_ADMIN_ID
    import asyncio
    await asyncio.to_thread(save_theme,Database(),SUPER_ADMIN_ID,theme)
    context.bot.set_emoji_theme(theme)
    await update.effective_message.reply_text('✅ بسته '+theme.name+' فعال و ذخیره شد. با /menu منوی جدید را ببینید.')
    return True


async def command(update,context):
    if not _admin(update):return
    if context.args:await activate_pack(update,context,' '.join(context.args))
    else:await settings_menu(update,context)
    raise ApplicationHandlerStop


async def receive_pack(update,context):
    if not _admin(update):return
    await activate_pack(update,context,update.effective_message.text.strip())
    raise ApplicationHandlerStop


async def action(update,context):
    if not _admin(update):return
    await safe_callback_answer(update.callback_query)
    if update.callback_query.data=='admin_emoji_news':await activate_pack(update,context,DEFAULT_PACK)
    elif update.callback_query.data=='admin_emoji_disable':
        from database import Database
        from settings import SUPER_ADMIN_ID
        import asyncio
        await asyncio.to_thread(save_theme,Database(),SUPER_ADMIN_ID,None)
        context.bot.set_emoji_theme(None)
        await update.effective_message.reply_text('↩️ ایموجی معمولی فعال شد.')
    else:await settings_menu(update,context)
    raise ApplicationHandlerStop


def register(app):
    from settings import SUPER_ADMIN_ID
    if not SUPER_ADMIN_ID:return
    app.add_handler(CommandHandler('emoji',command,filters=filters.User(SUPER_ADMIN_ID)),group=-2)
    app.add_handler(CallbackQueryHandler(action,pattern=r'^admin_emoji_(?:settings|news|disable)$'),group=-2)
    app.add_handler(MessageHandler(filters.User(SUPER_ADMIN_ID)&filters.TEXT&~filters.COMMAND&
        filters.Regex(r'^\s*'+PACK_LINK+r'\s*$'),receive_pack),group=-2)
