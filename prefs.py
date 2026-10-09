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
from bpy.props import FloatProperty, FloatVectorProperty

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


# 겹침을 확인할 키맵. 3D 뷰 편집 모드에서 같은 키를 먼저 가져갈 수 있는 곳들이다.
_CONFLICT_KEYMAPS = ("Mesh", "3D View", "3D View Generic", "Window", "Screen", "Frames")


def find_conflicts(kmi, limit=3):
    """
    kmi 와 같은 키(종류, 값, 수식키)를 쓰는 다른 활성 단축키 이름 리스트 (최대 limit개).
    다른 애드온이나 Blender 기본 단축키와 겹치면 먼저 처리되는 쪽만 동작하므로, 바꿀 때 경고하는 데 쓴다.
    """
    if kmi is None or kmi.type == 'NONE':
        return []
    kc = bpy.context.window_manager.keyconfigs.user
    if kc is None:
        return []
    out = []
    for name in _CONFLICT_KEYMAPS:
        km = kc.keymaps.get(name)
        if km is None:
            continue
        for k in km.keymap_items:
            if k.id == kmi.id or not k.active or k.type != kmi.type or k.value != kmi.value:
                continue
            same_mods = (k.ctrl == kmi.ctrl and k.shift == kmi.shift and k.alt == kmi.alt
                         and k.oskey == kmi.oskey)
            if (same_mods or k.any) and k.key_modifier == kmi.key_modifier:
                out.append(k.name or k.idname)
                if len(out) >= limit:
                    return out
    return out


def get_prefs(context=None):
    """이 애드온의 환경설정. 아직 활성화 전이면 None."""
    context = context or bpy.context
    addon = context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


class MLS_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    anchor_color: FloatVectorProperty(
        name="시작 루프 표시 색",
        description="면 모드 사이 선택에서 첫 클릭으로 지정한 시작 루프(대기 중)를 3D 뷰에 겹쳐 그리는 색",
        subtype='COLOR', size=4, min=0.0, max=1.0, default=(1.0, 0.25, 0.1, 1.0),
    )
    anchor_width: FloatProperty(
        name="시작 루프 표시 두께",
        description="시작 루프 표시 선의 두께(픽셀)",
        default=4.0, min=1.0, max=20.0,
    )

    def draw(self, context):
        col = self.layout.column(align=True)
        col.prop(self, "anchor_color")
        col.prop(self, "anchor_width")
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
