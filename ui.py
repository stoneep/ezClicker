"""
ui.py — 확장 단계 전환 UI와 기능 메뉴 (3D 뷰포트 헤더 버튼 / 우클릭 메뉴 / 팝업).

세 곳이 같은 본문(draw_panel_body)을 쓴다. 고칠 때는 여기 한 곳만 고치면 된다.
  - 헤더 버튼         : 팝오버 패널 (VIEW3D_PT_mirror_loop_select)
  - 우클릭 메뉴 맨 아래 : 서브메뉴 (MESH_MT_mirror_loop_level)
  - 팝업 단축키        : 같은 패널을 마우스 위치에 띄운다 (wm.call_panel, 열린 채로 여러 항목을 바꿀 수 있다)

본문 구성
  1) 확장 단계 버튼
  2) 옵션 : 각 기능 모듈(edge_ops, face_ops)이 내놓은 draw_settings(layout, settings) 를 순서대로 이어서 그린다.
            (어떤 모듈이 그릴지는 __init__.register() 가 settings_drawers 에 채워 넣는다.
             그래서 이 파일은 엣지/면 모듈을 직접 가져오지 않는다.)
  3) 기능 켜기/끄기 + 현재 단축키 : state.keymap_items 의 사용자 키맵 항목을 그린다.
  4) 도구 / 환경설정 바로가기
"""

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty

from . import prefs, state
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


def _redraw(context):
    if context.screen:
        for area in context.screen.areas:
            area.tag_redraw()


def view3d_override(context):
    """
    팝업/헤더에서 누른 버튼은 3D 뷰의 '작업 영역'이 아닌 곳의 컨텍스트로 실행된다.
    화면 기준(휠 확장의 위/아래)이나 마우스 위치를 쓰는 기능은 3D 뷰 영역으로 바꿔서 실행해야 한다.
    {window, area, region} 또는 못 찾으면 None.
    """
    screen = context.screen
    if screen is None:
        return None
    area = context.area if (context.area and context.area.type == 'VIEW_3D') else \
        next((a for a in screen.areas if a.type == 'VIEW_3D'), None)
    if area is None:
        return None
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    if region is None:
        return None
    return {'window': context.window, 'area': area, 'region': region}


def _call(context, idname, props, mode='INVOKE_DEFAULT'):
    """'mesh.xxx' 형태 idname 의 오퍼레이터를 3D 뷰 컨텍스트로 호출한다."""
    group, name = idname.split(".")
    func = getattr(getattr(bpy.ops, group), name)
    ov = view3d_override(context)
    if ov is None:
        return func(mode, **props)
    with context.temp_override(**ov):
        return func(mode, **props)


def _set_hint(context, text):
    """대기 중임을 알린다: 상태 표시줄 글자 + 십자 커서. text 가 None 이면 되돌린다."""
    try:
        if context.workspace:
            context.workspace.status_text_set(text)
        if context.window:
            if text is None:
                context.window.cursor_modal_restore()
            else:
                context.window.cursor_modal_set('CROSSHAIR')
    except (AttributeError, RuntimeError):
        pass


class MESH_OT_mirror_menu_run(bpy.types.Operator):
    """메뉴에서 기능을 클릭으로 실행: 클릭 기능은 '다음 클릭 한 번'을 기다리고, 그 외는 바로 실행한다"""
    bl_idname = "mesh.mirror_menu_run"
    bl_label = "Run Mirror Loop Feature"
    bl_options = {'INTERNAL'}

    index: IntProperty(default=0, min=0)

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    def execute(self, context):
        if self.index >= len(state.keymap_items):
            return {'CANCELLED'}
        entry = state.keymap_items[self.index]
        if entry['click']:
            if state.armed:
                _set_hint(context, None)
            state.arm(entry)
            _set_hint(context, "%s: 3D 뷰에서 클릭하면 실행됩니다 (Esc = 취소)" % entry['title'])
            _redraw(context)
            self.report({'INFO'}, "%s: 3D 뷰를 클릭하세요 (Esc = 취소)" % entry['title'])
            return {'FINISHED'}
        try:
            result = _call(context, entry['idname'], entry['props'])
        except RuntimeError:
            # 실행 조건이 안 맞는 경우. (예: 휠 확장은 루프를 선택한 직후에만 쓸 수 있다)
            self.report({'WARNING'}, "지금은 '%s'을(를) 실행할 수 없습니다. 휠 확장은 루프를 막 선택한 직후에만 됩니다."
                        % entry['title'])
            return {'CANCELLED'}
        _redraw(context)
        return {'FINISHED'} if 'FINISHED' in result else {'CANCELLED'}


