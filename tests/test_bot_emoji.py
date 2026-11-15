"""Custom emoji rendering, Bot API wire payloads and safe delivery fallbacks."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from telegram import InlineKeyboardButton,InlineKeyboardMarkup,MessageEntity
from telegram.error import NetworkError,BadRequest
from telegram.request import BaseRequest
from bot_emoji import EmojiTheme,EmojiBot,pack_name,decorate_payload,emoji_entities

THEME=EmojiTheme('NewsEmoji',(('🛰️','111'),('🖥️','222'),('✅','333')))


@pytest.mark.parametrize('link',[
    'https://evil.example/addemoji/NewsEmoji','http://t.me/addemoji/NewsEmoji',
    'https://t.me/addstickers/NewsEmoji','https://t.me/addemoji/A?redirect=evil',
    'https://t.me@evil.example/addemoji/NewsEmoji','https://t.me/addemoji/../secret'])
def test_pack_url_does_not_fetch_arbitrary_destinations(link):
    with pytest.raises(ValueError):pack_name(link)


def test_pack_validation_and_persistence_round_trip():
    assert pack_name('https://t.me/addemoji/NewsEmoji')=='NewsEmoji'
    assert EmojiTheme.loads(THEME.dumps())==THEME
    with pytest.raises(ValueError):EmojiTheme.from_stickers(SimpleNamespace(sticker_type='regular'))
    with pytest.raises(ValueError):EmojiTheme.loads('{"name":"../bad","items":[]}')


def test_keyboard_icons_keep_actions_and_do_not_mutate_shared_buttons():
    markup=InlineKeyboardMarkup([[InlineKeyboardButton('🖥️ سرورها',callback_data='v6_servers'),
        InlineKeyboardButton('ورود',url='https://example.org')]])
    original=markup.to_dict()
    payload,changed,_=decorate_payload('sendMessage',{'text':'سلام','reply_markup':markup},THEME)
    buttons=payload['reply_markup']['inline_keyboard'][0]
    assert changed and buttons[0]['icon_custom_emoji_id']=='222'
    assert buttons[0]['text']=='سرورها' and buttons[0]['callback_data']=='v6_servers'
    assert buttons[1]['url']=='https://example.org' and buttons[1]['icon_custom_emoji_id']
    assert markup.to_dict()==original


def test_utf16_entities_preserve_formatting_and_skip_code_and_existing_custom():
    text='😀 حرف 🛰️ و ✅'
    # Astral emoji before the target must count as two UTF-16 code units.
    offset=len(text[:text.index('🛰')].encode('utf-16-le'))//2
    existing=[MessageEntity('bold',0,2),MessageEntity('code',offset,3)]
    entities,changed=emoji_entities(text,existing,THEME)
    assert changed and existing[0].to_dict() in entities and existing[1].to_dict() in entities
    added=[e for e in entities if e['type']=='custom_emoji']
    assert len(added)==2 and all(e['offset']!=offset for e in added)
    entities2,changed2=emoji_entities(text,entities,THEME)
    assert entities2==entities and not changed2


def test_html_decorates_text_without_corrupting_links_or_code():
    source='<b>🛰️ وضعیت</b> &amp; <code>✅</code> <a href="https://example.org/✅">لینک</a> <tg-emoji emoji-id="999">✅</tg-emoji>'
    data,changed,markdown=decorate_payload('sendMessage',{'text':source,'parse_mode':'HTML'},THEME)
    assert changed and not markdown
    assert '<b><tg-emoji emoji-id="111">🛰️</tg-emoji> وضعیت</b>' in data['text']
    for unchanged in ('&amp;','<code>✅</code>','href="https://example.org/✅"','<tg-emoji emoji-id="999">✅</tg-emoji>'):
        assert unchanged in data['text']


def test_caption_and_non_message_endpoints_are_handled_separately():
    data,changed,_=decorate_payload('sendPhoto',{'caption':'✅ آماده'},THEME)
    assert changed and data['caption_entities'][0]['custom_emoji_id']=='333'
    original={'text':'✅ untouched'}
    assert decorate_payload('answerCallbackQuery',original,THEME)==(original,False,False)


class TelegramAPI(BaseRequest):
    def __init__(self,errors=None,edit_error=None):
        self.calls=[];self.errors=list(errors or []);self.edit_error=edit_error
    @property
    def read_timeout(self):return 5
    async def initialize(self):pass
    async def shutdown(self):pass
    async def do_request(self,url,method,request_data=None,**kwargs):
        endpoint=url.rsplit('/',1)[-1];data=copy.deepcopy(request_data.parameters if request_data else {})
        if endpoint=='getMe':
            result={'id':123456,'is_bot':True,'first_name':'Test','username':'synthetic_bot'}
        else:
            self.calls.append((endpoint,data))
            error=self.errors.pop(0) if self.errors else None
            if endpoint.startswith('edit') and self.edit_error:error=self.edit_error
            if isinstance(error,Exception):raise error
            if error:return 400,json.dumps({'ok':False,'error_code':400,'description':error}).encode()
            field='caption' if 'caption' in data else 'text'
            result={'message_id':9,'date':0,'chat':{'id':data.get('chat_id',901),'type':'private'},field:data.get(field,'')}
            if data.get('parse_mode')=='Markdown':
                result[field]='متن 🛰️';result['entities']=[{'type':'bold','offset':0,'length':3}]
            else:
                for key in ('entities','caption_entities','reply_markup'):
                    if key in data:result[key]=data[key]
        return 200,json.dumps({'ok':True,'result':result}).encode()


@pytest.mark.asyncio
async def test_real_bot_api_payload_supports_icons_on_pinned_ptb_without_upgrade():
    api=TelegramAPI();bot=EmojiBot('123456:synthetic-only',request=api);bot.set_emoji_theme(THEME)
    async with bot:
        await bot.send_message(901,'🛰️ وضعیت',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🖥 سرورها',callback_data='v6_servers')]]))
    endpoint,data=api.calls[0]
    assert endpoint=='sendMessage' and data['entities'][0]['custom_emoji_id']=='111'
    assert data['reply_markup']['inline_keyboard'][0][0]['icon_custom_emoji_id']=='222'


@pytest.mark.asyncio
async def test_markdown_preserves_telegram_parsed_entities_before_emoji_edit():
    api=TelegramAPI();bot=EmojiBot('123456:synthetic-only',request=api);bot.set_emoji_theme(THEME)
    async with bot:await bot.send_message(901,'*متن* 🛰️',parse_mode='Markdown')
    assert [e for e,_ in api.calls]==['sendMessage','editMessageText']
    edit=api.calls[1][1]
    assert edit['text']=='متن 🛰️' and 'parse_mode' not in edit
    assert {'type':'bold','offset':0,'length':3} in edit['entities']
    assert {'type':'custom_emoji','offset':4,'length':3,'custom_emoji_id':'111'} in edit['entities']


@pytest.mark.asyncio
async def test_custom_emoji_rejection_falls_back_once_and_is_isolated_to_chat():
    api=TelegramAPI(errors=['CUSTOM_EMOJI_NOT_ALLOWED']);bot=EmojiBot('123456:synthetic-only',request=api);bot.set_emoji_theme(THEME)
    async with bot:
        await bot.send_message(901,'✅ آماده')
        await bot.send_message(901,'✅ دوباره')
        await bot.send_message(902,'✅ مستقل')
    assert len(api.calls)==4
    assert 'entities' in api.calls[0][1] and 'entities' not in api.calls[1][1]
    assert 'entities' not in api.calls[2][1] and 'entities' in api.calls[3][1]


@pytest.mark.asyncio
async def test_network_error_is_never_replayed_as_another_message():
    api=TelegramAPI(errors=[NetworkError('uncertain delivery')]);bot=EmojiBot('123456:synthetic-only',request=api);bot.set_emoji_theme(THEME)
    async with bot:
        with pytest.raises(NetworkError):await bot.send_message(901,'✅ آماده')
    assert len(api.calls)==1


@pytest.mark.asyncio
async def test_markdown_edit_failure_does_not_duplicate_delivered_message():
    api=TelegramAPI(edit_error=NetworkError('edit unavailable'));bot=EmojiBot('123456:synthetic-only',request=api);bot.set_emoji_theme(THEME)
    async with bot:message=await bot.send_message(901,'*متن* 🛰️',parse_mode='Markdown')
    assert message.message_id==9
    assert [e for e,_ in api.calls]==['sendMessage','editMessageText']


@pytest.mark.asyncio
async def test_other_bad_request_is_not_hidden_by_emoji_fallback():
    api=TelegramAPI(errors=['chat not found']);bot=EmojiBot('123456:synthetic-only',request=api);bot.set_emoji_theme(THEME)
    async with bot:
        with pytest.raises(BadRequest):await bot.send_message(901,'✅ آماده')
    assert len(api.calls)==1


@pytest.mark.asyncio
@pytest.mark.parametrize('uid,premium',[(902,True),(901,False)])
async def test_activation_checks_real_telegram_admin_premium_before_fetch(monkeypatch,uid,premium):
    import settings
    from bot_emoji import activate_pack
    monkeypatch.setattr(settings,'SUPER_ADMIN_ID',901)
    bot=SimpleNamespace(get_sticker_set=AsyncMock())
    message=SimpleNamespace(reply_text=AsyncMock())
    update=SimpleNamespace(effective_user=SimpleNamespace(id=uid,is_premium=premium),effective_message=message)
    assert not await activate_pack(update,SimpleNamespace(bot=bot),'https://t.me/addemoji/NewsEmoji')
    bot.get_sticker_set.assert_not_awaited()
