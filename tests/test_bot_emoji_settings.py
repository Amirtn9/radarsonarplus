"""Real database persistence and premium eligibility before changing the bot theme."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from telegram import InlineKeyboardButton,InlineKeyboardMarkup,MessageEntity
from telegram.error import BadRequest
from test_billing_workflow import service
from test_postgres_workflow import isolated_database
from bot_emoji import EmojiTheme,activate_pack,load_theme,save_theme,emoji_entities


def activation(service,monkeypatch,accepted=True):
    import database,settings
    monkeypatch.setattr(database,'Database',lambda:service.db)
    monkeypatch.setattr(settings,'SUPER_ADMIN_ID',901)
    pack=SimpleNamespace(name='NewsEmoji',sticker_type='custom_emoji',stickers=[SimpleNamespace(emoji='✅',custom_emoji_id='12345')])
    bot=SimpleNamespace(get_sticker_set=AsyncMock(return_value=pack),emoji_theme=None)
    def set_theme(theme):bot.emoji_theme=theme
    bot.set_emoji_theme=set_theme
    markup=InlineKeyboardMarkup([[InlineKeyboardButton('Preview',callback_data='admin_emoji_settings',api_kwargs={'icon_custom_emoji_id':'12345'})]])
    preview=SimpleNamespace(entities=[MessageEntity('custom_emoji',0,1,custom_emoji_id='12345')],reply_markup=markup)
    reply=AsyncMock(side_effect=[preview if accepted else BadRequest('CUSTOM_EMOJI_NOT_ALLOWED'),SimpleNamespace()])
    update=SimpleNamespace(effective_user=SimpleNamespace(id=901,is_premium=True),effective_message=SimpleNamespace(reply_text=reply))
    return update,SimpleNamespace(bot=bot)


@pytest.mark.asyncio
async def test_successful_preview_saves_global_theme_and_survives_reload(service,monkeypatch):
    from workspace_service import ACTIVE_WORKSPACE,ACTOR_ID
    update,context=activation(service,monkeypatch)
    # Selecting another technical workspace must never move this global setting.
    actor=ACTOR_ID.set(901);workspace=ACTIVE_WORKSPACE.set(903)
    try:assert await activate_pack(update,context,'https://t.me/addemoji/NewsEmoji')
    finally:ACTOR_ID.reset(actor);ACTIVE_WORKSPACE.reset(workspace)
    saved=load_theme(service.db,901)
    assert saved==context.bot.emoji_theme and saved.name=='NewsEmoji'
    assert load_theme(service.db,903) is None
    save_theme(service.db,901,None)
    assert load_theme(service.db,901) is None


@pytest.mark.asyncio
async def test_rejected_preview_keeps_old_theme_and_database(service,monkeypatch):
    old=EmojiTheme('Previous',(('🛰','999'),));save_theme(service.db,901,old)
    update,context=activation(service,monkeypatch,accepted=False)
    context.bot.set_emoji_theme(old)
    assert not await activate_pack(update,context,'https://t.me/addemoji/NewsEmoji')
    assert load_theme(service.db,901)==old and context.bot.emoji_theme==old


@pytest.mark.asyncio
async def test_silently_dropped_custom_button_does_not_activate(service,monkeypatch):
    update,context=activation(service,monkeypatch)
    preview=SimpleNamespace(entities=[MessageEntity('custom_emoji',0,1,custom_emoji_id='12345')],reply_markup=None)
    update.effective_message.reply_text.side_effect=[preview,SimpleNamespace()]
    assert not await activate_pack(update,context,'https://t.me/addemoji/NewsEmoji')
    assert load_theme(service.db,901) is None


def test_large_reports_keep_unicode_when_custom_entity_budget_is_full():
    theme=EmojiTheme('NewsEmoji',(('✅','12345'),))
    text='✅ '*150
    entities,changed=emoji_entities(text,[],theme)
    assert changed and len(entities)<=100
    assert text=='✅ '*150
    existing=[{'type':'bold','offset':i*2,'length':1} for i in range(100)]
    assert emoji_entities(text,existing,theme)==(existing,False)
