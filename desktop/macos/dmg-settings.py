"""Finder presentation only; no customer data or account settings are used."""
from pathlib import Path

application = Path(defines['app'])  # noqa: F821 - injected by dmgbuild
files = [str(application)]
symlinks = {'Applications': '/Applications'}
# Do not set hidden-extension FinderInfo on the already signed app.
# Finder normally hides the app suffix; signing metadata must stay untouched.
icon = str(application / 'Contents/Resources/AppIcon.icns')
background = defines['background']  # noqa: F821 - injected by dmgbuild
format = 'UDZO'
filesystem = 'HFS+'
window_rect = ((180, 160), (640, 400))
icon_locations = {application.name: (160, 200), 'Applications': (480, 200)}
icon_size = 96
text_size = 14
label_pos = 'bottom'
arrange_by = None
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
default_view = 'icon-view'
include_icon_view_settings = True
include_list_view_settings = False