class MESH_OT_mirror_armed_click(bpy.types.Operator):
    """(내부용) 메뉴에서 대기시킨 기능을 다음 왼쪽 클릭에 실행하거나, Esc 로 취소한다"""
    bl_idname = "mesh.mirror_armed_click"
    bl_label = "Mirror Loop Armed Click"
    bl_options = {'INTERNAL'}

    cancel: BoolProperty(default=False)

    @classmethod
    def poll(cls, context):
        # 대기 중이 아닐 때는 poll 이 실패해서 평소 클릭/Esc 가 그대로 동작한다.
        return state.armed is not None and context.mode == 'EDIT_MESH'

    def invoke(self, context, event):
        armed = state.armed
        state.disarm()
        _set_hint(context, None)
        _redraw(context)
        if self.cancel or armed is None:
            return {'FINISHED'}              # Esc 를 삼켜서 대기만 푼다
        try:
            result = _call(context, armed['idname'], armed['props'])
        except RuntimeError:
            self.report({'WARNING'}, "'%s'을(를) 실행할 수 없습니다 (확장 단계가 '끔'이거나 편집 모드가 아닙니다)" % armed['title'])
            return {'CANCELLED'}
        if 'FINISHED' in result:
            return {'FINISHED'}
        if 'PASS_THROUGH' in result:
            return {'CANCELLED', 'PASS_THROUGH'}   # 대상이 없는 곳이면 평소 클릭으로 넘긴다
        return {'CANCELLED'}


POPUP_IDNAME = "wm.call_panel"      # 팝업을 여는 키맵 항목의 오퍼레이터


def popup_entry():
    return next((e for e in state.keymap_items if e['idname'] == POPUP_IDNAME), None)


def draw_popup_key(layout, editable):
    """팝업을 여는 단축키. editable 이면 키 버튼을 눌러 바로 바꾼다. 다른 단축키와 겹치면 경고한다."""
    entry = popup_entry()
    if entry is None:
        return
    kmi = prefs.find_user_kmi(entry)
    row = layout.row(align=True)
    row.label(text="팝업 단축키")
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
    """
    기능별 한 줄:  [켜기/끄기 체크]  [실행 버튼]  단축키
      - 체크박스는 '단축키'를 켜고 끈다. 끄면 그 키는 Blender 기본 동작으로 돌아간다.
      - 실행 버튼은 단축키와 상관없이 메뉴에서 바로 쓴다.
        클릭 기능(▷)은 누른 뒤 3D 뷰를 한 번 클릭하면 실행되고, 나머지(▶)는 바로 실행된다.
      - 팝업을 여는 키는 따로 그린다.
    """
    for index, entry in enumerate(state.keymap_items):
        if entry['idname'] == POPUP_IDNAME:
            continue
        kmi = prefs.find_user_kmi(entry)
        row = layout.row(align=True)
        if kmi is None:
            row.label(text=entry['title'], icon='ERROR')
            continue
        split = row.split(factor=0.7, align=True)
        left = split.row(align=True)
        left.prop(kmi, "active", text="")
        armed = state.armed is not None and state.armed['title'] == entry['title']
        left.operator(MESH_OT_mirror_menu_run.bl_idname, text=entry['title'],
                      icon='RESTRICT_SELECT_OFF' if entry['click'] else 'PLAY',
                      depress=armed).index = index
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


def draw_header_button(self, context):
    """3D 뷰포트 헤더(편집 모드)에 현재 단계를 보여주는 버튼."""
    if context.mode != 'EDIT_MESH':
        return
    s = get_settings(context)
    if s is None:
        return
    armed = state.armed
    self.layout.popover(
        panel=VIEW3D_PT_mirror_loop_select.__name__,
        text=("클릭 대기: " + armed['title']) if armed else LEVEL_SHORT[s.level],
        icon='RESTRICT_SELECT_OFF' if armed else LEVEL_ICON[s.level])


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
    """Shift+G (Select Similar) 메뉴에 '모양' 항목을 붙인다. 선택한 면과 같은 모양의 면을 찾는다."""
    self.layout.operator("mesh.mirror_face_similar", text="모양 (Shape)")


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
    # 내부용(제목 없음 = 목록에 안 보임): 메뉴에서 대기시킨 기능을 다음 클릭에 실행 / Esc 로 취소.
    # 대기 중이 아니면 poll 이 실패해서 평소 클릭과 Esc 는 그대로 동작한다.
    (MESH_OT_mirror_armed_click.bl_idname, 'LEFTMOUSE', 'PRESS', {}, {'cancel': False}, None, None),
    (MESH_OT_mirror_armed_click.bl_idname, 'ESC', 'PRESS', {}, {'cancel': True}, None, None),
)

classes = (
    MESH_OT_mirror_loop_level,
    MESH_OT_mirror_menu_run,
    MESH_OT_mirror_armed_click,
    MESH_MT_mirror_loop_level,
    VIEW3D_PT_mirror_loop_select,
)
