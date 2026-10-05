"""
state.py — 모듈 사이에서 공유하는 런타임 상태.

파이썬은 `from .x import var` 로 가져온 변수에 다시 대입해도 원본이 바뀌지 않는다.
그래서 여러 모듈이 같이 읽고 쓰는 값은 이 모듈의 속성(state.wheel, state.anchor)으로만 다룬다.

  wheel  : 마지막 Alt+클릭 루프 선택의 휠 확장 상태.
           None 이면 휠 오퍼레이터의 poll 이 실패해서 휠은 Blender 기본 동작으로 넘어간다.
  anchor : 마지막으로 선택한 루프(시작 루프).
           Ctrl+Alt+클릭(사이 선택)이 이걸 기준으로 사이를 채운다.
  keymap_items : __init__.register() 가 등록한 단축키 목록. 환경설정(prefs)이 이걸 보고 그린다.
           각 항목은 {'idname', 'kmi', 'km_name', 'title', 'desc'} 딕셔너리.
"""

wheel = None
anchor = None
keymap_items = []


def reset_wheel():
    global wheel
    wheel = None


def set_wheel(st):
    global wheel
    wheel = st


def set_anchor(ob_name, seed_index, counts, selected=True, loop=None):
    """selected: 시작 루프가 선택된 상태로 남았는지. 면 모드에서 첫 클릭처럼 아무것도 선택하지 않고
    '대기'만 하는 경우는 False. (선택이 비었을 때 앵커를 버릴지 정하는 데 쓴다)
    loop: 시작 루프의 엣지 인덱스 리스트. 대기 중일 때 3D 뷰에 색 선으로 표시하는 데 쓴다. (overlay.py)"""
    global anchor
    anchor = {'ob': ob_name, 'seed': seed_index, 'counts': counts, 'selected': selected,
              'loop': sorted(loop) if loop else None}


def reset_all():
    global wheel, anchor
    wheel = None
    anchor = None
