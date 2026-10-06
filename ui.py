"""
ui.py — 확장 단계 전환 UI와 기능 메뉴 (3D 뷰포트 헤더 버튼 / 우클릭 메뉴 / 팝업).

세 곳이 같은 본문(draw_panel_body)을 쓴다. 고칠 때는 여기 한 곳만 고치면 된다.
  - 헤더 버튼         : 팝오버 패널 (VIEW3D_PT_mirror_loop_select)
  - 우클릭 메뉴 맨 아래 : 서브메뉴 (MESH_MT_mirror_loop_level)
  - 팝업 단축키        : 같은 패널을 마우스 위치에 띄운다 (wm.call_panel, 열린 채로 여러 항목을 바꿀 수 있다)
또 하나, 사이드바(N)의 'Mirror Loop' 탭(VIEW3D_PT_mirror_loop_adjust)은 마지막 루프 선택의 폭/길이/링을 +/- 로 고치는
고정 패널이다. 면 모드에서는 Blender 기본 Alt+클릭(면 루프 선택)으로 고른 면 줄의 폭/길이를 조절한다. 팝업과 달리 마우스가 벗어나도, 휠을 돌려도 닫히지 않는다. (열고 닫기: mesh.mirror_loop_panel, 임시 키 Alt+1)

본문 구성
  1) 확장 단계 버튼
  2) 옵션 : 각 기능 모듈(edge_ops, face_ops)이 내놓은 draw_settings(layout, settings) 를 순서대로 이어서 그린다.
            (어떤 모듈이 그릴지는 __init__.register() 가 settings_drawers 에 채워 넣는다.
             그래서 이 파일은 엣지/면 모듈을 직접 가져오지 않는다.)
  3) 기능 켜기/끄기 + 현재 단축키 : state.keymap_items 의 사용자 키맵 항목을 그린다.
  4) 도구 / 환경설정 바로가기
"""

import bpy
from bpy.props import EnumProperty

from . import prefs, state
from .common import adjust_mode, adjust_valid, face_only_mode, similar_edge_valid, similar_valid
from .settings import LEVEL_ICON, LEVEL_ITEMS, LEVEL_SHORT, get_settings


# __init__.register() 가 채우고 unregister() 가 비운다.
settings_drawers = []


class MESH_OT_mirror_loop_level(bpy.types.Operator):
    """Mirror Loop Select 의 확장 단계를 바꾼다 (끔 / 1단계 / 2단계 / 다음 단계로 순환)"""
    bl_idname = "mesh.mirror_loop_level"
    bl_label = "Mirror Loop Level"

    level: EnumProperty(
        name="Level",
        items=LEVEL_ITEMS + (
            ('CYCLE', "다음 단계로", "끔 → 1단계 → 2단계 순서로 돌린다", 'FILE_REFRESH', 3),
        ),
        default='CYCLE',
    )

    def execute(self, context):
        s = get_settings(context)
        if s is None:
            return {'CANCELLED'}
        if self.level == 'CYCLE':
            order = [item[0] for item in LEVEL_ITEMS]
            s.level = order[(order.index(s.level) + 1) % len(order)]
        else:
            s.level = self.level
        self.report({'INFO'}, LEVEL_SHORT[s.level])
        if context.screen:
            for area in context.screen.areas:
                area.tag_redraw()
        return {'FINISHED'}


POPUP_IDNAME = "wm.call_panel"      # 팝업을 여는 키맵 항목의 오퍼레이터
PANEL_IDNAME = "mesh.mirror_loop_panel"      # 고정 패널(사이드바 탭)을 여닫는 키맵 항목의 오퍼레이터
ADJUST_IDNAME = "mesh.mirror_loop_adjust"    # 고정 패널 숫자칸/버튼이 부르는 오퍼레이터 (엣지/버텍스 모드)
FACE_ADJUST_IDNAME = "mesh.mirror_face_adjust"    # 같은 숫자칸이 면 모드에서 부르는 오퍼레이터
KEY_ROW_IDNAMES = (POPUP_IDNAME, PANEL_IDNAME)     # 기능 목록 대신 위쪽 '키 입력 행'으로 따로 그리는 항목


def popup_entry(idname=POPUP_IDNAME):
    return next((e for e in state.keymap_items if e['idname'] == idname), None)


