"""Per-user navigation state for categorized Telegram menus.

Only a category slug is stored. No WebSocket/agent settings are referenced.
Never serialize user credentials into UI state.
"""

CATEGORIES = frozenset(('servers', 'configs', 'monitor', 'tools', 'settings', 'account', 'admin'))
KEY = '_sonar_navigation_section'


def remember_section(context, section):
    if section not in CATEGORIES:
        raise ValueError('Unknown section')
    if context.user_data.get(KEY) != section:
        context.user_data.pop('last_config_list_route', None)
    context.user_data[KEY] = section


def get_section(context):
    value = context.user_data.get(KEY)
    return value if value in CATEGORIES else None


def clear_section(context):
    context.user_data.pop(KEY, None)


def section_parent(context):
    value = get_section(context)
    return 'v6_' + value if value else 'main_menu'
