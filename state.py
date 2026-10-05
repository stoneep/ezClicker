"""
state.py — 모듈 사이에서 공유하는 런타임 상태.

파이썬은 `from .x import var` 로 가져온 변수에 다시 대입해도 원본이 바뀌지 않는다.
그래서 여러 모듈이 같이 읽고 쓰는 값은 이 모듈의 속성(state.wheel, state.anchor)으로만 다룬다.

  wheel  : 마지막 Alt+클릭 루프 선택의 휠 확장 상태.
           None 이면 휠 오퍼레이터의 poll 이 실패해서 휠은 Blender 기본 동작으로 넘어간다.
  anchor : 마지막으로 선택한 루프(시작 루프).
           Ctrl+Alt+클릭(사이 선택)이 이걸 기준으로 사이를 채운다.
"""

wheel = None
anchor = None


def reset_wheel():
    global wheel
    wheel = None


def set_wheel(st):
    global wheel
    wheel = st


def set_anchor(ob_name, seed_index, counts):
    global anchor
    anchor = {'ob': ob_name, 'seed': seed_index, 'counts': counts}


def reset_all():
    global wheel, anchor
    wheel = None
    anchor = None