def draw_popup_key(layout, editable, idname=POPUP_IDNAME, label="팝업 단축키"):
    """팝업(또는 고정 패널)을 여는 단축키. editable 이면 키 버튼을 눌러 바로 바꾼다. 다른 단축키와 겹치면 경고한다."""
    entry = popup_entry(idname)
    if entry is None:
        return
    kmi = prefs.find_user_kmi(entry)
    row = layout.row(align=True)
    row.label(text=label)
    if kmi is None:
        row.label(text="키맵에서 찾을 수 없음", icon='ERROR')
        return
    if editable:
        row.prop(kmi, "type", text="", full_event=True)
        if kmi.is_user_modified:
            row.operator("preferences.keyitem_restore", text="", icon='BACK').item_id = kmi.id
    else:
        row.label(text=prefs.key_text(kmi))
    conflicts = prefs.find_conflicts(kmi)
    if conflicts:
        layout.label(text="겹침: " + ", ".join(conflicts), icon='ERROR')


def draw_features(layout):
    """기능별 켜기/끄기 체크박스 + 현재 단축키. (바꾼 키도 반영된다.) 팝업 키는 따로 그린다."""
    for entry in state.keymap_items:
        if entry['idname'] in KEY_ROW_IDNAMES:
            continue
        kmi = prefs.find_user_kmi(entry)
        row = layout.row(align=True)
        if kmi is None:
            row.label(text=entry['title'], icon='ERROR')
            continue
        split = row.split(factor=0.66, align=True)
        split.prop(kmi, "active", text=entry['title'])
        key = split.row(align=True)
        key.active = kmi.active
        key.alignment = 'RIGHT'
        key.label(text=prefs.key_text(kmi))


def draw_panel_body(layout, s, editable=True):
    """
    메뉴, 패널, 팝업이 같이 쓰는 본문.
    editable: 팝업 단축키를 키 버튼으로 바로 바꿀 수 있게 할지.
              (우클릭 서브메뉴는 키 입력을 받는 버튼이 어울리지 않아 글자로만 보여준다.)
    """
    draw_popup_key(layout, editable)
    draw_popup_key(layout, editable, PANEL_IDNAME, "고정 패널 단축키")
    layout.separator()

    layout.label(text="확장 단계")
    for ident, name, _desc, icon, _num in LEVEL_ITEMS:
        op = layout.operator(MESH_OT_mirror_loop_level.bl_idname, text=name, icon=icon,
                             depress=(s.level == ident))
        op.level = ident

    if settings_drawers:
        layout.separator()
        layout.label(text="옵션")
        for draw in settings_drawers:
            draw(layout, s)

    if len(state.keymap_items) > 1:
        layout.separator()
        layout.label(text="기능 켜기/끄기 · 단축키")
        draw_features(layout)

    layout.separator()
    layout.operator("mesh.mirror_face_similar", text="같은 모양 면 선택 (선택한 면 기준)", icon='FACESEL')
    layout.operator("preferences.addon_show", text="환경설정 (단축키 변경)", icon='PREFERENCES').module = __package__


# ---------------------------------------------------------------------------
# 고정 패널: 사이드바(N) > Mirror Loop 탭
# ---------------------------------------------------------------------------

def draw_adjust(layout, context):
    """고정 패널 본문. state.adjust (마지막 루프 선택)의 값을 보여주고 +/- 로 바꾼다."""
    entry = popup_entry(PANEL_IDNAME)
    kmi = prefs.find_user_kmi(entry) if entry else None

    valid = adjust_valid(context)
    mode = adjust_mode()
    if face_only_mode(context) and not (valid and mode == 'FACE'):
        layout.label(text="Alt+클릭으로 면 루프를 선택하세요", icon='INFO')
        layout.label(text="(Blender 기본 면 루프 선택)")
        layout.label(text="고른 면 줄의 폭·길이를")
        layout.label(text="이 패널에서 조절할 수 있습니다")
    elif not valid:
        layout.label(text="Alt+클릭으로 루프를 선택하세요", icon='INFO')
        layout.label(text="(엣지·버텍스 모드에서 동작)")
        layout.label(text="선택을 바꾸면 이 패널은 쉬었다가")
        layout.label(text="다음 루프 선택부터 다시 동작합니다")
    elif mode == 'FACE':
        # 면 모드: 기본 면 루프 선택으로 고른 줄. 폭 = 양옆에 붙일 나란한 줄 수, 길이 = 줄 안에서 남길 면 수.
        adj = state.adjust
        s = get_settings(context)
        layout.label(text="면 루프 (Blender 기본 선택 기준)", icon='FACESEL')
        layout.prop(s, "side_reference")
        col = layout.column(align=True)
        col.prop(s, "adjust_up", text="폭: 시계 방향 (줄 수)")
        col.prop(s, "adjust_down", text="폭: 반시계 방향 (줄 수)")
        col.prop(s, "adjust_length", text=f"길이: 면 수 (전체 {adj['total']})")
        layout.operator(FACE_ADJUST_IDNAME, text="기본 선택으로 초기화", icon='LOOP_BACK').target = 'RESET'
    else:
        adj = state.adjust
        p = adj['params']
        ring = p['use_ring']

        layout.operator(ADJUST_IDNAME, text="링 (Ring)", icon='MOD_ARRAY', depress=ring).target = 'RING'

        # 숫자칸: 클릭 드래그, 좌우 화살표, 더블클릭 입력이 모두 된다. (값은 state.adjust 와 항상 일치)
        s = get_settings(context)
        layout.prop(s, "side_reference")
        col = layout.column(align=True)
        col.enabled = not ring        # 링은 폭/길이를 쓰지 않는다
        col.prop(s, "adjust_up")
        col.prop(s, "adjust_down")
        total = adj['total']
        col.prop(s, "adjust_length", text=f"길이: 엣지 수 (전체 {total})")
        col.operator(ADJUST_IDNAME, text="폭·길이 초기화", icon='LOOP_BACK').target = 'RESET'

    layout.separator()
    if kmi is not None:
        row = layout.row(align=True)
        row.label(text="열기/닫기")
        row.label(text=prefs.key_text(kmi))
        layout.prop(kmi, "type", text="", full_event=True)
        conflicts = prefs.find_conflicts(kmi)
        if conflicts:
            layout.label(text="겹침: " + ", ".join(conflicts), icon='ERROR')


