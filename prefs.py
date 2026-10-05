"""
prefs.py — 애드온 환경설정: 단축키 목록 (on/off, 키 변경, 간단한 설명).

Edit > Preferences > Add-ons > Mirror Loop Select 에서 연다.

한 줄에 하나:  [체크박스]  기능 이름 (간단한 설명)  |  단축키

  - 체크박스    : 그 기능(단축키)을 켜고 끈다. (끄면 Blender 기본 동작이 그대로 나온다)
  - 단축키 버튼 : 누르고 새 키(수식키 포함)를 입력하면 바꾼다.
  - 되돌리기 버튼 : 바꾼 키가 있을 때만 나타나며, 기본 키로 되돌린다.

사용자가 바꾼 값은 Blender 의 사용자 키맵에 저장된다. 그래서 애드온이 등록한 키맵 항목이 아니라
'사용자 키맵'의 같은 항목을 찾아서(find_user_kmi) 그 항목을 그린다.
"""

import bpy

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


_KEY_NAMES = {
    'LEFTMOUSE': "LMB", 'RIGHTMOUSE': "RMB", 'MIDDLEMOUSE': "MMB",
    'WHEELUPMOUSE': "휠↑", 'WHEELDOWNMOUSE': "휠↓",
}


def key_text(kmi):
    """키맵 항목의 단축키 표시: 'Ctrl+Alt+LMB', 'Alt+휠↑', 'Alt+LMB 더블클릭', 키가 없으면 '키 없음'."""
    if kmi.type == 'NONE':
        return "키 없음"
    mods = [n for n, on in (("Ctrl", kmi.ctrl), ("Shift", kmi.shift), ("Alt", kmi.alt), ("OS", kmi.oskey)) if on]
    text = "+".join(mods + [_KEY_NAMES.get(kmi.type, kmi.type)])
    if kmi.value == 'DOUBLE_CLICK':
        text += " 더블클릭"
    return text


class MLS_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    def draw(self, context):
        box = self.layout.box()
        box.label(text="기능 / 단축키", icon='EVENT_A')
        if not state.keymap_items:
            box.label(text="등록된 단축키가 없습니다")
            return

        for entry in state.keymap_items:
            kmi = find_user_kmi(entry)
            row = box.row(align=True)
            if kmi is None:
                row.label(text=entry['title'] + "  (키맵에서 찾을 수 없음)", icon='ERROR')
                continue
            row.prop(kmi, "active", text="")
            split = row.split(factor=0.68, align=True)
            left = split.row(align=True)
            left.active = kmi.active          # 꺼진 기능은 흐리게
            text = entry['title']
            if entry['desc']:
                text += " (" + entry['desc'] + ")"
            left.label(text=text)
            right = split.row(align=True)
            right.active = kmi.active
            right.prop(kmi, "type", text="", full_event=True)
            if kmi.is_user_modified:
                right.operator("preferences.keyitem_restore", text="", icon='BACK').item_id = kmi.id


classes = (MLS_Preferences,)
