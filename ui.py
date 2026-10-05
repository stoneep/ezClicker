"""
ui.py — 확장 단계 전환 UI (3D 뷰포트 헤더 버튼 / 우클릭 메뉴 / 팝오버 패널).

패널은 '확장 단계 버튼' 아래에, 각 기능 모듈(edge_ops, face_ops)이 내놓은
draw_settings(layout, settings) 를 순서대로 이어서 그린다.
(어떤 모듈이 그릴지는 __init__.register() 가 settings_drawers 에 채워 넣는다.
 그래서 이 파일은 엣지/면 모듈을 직접 가져오지 않는다.)
"""

import bpy
from bpy.props import EnumProperty

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


def draw_panel_body(layout, s):
    """확장 단계 버튼 + 각 기능 모듈의 설정. 메뉴와 패널이 같이 쓴다."""
    for ident, name, _desc, icon, _num in LEVEL_ITEMS:
        op = layout.operator(MESH_OT_mirror_loop_level.bl_idname, text=name, icon=icon,
                             depress=(s.level == ident))
        op.level = ident
    if settings_drawers:
        layout.separator()
        for draw in settings_drawers:
            draw(layout, s)


class MESH_MT_mirror_loop_level(bpy.types.Menu):
    bl_label = "루프 확장 단계"
    bl_idname = "MESH_MT_mirror_loop_level"

    def draw(self, context):
        s = get_settings(context)
        if s is not None:
            draw_panel_body(self.layout, s)


class VIEW3D_PT_mirror_loop_select(bpy.types.Panel):
    bl_label = "Mirror Loop Select"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'

    def draw(self, context):
        s = get_settings(context)
        if s is not None:
            draw_panel_body(self.layout.column(align=True), s)


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
    """편집 모드 우클릭 메뉴 맨 아래에 단계 서브메뉴를 붙인다."""
    s = get_settings(context)
    if s is None:
        return
    layout = self.layout
    layout.separator()
    layout.menu(MESH_MT_mirror_loop_level.bl_idname,
                text=LEVEL_SHORT[s.level], icon=LEVEL_ICON[s.level])


def register_hooks():
    bpy.types.VIEW3D_HT_header.append(draw_header_button)
    bpy.types.VIEW3D_MT_edit_mesh_context_menu.append(draw_context_menu)


def unregister_hooks():
    bpy.types.VIEW3D_MT_edit_mesh_context_menu.remove(draw_context_menu)
    bpy.types.VIEW3D_HT_header.remove(draw_header_button)


# 확장 단계 순환: 키는 비워 둔다. (Preferences > Keymap > Mesh 에서 지정)
KEYMAPS = (
    (MESH_OT_mirror_loop_level.bl_idname, 'NONE', 'PRESS', {}, {'level': 'CYCLE'}),
)

classes = (
    MESH_OT_mirror_loop_level,
    MESH_MT_mirror_loop_level,
    VIEW3D_PT_mirror_loop_select,
)