class VIEW3D_PT_mirror_loop_adjust(bpy.types.Panel):
    bl_label = "Mirror Loop"
    bl_idname = "VIEW3D_PT_mirror_loop_adjust"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Mirror Loop"

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    def draw(self, context):
        draw_adjust(self.layout, context)


class VIEW3D_PT_mirror_loop_adjust_options(bpy.types.Panel):
    """루프를 따라가는 규칙. (예전에는 Alt+클릭 직후 왼쪽 아래 '마지막 작업' 패널에 있던 옵션)
    모든 루프 선택이 같은 값을 쓰므로 다른 작업의 옵션과 섞이지 않는다."""
    bl_label = "루프 따라가기 옵션"
    bl_idname = "VIEW3D_PT_mirror_loop_adjust_options"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Mirror Loop"
    bl_parent_id = "VIEW3D_PT_mirror_loop_adjust"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    def draw(self, context):
        s = get_settings(context)
        if s is None:
            return
        col = self.layout.column(align=True)
        col.prop(s, "max_turn_angle")
        col.prop(s, "use_dihedral")
        self.layout.label(text="바꾸면 방금 고른 루프를 다시 계산합니다")


class MESH_MT_mirror_loop_level(bpy.types.Menu):
    bl_label = "Mirror Loop Select"
    bl_idname = "MESH_MT_mirror_loop_level"

    def draw(self, context):
        s = get_settings(context)
        if s is not None:
            draw_panel_body(self.layout, s, editable=False)


class VIEW3D_PT_mirror_loop_select(bpy.types.Panel):
    bl_label = "Mirror Loop Select"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'

    def draw(self, context):
        s = get_settings(context)
        if s is not None:
            draw_panel_body(self.layout.column(align=True), s)


class VIEW3D_PT_mirror_loop_similar(bpy.types.Panel):
    """같은 모양 면 선택: 기어, 나사 머리처럼 평평한 같은 모양의 면을 한 번에 고른다.
    (예전에는 실행 직후 왼쪽 아래 '마지막 작업' 패널에 있던 옵션. 이제 여기서 바꾸면 바로 다시 찾는다.)"""
    bl_label = "같은 모양 면 선택"
    bl_idname = "VIEW3D_PT_mirror_loop_similar"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Mirror Loop"

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    def draw(self, context):
        s = get_settings(context)
        if s is None:
            return
        layout = self.layout
        layout.operator("mesh.mirror_face_similar", text="선택한 면과 같은 모양 찾기", icon='FACESEL')
        info = state.similar
        if info is not None and similar_valid(context):
            layout.label(text="같은 모양 %d곳 (면 %d개)" % (info['regions'], info['faces']), icon='CHECKMARK')
        else:
            layout.label(text="면을 선택하거나 Alt+더블클릭하세요")
        layout.prop(s, "similar_mode", text="")
        col = layout.column(align=True)
        col.prop(s, "similar_extend")
        col.prop(s, "similar_scale_invariant")
        if s.similar_mode == 'PATCH':
            # 경계 엣지 루프 방식: 덩어리를 끊는 각도와 곡선 허용 오차
            col = layout.column(align=True)
            col.prop(s, "similar_patch_angle")
            col.prop(s, "similar_curve_tolerance")
        else:
            col.prop(s, "similar_use_island")
            col = layout.column(align=True)
            col.prop(s, "similar_length_tolerance")
            col.prop(s, "similar_angle_tolerance")
            col.prop(s, "similar_flat_angle")
        layout.label(text="옵션을 바꾸면 같은 기준 면으로 다시 찾습니다")


