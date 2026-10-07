"""Object History - 오브젝트 단위 변경 기록 / 복구 (프로토타입 v0.1)

핵심 아이디어
- 기록은 Blender Undo 시스템 밖(.blend 옆의 <파일명>.history 폴더)에 저장한다.
- 오브젝트별로 지오메트리 / 모디파이어 / 트랜스폼 / 머티리얼의 해시를 만들고,
  해시가 바뀐 경우에만 스냅샷을 남긴다 (선택, 정점 클릭 같은 단순 조작은 기록되지 않음).
- 모디파이어 개수가 줄어든 변경(Apply 또는 삭제)이 감지되면, 직전 스냅샷을 마일스톤으로 고정한다.
- 복구는 스냅샷을 새 오브젝트로 가져오는 방식이라 현재 작업을 건드리지 않는다.
"""

bl_info = {
    "name": "Object History (prototype)",
    "author": "류우",
    "version": (0, 1, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > ObjHistory",
    "description": "오브젝트 단위 변경 기록과 복구 (Undo 시스템 밖에 저장)",
    "category": "Object",
}

import hashlib
import json
import tempfile
import time
import uuid
from pathlib import Path

import bpy
import numpy as np
from bpy.app.handlers import persistent

# ----------------------------------------------------------------------------
# 설정
# ----------------------------------------------------------------------------
DEBOUNCE = 0.8      # 마지막 변경 후 이 시간(초) 동안 조용하면 기록
MAX_KEEP = 30       # 마일스톤이 아닌 스냅샷을 오브젝트당 최대 몇 개 유지할지
UID_KEY = "oh_uid"  # 오브젝트에 붙는 고유 ID 커스텀 프로퍼티
SHOW_MAX = 25       # 패널에 보여줄 최대 항목 수

# 해시에서 제외할 모디파이어 프로퍼티 (UI 상태일 뿐 결과에 영향이 없음)
_SKIP_PROPS = {"rna_type", "show_expanded", "is_active", "is_override_data"}

# ----------------------------------------------------------------------------
# 런타임 상태 (Undo 영향을 받지 않는 파이썬 메모리)
# ----------------------------------------------------------------------------
_cache = {}          # uid -> entries(list)
_pending = set()     # 변경 감지된 오브젝트 이름
_last_update = 0.0
_busy = False        # 복구 중에는 변경 감지를 무시


# ----------------------------------------------------------------------------
# 저장소
# ----------------------------------------------------------------------------
def history_root():
    if bpy.data.filepath:
        p = Path(bpy.data.filepath)
        return p.with_name(p.stem + ".history")
    return Path(tempfile.gettempdir()) / "objhist_unsaved"


def _obj_dir(uid):
    return history_root() / uid


def load_entries(uid):
    if uid in _cache:
        return _cache[uid]
    data = []
    p = _obj_dir(uid) / "index.json"
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as ex:
            print("[ObjHistory] index 읽기 실패:", ex)
    _cache[uid] = data
    return data


def save_entries(uid):
    d = _obj_dir(uid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.json").write_text(
        json.dumps(_cache[uid], ensure_ascii=False, indent=1), encoding="utf-8"
    )


def ensure_uid(ob):
    uid = ob.get(UID_KEY)
    if not uid:
        uid = uuid.uuid4().hex[:12]
        ob[UID_KEY] = uid
    return uid


def prune(uid):
    entries = _cache[uid]
    normal = [e for e in entries if not e.get("milestone")]
    extra = len(normal) - MAX_KEEP
    if extra <= 0:
        return
    drop_ids = {e["id"] for e in normal[:extra]}
    for e in entries:
        if e["id"] in drop_ids:
            try:
                (_obj_dir(uid) / e["file"]).unlink()
            except FileNotFoundError:
                pass
    entries[:] = [e for e in entries if e["id"] not in drop_ids]


# ----------------------------------------------------------------------------
# 해시 (선택 / 숨김 같은 상태는 포함하지 않음)
# ----------------------------------------------------------------------------
def _idprop(v):
    if hasattr(v, "to_list"):
        return v.to_list()
    if hasattr(v, "to_dict"):
        return v.to_dict()
    return getattr(v, "name", v)


def _mod_signature(m):
    parts = [m.type]
    for p in m.bl_rna.properties:
        pid = p.identifier
        if pid in _SKIP_PROPS or p.type == "COLLECTION":
            continue
        try:
            v = getattr(m, pid)
            if p.type == "POINTER":
                v = getattr(v, "name", None) if v is not None else None
            elif p.type in {"FLOAT", "INT", "BOOLEAN"} and getattr(p, "array_length", 0) > 0:
                v = tuple(v)
            parts.append(f"{pid}={v!r}")
        except Exception:
            pass
    # 지오메트리 노드처럼 RNA 밖에 있는 입력값(ID 프로퍼티)
    try:
        for k in m.keys():
            parts.append(f"[{k}]={_idprop(m[k])!r}")
    except Exception:
        pass
    return "|".join(parts)


def compute_hash(ob):
    h = hashlib.sha1()
    me = ob.data

    co = np.empty(len(me.vertices) * 3, dtype=np.float32)
    me.vertices.foreach_get("co", co)
    h.update(co.tobytes())

    ed = np.empty(len(me.edges) * 2, dtype=np.int32)
    me.edges.foreach_get("vertices", ed)
    h.update(ed.tobytes())

    lp = np.empty(len(me.loops), dtype=np.int32)
    me.loops.foreach_get("vertex_index", lp)
    h.update(lp.tobytes())

    h.update(str(len(me.polygons)).encode())
    try:
        pt = np.empty(len(me.polygons), dtype=np.int32)
        me.polygons.foreach_get("loop_total", pt)
        h.update(pt.tobytes())
    except Exception:
        pass

    for m in ob.modifiers:
        h.update(m.name.encode("utf-8"))
        h.update(_mod_signature(m).encode("utf-8"))

    h.update(repr([round(x, 6) for x in ob.location]).encode())
    h.update(repr([round(x, 6) for x in ob.rotation_euler]).encode())
    h.update(repr([round(x, 6) for x in ob.scale]).encode())
    h.update(repr([s.material.name if s.material else "" for s in ob.material_slots]).encode())
    return h.hexdigest()


# ----------------------------------------------------------------------------
# 스냅샷
# ----------------------------------------------------------------------------
def take_snapshot(ob, label="자동", milestone=False):
    """변경이 있을 때만 스냅샷을 저장한다. 저장했으면 entry, 아니면 None."""
    uid = ensure_uid(ob)
    entries = load_entries(uid)
    h = compute_hash(ob)

    # 이미 기록된 상태(Undo로 돌아간 경우 포함)는 중복 저장하지 않는다.
    for e in entries:
        if e["hash"] == h:
            if milestone and not e.get("milestone"):
                e["milestone"] = True
                e["label"] = label
                save_entries(uid)
            return None

    # 모디파이어가 줄었다면 Apply(또는 삭제)로 보고 직전 상태를 고정한다.
    mods = len(ob.modifiers)
    if entries and entries[-1].get("mods", 0) > mods and not entries[-1].get("milestone"):
        entries[-1]["milestone"] = True
        entries[-1]["label"] = "Apply/삭제 직전"

    next_id = (max(e["id"] for e in entries) + 1) if entries else 1
    fname = f"{next_id:04d}.blend"
    d = _obj_dir(uid)
    d.mkdir(parents=True, exist_ok=True)
    try:
        bpy.data.libraries.write(str(d / fname), {ob}, fake_user=True, compress=True)
    except Exception as ex:
        print("[ObjHistory] 스냅샷 저장 실패:", ex)
        return None

    entry = {
        "id": next_id,
        "file": fname,
        "time": time.time(),
        "hash": h,
        "label": label,
        "milestone": bool(milestone),
        "obj_name": ob.name,
        "verts": len(ob.data.vertices),
        "mods": mods,
    }
    entries.append(entry)
    prune(uid)
    save_entries(uid)
    return entry


# ----------------------------------------------------------------------------
# 변경 감지: 핸들러는 이름만 모으고, 실제 저장은 디바운스된 타이머가 한다.
# ----------------------------------------------------------------------------
def _process_pending():
    if time.time() - _last_update < DEBOUNCE:
        return 0.3
    keep = set()
    for name in list(_pending):
        ob = bpy.data.objects.get(name)
        if ob is None or ob.type != "MESH":
            continue
        if ob.mode == "EDIT":  # 편집 모드 중에는 메시 데이터가 갱신되지 않으므로 나중에
            keep.add(name)
            continue
        try:
            take_snapshot(ob)
        except Exception as ex:
            print("[ObjHistory] 오류:", ex)
    _pending.clear()
    _pending.update(keep)
    return 1.0 if _pending else None


@persistent
def _on_depsgraph(scene, depsgraph):
    global _last_update
    if _busy:
        return
    touched = False
    for u in depsgraph.updates:
        idb = u.id
        if isinstance(idb, bpy.types.Object):
            ob = idb.original
            if ob.type == "MESH" and (u.is_updated_geometry or u.is_updated_transform):
                _pending.add(ob.name)
                touched = True
        elif isinstance(idb, bpy.types.Mesh):
            mname = idb.original.name
            for ob in bpy.data.objects:
                if ob.type == "MESH" and ob.data and ob.data.name == mname:
                    _pending.add(ob.name)
                    touched = True
    if touched:
        _last_update = time.time()
        if not bpy.app.timers.is_registered(_process_pending):
            bpy.app.timers.register(_process_pending, first_interval=0.5)


@persistent
def _on_load(_dummy):
    _cache.clear()
    _pending.clear()


# ----------------------------------------------------------------------------
# 오퍼레이터
# ----------------------------------------------------------------------------
class OBJHIST_OT_snapshot(bpy.types.Operator):
    bl_idname = "objhist.snapshot"
    bl_label = "지금 저장"
    bl_description = "현재 상태를 마일스톤으로 기록합니다 (자동 삭제되지 않음)"

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.type == "MESH"

    def execute(self, context):
        ob = context.active_object
        if ob.mode != "OBJECT":
            self.report({"WARNING"}, "오브젝트 모드에서 사용하세요")
            return {"CANCELLED"}
        entry = take_snapshot(ob, label="수동 저장", milestone=True)
        self.report({"INFO"}, "저장했습니다" if entry else "이미 기록된 상태입니다 (마일스톤으로 표시됨)")
        return {"FINISHED"}


class OBJHIST_OT_restore(bpy.types.Operator):
    bl_idname = "objhist.restore"
    bl_label = "복구"
    bl_description = "이 시점의 오브젝트를 새 오브젝트로 가져옵니다 (현재 작업은 그대로 유지)"
    bl_options = {"REGISTER", "UNDO"}

    uid: bpy.props.StringProperty()
    entry_id: bpy.props.IntProperty()

    def execute(self, context):
        global _busy
        entries = load_entries(self.uid)
        entry = next((e for e in entries if e["id"] == self.entry_id), None)
        if entry is None:
            self.report({"ERROR"}, "기록을 찾을 수 없습니다")
            return {"CANCELLED"}
        path = _obj_dir(self.uid) / entry["file"]
        if not path.exists():
            self.report({"ERROR"}, "스냅샷 파일이 없습니다")
            return {"CANCELLED"}

        _busy = True
        try:
            with bpy.data.libraries.load(str(path)) as (src, dst):
                names = list(src.objects)
                if names:
                    pick = entry.get("obj_name") if entry.get("obj_name") in names else names[0]
                    dst.objects = [pick]
            if not dst.objects:
                self.report({"ERROR"}, "스냅샷에서 오브젝트를 불러오지 못했습니다")
                return {"CANCELLED"}

            ob = dst.objects[0]
            ob.name = f"{entry['obj_name']}_r{entry['id']:04d}"
            if UID_KEY in ob.keys():
                del ob[UID_KEY]  # 복구본은 새 기록으로 시작
            try:
                ob.data.use_fake_user = False
            except Exception:
                pass

            context.collection.objects.link(ob)
            for o in context.selected_objects:
                o.select_set(False)
            ob.select_set(True)
            context.view_layer.objects.active = ob
        finally:
            _busy = False

        self.report({"INFO"}, f"복구했습니다: {ob.name}")
        return {"FINISHED"}


# ----------------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------------
class OBJHIST_PT_panel(bpy.types.Panel):
    bl_label = "Object History"
    bl_idname = "OBJHIST_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "ObjHistory"

    def draw(self, context):
        layout = self.layout
        ob = context.active_object
        if ob is None or ob.type != "MESH":
            layout.label(text="메시 오브젝트를 선택하세요")
            return

        layout.operator("objhist.snapshot", icon="BOOKMARKS")
        if ob.mode == "EDIT":
            layout.label(text="편집 모드를 나가면 기록됩니다", icon="INFO")

        uid = ob.get(UID_KEY)
        entries = load_entries(uid) if uid else []
        if not entries:
            layout.label(text="아직 기록이 없습니다")
            return

        layout.label(text=f"기록 {len(entries)}개  ·  {history_root().name}")
        box = layout.box()
        for e in reversed(entries[-SHOW_MAX:]):
            row = box.row(align=True)
            ts = time.strftime("%H:%M:%S", time.localtime(e["time"]))
            icon = "SOLO_ON" if e.get("milestone") else "DOT"
            row.label(text=f"{ts}  {e['label']}  ({e['verts']}v/{e['mods']}m)", icon=icon)
            op = row.operator("objhist.restore", text="", icon="LOOP_BACK")
            op.uid = uid
            op.entry_id = e["id"]


# ----------------------------------------------------------------------------
# 등록
# ----------------------------------------------------------------------------
classes = (OBJHIST_OT_snapshot, OBJHIST_OT_restore, OBJHIST_PT_panel)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    if _on_depsgraph not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)


def unregister():
    if _on_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph)
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    if bpy.app.timers.is_registered(_process_pending):
        bpy.app.timers.unregister(_process_pending)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)


if __name__ == "__main__":
    register()
