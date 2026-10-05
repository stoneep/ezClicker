"""
Mirror Loop Select

파일 구성 (의존 방향: ops -> range -> core -> common)

  __init__.py    bl_info, 등록/해제, 키맵 조립
  state.py       모듈 간 공유 상태 (휠 확장 상태, 사이 선택 앵커)
  common.py      엣지/면 공통 유틸
  settings.py    확장 단계 설정 (PropertyGroup)
  ui.py          헤더 버튼, 우클릭 메뉴, 팝업 본문(기능 on/off + 현재 단축키), 단계 전환 오퍼레이터,
                 사이드바 고정 패널(폭/길이/링 +/- 조절)
  prefs.py       애드온 환경설정: 단축키 목록 (on/off, 키 변경, 한 줄 설명), 단축키 표시 문자열

  edge_core.py   [엣지] 루프 걷기, 다이헤드럴 보조 점수, 미러 반대편 루프 찾기
  edge_range.py  [엣지] 휠 확장/축소(오프셋 루프), 시작~끝 루프 사이 탐색
  edge_ops.py    [엣지] Alt+클릭 루프 선택, Alt/Ctrl+휠 확장 오퍼레이터

  face_core.py   [면]   사각형 면 위상 헬퍼, 루프 사이의 면 모으기
  face_shape.py  [면]   평평한 면 영역의 모양 비교 (같은 모양 찾기)
  overlay.py     면 모드 사이 선택의 시작 루프(대기 중) 임시 색 표시
  face_ops.py    [면]   Ctrl+Alt+클릭 사이 전부 / Shift+Alt+클릭 사이 루프만 / Alt+더블클릭 같은 모양 면 선택 오퍼레이터

새 기능을 추가할 때
  1) edge_* 또는 face_* 쪽에 오퍼레이터를 만들고, 모듈 맨 아래에
     classes (필수), KEYMAPS (키를 줄 때), draw_settings (패널에 옵션이 필요할 때) 를 내놓는다.
     - KEYMAPS 항목: (idname, 키, 값, {수식키}, {오퍼레이터 속성}, 환경설정에 보일 제목, 한 줄 설명)
       (제목이 None 이면 내부용 키맵이라 목록에는 보이지 않고 등록만 된다)
       (같은 idname 이면 오퍼레이터 속성 조합이 서로 달라야 환경설정이 항목을 구분할 수 있다)
     - draw_settings(layout, settings): 패널/메뉴에 옵션을 그린다. 옵션 값은 settings.MLS_Settings 에 둔다.
  2) 아래 MODULES 에 그 모듈을 넣는다. (등록·키맵·패널 연결은 자동)
"""

bl_info = {
    "name": "Mirror Loop Select",
    "author": "류우",
    "version": (0, 11, 0),
    "blender": (4, 0, 0),
    "location": "3D Viewport > Edit Mode > Alt + 클릭 = 루프 선택 / Ctrl + Alt + 클릭 = 시작 루프와 클릭한 루프 사이 전부 선택(버텍스·엣지·면) / Shift + Alt + 클릭 = 사이의 루프만 선택 / Alt + 더블클릭 = 같은 모양의 면 전부 선택(Shift+G > 모양) / Ctrl + Shift + Alt + 클릭 = 루프 선택 추가·해제 / (루프 선택 후) Alt + 휠 = 위·아래 동시 확장, Ctrl + 휠 = 한 방향 확장 / Alt + 1 = 사이드바 Mirror Loop 고정 패널 열기·닫기(폭·길이·링 조절) / 헤더 또는 우클릭 메뉴 = 확장 단계(끔·1단계·2단계) 전환",
    "description": "극점/삼각형에서 멈추지 않고 루프를 끝까지 선택 + 미러 축에서 끊긴 반대편 루프까지 선택 + Alt+휠로 위/아래 루프 확장/축소 + 루프와 루프 사이 전부(사이의 면까지) 선택 + 확장 단계 전환",
    "category": "Mesh",
}

# 애드온을 껐다 켜거나 Reload Scripts 를 할 때, 하위 모듈도 다시 읽도록 한다.
# (순서는 의존 방향을 따른다: 아래쪽 모듈이 먼저)
if "bpy" in locals():
    import importlib
    from . import (state, common, settings, face_core, face_shape, edge_core, edge_range,
                   edge_ops, face_ops, prefs, ui, overlay)
    for _m in (state, common, settings, face_core, face_shape, edge_core, edge_range,
               edge_ops, face_ops, prefs, ui, overlay):
        importlib.reload(_m)
else:
    from . import (state, common, settings, face_core, face_shape, edge_core, edge_range,  # noqa: F401
                   edge_ops, face_ops, prefs, ui, overlay)

import bpy
from bpy.props import PointerProperty


# 클래스 / 키맵 / 패널 설정을 내놓는 모듈. 등록은 이 순서대로 한다.
MODULES = (settings, edge_ops, face_ops, prefs, ui)

classes = tuple(c for m in MODULES for c in m.classes)
addon_keymaps = []


def register():
    for c in classes:
        bpy.utils.register_class(c)

    bpy.types.WindowManager.mls_settings = PointerProperty(type=settings.MLS_Settings)

    # 각 기능 모듈의 설정 UI 를 패널/메뉴에 이어 붙인다.
    ui.settings_drawers[:] = [m.draw_settings for m in MODULES if hasattr(m, "draw_settings")]
    ui.register_hooks()
    overlay.register()

    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name='Mesh', space_type='EMPTY')
        for m in MODULES:
            for idname, key, value, modifiers, props, title, desc in getattr(m, "KEYMAPS", ()):
                kmi = km.keymap_items.new(idname, key, value, **modifiers)
                for name, val in props.items():
                    setattr(kmi.properties, name, val)
                addon_keymaps.append((km, kmi))
                if title is None:        # 내부용 키맵(Esc 취소 등)은 환경설정/메뉴의 단축키 목록에 보이지 않는다
                    continue
                state.keymap_items.append({
                    'idname': idname, 'kmi': kmi, 'km_name': km.name,
                    'title': title, 'desc': desc,
                })


def unregister():
    state.reset_all()
    state.keymap_items.clear()

    overlay.unregister()
    ui.unregister_hooks()
    ui.settings_drawers.clear()

    for km, kmi in addon_keymaps:
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()

    del bpy.types.WindowManager.mls_settings
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