class VIEW3D_PT_mirror_loop_similar_edge(bpy.types.Panel):
    """같은 모양 엣지 루프: 톱니바퀴 림처럼 뾰족한 윤곽의 엣지 루프를 다른 곳에서 찾는다. (베벨, 버텍스 수, 회전, 크기가 달라도)"""
    bl_label = "같은 모양 엣지 루프"
    bl_idname = "VIEW3D_PT_mirror_loop_similar_edge"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Mirror Loop"

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    def draw(self, context):
        s = get_settings(context)
        if s is None:
            return
        layout = self.layout
        if face_only_mode(context):
            layout.label(text="엣지(또는 버텍스) 모드에서 쓸 수 있습니다", icon='INFO')
            return
        layout.operator("mesh.mirror_edge_similar", text="선택한 루프와 같은 모양 찾기", icon='EDGESEL')
        info = state.similar_edge
        if info is not None and similar_edge_valid(context):
            layout.label(text="같은 모양 루프 %d개" % info['loops'], icon='CHECKMARK')
        else:
            layout.label(text="Alt+클릭으로 루프를 고른 뒤 누르세요")
        col = layout.column(align=True)
        col.prop(s, "edge_similar_extend")
        col.prop(s, "edge_similar_scale_invariant")
        col = layout.column(align=True)
        col.prop(s, "edge_similar_shape_tol")
        col.prop(s, "edge_similar_size_tol")
        layout.label(text="옵션을 바꾸면 같은 루프로 다시 찾습니다")


def draw_header_button(self, context):
    """3D 뷰포트 헤더(편집 모드)에 현재 단계를 보여주는 버튼."""
    if context.mode != 'EDIT_MESH':
        return
    s = get_settings(context)
    if s is None:
        return
    self.layout.popover(
        panel=VIEW3D_PT_mirror_loop_select.__name__,
        text=LEVEL_SHORT[s.level], icon=LEVEL_ICON[s.level])


def draw_context_menu(self, context):
    """편집 모드 우클릭 메뉴 맨 아래에 기능 서브메뉴를 붙인다. (현재 확장 단계가 제목에 보인다)"""
    s = get_settings(context)
    if s is None:
        return
    layout = self.layout
    layout.separator()
    layout.menu(MESH_MT_mirror_loop_level.bl_idname,
                text=LEVEL_SHORT[s.level], icon=LEVEL_ICON[s.level])


def draw_select_similar(self, context):
    """Shift+G (Select Similar) 메뉴에 '모양' 항목을 붙인다. 면 모드면 같은 모양의 면, 엣지/버텍스 모드면 같은 모양의 엣지 루프."""
    if face_only_mode(context):
        self.layout.operator("mesh.mirror_face_similar", text="모양 (Shape)")
    else:
        self.layout.operator("mesh.mirror_edge_similar", text="모양 (Shape)")


def register_hooks():
    bpy.types.VIEW3D_HT_header.append(draw_header_button)
    bpy.types.VIEW3D_MT_edit_mesh_context_menu.append(draw_context_menu)
    bpy.types.VIEW3D_MT_edit_mesh_select_similar.append(draw_select_similar)


def unregister_hooks():
    bpy.types.VIEW3D_MT_edit_mesh_select_similar.remove(draw_select_similar)
    bpy.types.VIEW3D_MT_edit_mesh_context_menu.remove(draw_context_menu)
    bpy.types.VIEW3D_HT_header.remove(draw_header_button)


# (idname, 키, 값, 수식키, 오퍼레이터 속성, 환경설정에 보일 제목, 한 줄 설명)
KEYMAPS = (
    # 팝업: 헤더 버튼과 같은 패널을 마우스 위치에 띄운다. 다른 애드온과 키가 겹치면 환경설정에서 바꾼다.
    ("wm.call_panel", 'Q', 'PRESS', {'alt': True},
     {'name': 'VIEW3D_PT_mirror_loop_select', 'keep_open': True},
     "기능 메뉴 열기",
     "기능 켜기/끄기와 단축키를 한곳에 보여주는 팝업"),
    # 확장 단계 순환: 키는 비워 둔다. (환경설정의 단축키 목록에서 지정)
    (MESH_OT_mirror_loop_level.bl_idname, 'NONE', 'PRESS', {}, {'level': 'CYCLE'},
     "확장 단계 순환",
     "끔→1단계→2단계 전환, 기본 키 없음"),
)

classes = (
    MESH_OT_mirror_loop_level,
    MESH_MT_mirror_loop_level,
    VIEW3D_PT_mirror_loop_select,
    VIEW3D_PT_mirror_loop_adjust,
    VIEW3D_PT_mirror_loop_adjust_options,
    VIEW3D_PT_mirror_loop_similar,
    VIEW3D_PT_mirror_loop_similar_edge,
)
