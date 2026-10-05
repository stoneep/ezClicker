"""
prefs.py — 애드온 환경설정: 단축키 목록 (on/off, 키 변경, 간단한 설명).

Edit > Preferences > Add-ons > Mirror Loop Select 에서 연다.

  - 체크박스    : 그 단축키를 켜고 끈다. (끄면 Blender 기본 동작이 그대로 나온다)
  - 키 버튼     : 누르고 새 키(수식키 포함)를 입력하면 바꾼다.
  - 되돌리기 버튼 : 바꾼 키가 있을 때만 나타나며, 기본 키로 되돌린다.
  - 설명 보기   : 각 단축키 아래의 한 줄 설명을 켜고 끈다.

사용자가 바꾼 값은 Blender 의 사용자 키맵에 저장된다. 그래서 애드온이 등록한 키맵 항목이 아니라
'사용자 키맵'의 같은 항목을 찾아서(find_user_kmi) 그 항목을 그린다.
"""

import bpy
from bpy.props import BoolProperty

from . import state


def _same_props(a, b):
    """두 오퍼레이터 프로퍼티 묶음의 값이 모두 같은지."""
    if a is None or b is None:
        return a is b
    for name in a.bl_rna.properties.keys():
        if name == "rna_type":
            continue
        try:
            if getattr(a, name) != getattr(b, name):
                return False
        except (AttributeError, TypeError):
            pass
    return True


def find_user_kmi(entry):
    """애드온이 등록한 항목(entry)에 대응하는 사용자 키맵 항목을 찾는다. 없으면 None."""
    kc = bpy.context.window_manager.keyconfigs.user
    if kc is None:
        return None
    km = kc.keymaps.get(entry['km_name'])
    if km is None:
        return None
    for kmi in km.keymap_items:
        if kmi.idname == entry['idname'] and _same_props(kmi.properties, entry['kmi'].properties):
            return kmi
    return None


class MLS_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    show_descriptions: BoolProperty(
        name="설명 보기",
        description="각 단축키 아래에 한 줄 설명을 보여준다",
        default=True,
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "show_descriptions")

        box = layout.box()
        box.label(text="단축키", icon='EVENT_A')
        if not state.keymap_items:
            box.label(text="등록된 단축키가 없습니다")
            return

        for entry in state.keymap_items:
            kmi = find_user_kmi(entry)
            col = box.column(align=True)
            row = col.row(align=True)
            if kmi is None:
                row.label(text=entry['title'] + "  (키맵에서 찾을 수 없음)", icon='ERROR')
            else:
                row.prop(kmi, "active", text="")
                sub = row.row(align=True)
                sub.active = kmi.active
                sub.label(text=entry['title'])
                sub.prop(kmi, "type", text="", full_event=True)
                if kmi.is_user_modified:
                    row.operator("preferences.keyitem_restore", text="", icon='BACK').item_id = kmi.id
            if self.show_descriptions and entry['desc']:
                desc = col.row()
                desc.active = False
                desc.label(text="      " + entry['desc'])
            box.separator(factor=0.3)


classes = (MLS_Preferences,)
