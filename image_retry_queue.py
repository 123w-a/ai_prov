# image_retry_queue.py：失败图自动补图队列（L4 兜底）
# ---------------------------------------------------------------------------
# 背景：实时出图依赖外部网络（搜图/视觉审核/通义万相生图），网络抖动期（如
# ConnectionReset 10054 / 审核超时）整轮失败后，卡片只剩文字说明。本模块后台
# 周期扫描会话库中『有菜名但无图』的记录，用同一套 find_recipe_image 瀑布补图
# （AI 生图缓存前置 → 真图 → 生图兜底），成功即回写，用户刷新/进入会话即可见。
#
# 预算控制：每轮最多 3 张；失败菜名 1 小时冷却（防反复打外部接口）；
# 刚写过的会话文件（2 分钟内）跳过，避免与实时生成链路抢写。
# ---------------------------------------------------------------------------
import json
import threading
import time

from sessions_store import SESSIONS_DIR, update_answer_image_by_dish
from agent_tools import find_recipe_image

RETRY_INTERVAL_S = 600      # 每轮扫描间隔 10 分钟
FIRST_ROUND_DELAY_S = 60    # 启动后 1 分钟做首轮（给实时链路让路）
MAX_ITEMS_PER_ROUND = 3     # 每轮最多补 3 张，防止打爆外部接口
RECENT_WRITE_GRACE_S = 120  # 会话文件 2 分钟内被写过则跳过
COOLDOWN_S = 3600           # 失败菜名 1 小时内不重试

_cooldown_lock = threading.Lock()
_cooldown = {}  # dish -> 上次尝试时间戳


def scan_missing_images(max_items: int = MAX_ITEMS_PER_ROUND):
    """扫描会话库，返回 [(sid, record_id, dish)]：明确请求过配图但仍缺图的记录。"""
    now = time.time()
    targets = []
    if not SESSIONS_DIR.exists():
        return targets
    files = sorted(SESSIONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for fp in files:
        try:
            if now - fp.stat().st_mtime < RECENT_WRITE_GRACE_S:
                continue  # 正在实时生成的会话不碰
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        sid = data.get("session_id")
        if not sid:
            continue
        for rec in data.get("messages") or []:
            raw = rec.get("answer")
            if not isinstance(raw, str) or not raw or raw.startswith("__pending__"):
                continue  # 未完成回答（实时链路会自己兜底）
            try:
                ans = json.loads(raw)
            except Exception:
                continue
            if not isinstance(ans, dict):
                continue
            if not ans.get("image_requested"):
                continue  # 只补明确开过配图的轮次，避免普通问答被后台悄悄补图
            for recipe in ans.get("recipes") or []:
                dish = str(recipe.get("name") or "").strip()
                if not dish or recipe.get("image_url"):
                    continue
                with _cooldown_lock:
                    if now - _cooldown.get(dish, 0) < COOLDOWN_S:
                        continue
                targets.append((sid, rec.get("id"), dish))
                if len(targets) >= max_items:
                    return targets
    return targets


def backfill_once(max_items: int = MAX_ITEMS_PER_ROUND) -> int:
    """扫一轮并补图，返回成功张数。任何单菜失败不影响其余。"""
    filled = 0
    for sid, record_id, dish in scan_missing_images(max_items):
        try:
            url, source = find_recipe_image(dish)
        except Exception as exc:
            print(f"[image_retry] {dish} 补图异常：{exc}", flush=True)
            continue
        if not url:
            with _cooldown_lock:
                _cooldown[dish] = time.time()  # 失败进冷却，1 小时内不打外部接口
            print(f"[image_retry] {dish} 暂无可靠图源，1 小时后再试", flush=True)
            continue
        with _cooldown_lock:
            _cooldown.pop(dish, None)  # 成功即清除，下次扫描自然因有图跳过
        ai = source == "ai"
        note = (
            "AI 生成示意图（后台自动补图）；如与实际成品有出入，以文字描述为准"
            if ai
            else "后台自动补图：联网检索成品图"
        )
        if update_answer_image_by_dish(sid, record_id, dish, url, ai, note):
            filled += 1
            print(f"[image_retry] 已补图：{dish}（{'AI' if ai else '联网'}）", flush=True)
    return filled


def _loop():
    time.sleep(FIRST_ROUND_DELAY_S)
    while True:
        try:
            filled = backfill_once()
            print(f"[image_retry] 本轮补图 {filled} 张", flush=True)
        except Exception as exc:
            print(f"[image_retry] 轮次异常：{exc}", flush=True)
        time.sleep(RETRY_INTERVAL_S)


def start_retry_daemon():
    threading.Thread(target=_loop, name="image-retry", daemon=True).start()
