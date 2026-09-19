# chat_route.py：只负责"AI 对话"这一类接口（图片/文本 -> 大模型 -> 存库）
# 当前只保留流式分支（SSE 打字机 + 整包卡片 JSON），非流式分支已删除。
from fastapi import APIRouter, File, Form, UploadFile, HTTPException
from fastapi.responses import StreamingResponse
import json  # 把 token / structuring / answer / finish 打包成 SSE 事件
import os
import queue
import re
import threading

from main import (
    build_human_message,
    stream_agent,
    image_bytes_to_oss_url,
)
from agent_graph import (
    failover_llms,
    is_candidate_revision_request,
    is_execute_plan_request,
    is_specific_dish_request,
    parse_candidate_index,
)
from agent_tools import find_recipe_image
from model_name import is_provider_failure
from upload_guard import validate_image_upload
from sessions_store import append_message
import time
from datetime import datetime
import re

router = APIRouter()  # 分文件写接口的小路由

# 图片 MIME 白名单：挡掉非图片和可能的恶意文件
ALLOWED_MIME = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
_CANCELLED_IMAGE_TURNS: dict[str, float] = {}
_CANCELLED_IMAGE_KEEP_TEXT: dict[str, float] = {}
_CANCELLED_IMAGE_LOCK = threading.Lock()
_RECIPE_IMAGE_CACHE: dict[tuple[str, bool], tuple[str | None, str, float]] = {}
_RECIPE_IMAGE_CACHE_LOCK = threading.Lock()
_CANCELLED_IMAGE_TTL_S = 6 * 60 * 60
_MAX_CANCELLED_IMAGE_KEYS = 5000
_SEARCH_IMAGE_CACHE_TTL = 60 * 60 * 6
_AI_IMAGE_CACHE_TTL = 60 * 60 * 24 * 7
_MAX_RECIPE_IMAGE_CACHE_ENTRIES = 500
_ACTIVE_TURN_RECORDS: dict[str, tuple[int, float]] = {}
_ACTIVE_TURN_RECORDS_LOCK = threading.Lock()
_ACTIVE_TURN_RECORD_TTL_S = 6 * 60 * 60
_MAX_ACTIVE_TURN_RECORDS = 5000
_IMAGE_THREAD_POLL_TIMEOUT_S = 5.0
_IMAGE_THREAD_MAX_WAIT_S = float(os.getenv("CHEF_IMAGE_THREAD_MAX_WAIT_S", "180"))

# —— 并发护栏：同会话串行 + 全局 Agent 背压 ——
# 同 session_id 同时跑两轮，会以同一个 thread_id 同时写 LangGraph checkpoint，
# 状态会串写；前端按钮 disabled 只是 UI 约束，服务端必须自己兜住。
_TURNS_IN_FLIGHT: dict[str, float] = {}
_TURNS_IN_FLIGHT_LOCK = threading.Lock()
# 僵尸占用兜底：客户端硬断线时生成器 finally 可能不执行，超时后允许新轮次接管。
_TURN_IN_FLIGHT_TTL_S = 30 * 60
# 全局 Agent 并发上限：无界起线程会把内存/上游配额打满，这里只做背压不做排队。
_MAX_CONCURRENT_AGENT_TURNS = max(1, int(os.getenv("CHEF_MAX_CONCURRENT_TURNS", "4")))
_AGENT_TURN_SEMAPHORE = threading.BoundedSemaphore(_MAX_CONCURRENT_AGENT_TURNS)


def _try_begin_turn(session_id: str) -> str:
    """占用同会话轮次槽；成功返回空串，失败返回可直接展示给用户的原因。"""
    now = time.time()
    with _TURNS_IN_FLIGHT_LOCK:
        for key in [
            key for key, started in _TURNS_IN_FLIGHT.items()
            if now - started > _TURN_IN_FLIGHT_TTL_S
        ]:
            _TURNS_IN_FLIGHT.pop(key, None)
        if session_id in _TURNS_IN_FLIGHT:
            return "这个会话还有一轮没跑完，等它出结果再发下一句就好。"
        _TURNS_IN_FLIGHT[session_id] = now
    return ""


def _end_turn(session_id: str) -> None:
    with _TURNS_IN_FLIGHT_LOCK:
        _TURNS_IN_FLIGHT.pop(session_id, None)


def _acquire_agent_slot() -> bool:
    """非阻塞占用全局 Agent 槽位；acquire 阻塞会在 async def 里卡住事件循环。"""
    return _AGENT_TURN_SEMAPHORE.acquire(blocking=False)


def _release_agent_slot() -> None:
    try:
        _AGENT_TURN_SEMAPHORE.release()
    except ValueError:
        pass


def _notice_stream(text: str, session_id: str):
    """用 SSE 回一条可直接读的提示，避免前端只能弹一个看不懂的报错。"""
    def generator():
        yield f"data: {json.dumps({'token': text}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'finish': True, 'session_id': session_id, 'record_id': None}, ensure_ascii=False)}\n\n"
    return StreamingResponse(generator(), media_type="text/event-stream")


def _prune_cancel_state_locked(now: float | None = None) -> None:
    """清理过期取消标记，并给异常突发流量加上内存上限。"""
    now = time.monotonic() if now is None else now
    cutoff = now - _CANCELLED_IMAGE_TTL_S
    for state in (_CANCELLED_IMAGE_TURNS, _CANCELLED_IMAGE_KEEP_TEXT):
        for key, created_at in list(state.items()):
            if created_at < cutoff:
                state.pop(key, None)
        if len(state) > _MAX_CANCELLED_IMAGE_KEYS:
            oldest = sorted(state.items(), key=lambda item: item[1])
            for key, _ in oldest[:len(state) - _MAX_CANCELLED_IMAGE_KEYS]:
                state.pop(key, None)


def _prune_turn_records_locked(now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    cutoff = now - _ACTIVE_TURN_RECORD_TTL_S
    for key, (_, created_at) in list(_ACTIVE_TURN_RECORDS.items()):
        if created_at < cutoff:
            _ACTIVE_TURN_RECORDS.pop(key, None)
    if len(_ACTIVE_TURN_RECORDS) > _MAX_ACTIVE_TURN_RECORDS:
        oldest = sorted(_ACTIVE_TURN_RECORDS.items(), key=lambda item: item[1][1])
        for key, _ in oldest[:len(_ACTIVE_TURN_RECORDS) - _MAX_ACTIVE_TURN_RECORDS]:
            _ACTIVE_TURN_RECORDS.pop(key, None)


def _prune_recipe_image_cache_locked(now: float) -> None:
    for key, (_, _, cached_at) in list(_RECIPE_IMAGE_CACHE.items()):
        ttl = _AI_IMAGE_CACHE_TTL if key[1] else _SEARCH_IMAGE_CACHE_TTL
        if now - cached_at >= ttl:
            _RECIPE_IMAGE_CACHE.pop(key, None)
    if len(_RECIPE_IMAGE_CACHE) > _MAX_RECIPE_IMAGE_CACHE_ENTRIES:
        oldest = sorted(_RECIPE_IMAGE_CACHE.items(), key=lambda item: item[1][2])
        for key, _ in oldest[:len(_RECIPE_IMAGE_CACHE) - _MAX_RECIPE_IMAGE_CACHE_ENTRIES]:
            _RECIPE_IMAGE_CACHE.pop(key, None)


def _find_global_dish_asset(name: str):
    try:
        from dish_assets_store import find_dish_asset
        return find_dish_asset(name)
    except Exception:
        return None


def _save_global_dish_assets(answer_obj: dict, session_id: str) -> None:
    if not isinstance(answer_obj, dict):
        return
    try:
        from dish_assets_store import upsert_dish_asset
        for recipe in answer_obj.get("recipes") or []:
            if isinstance(recipe, dict) and str(recipe.get("name") or "").strip():
                upsert_dish_asset(recipe, session_id)
    except Exception:
        pass


def _global_asset_prompt(message: str) -> str:
    """把跨会话资产作为参考资料注入当前请求，不把旧会话结论当成当前结论。"""
    requested_name = _extract_requested_dish(message)
    asset = _find_global_dish_asset(requested_name) if requested_name else None
    if not asset:
        try:
            from dish_assets_store import find_dish_asset_in_text
            asset = find_dish_asset_in_text(message)
        except Exception:
            asset = None
    if not asset:
        return ""
    recipe = asset.get("recipe") or {}
    image_url = asset.get("image_url") or recipe.get("image_url")
    lines = [
        "【跨对话菜品资产参考】",
        f"系统曾保存过基础菜品「{asset.get('name') or recipe.get('name') or requested_name}」。",
        "这只是基础资料，不能继承来源对话的健康结论；必须根据本次对话的健康状态、口味和场景重新判断。",
    ]
    if image_url:
        lines.append(f"- 已有可复用配图：{image_url}")
    for field, label in (("intro", "基础介绍"), ("seasonings", "调味"), ("steps", "基础步骤")):
        value = recipe.get(field)
        if value:
            lines.append(f"- {label}：{json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value}")
    return "\n".join(lines)


def _image_cancel_key(session_id: str, turn_id: str | None = None) -> str:
    return f"{session_id}:{turn_id}" if turn_id else session_id


def _cancel_image_for_turn(
    session_id: str,
    turn_id: str | None = None,
    keep_text: bool = False,
) -> None:
    key = _image_cancel_key(session_id, turn_id)
    now = time.monotonic()
    with _CANCELLED_IMAGE_LOCK:
        _CANCELLED_IMAGE_TURNS[key] = now
        if keep_text:
            _CANCELLED_IMAGE_KEEP_TEXT[key] = now
        else:
            _CANCELLED_IMAGE_KEEP_TEXT.pop(key, None)
        _prune_cancel_state_locked(now)


def _clear_image_cancel(session_id: str, turn_id: str | None = None) -> None:
    key = _image_cancel_key(session_id, turn_id)
    with _CANCELLED_IMAGE_LOCK:
        _CANCELLED_IMAGE_TURNS.pop(key, None)
        _CANCELLED_IMAGE_KEEP_TEXT.pop(key, None)


def _clear_image_cancel_after_thread(session_id: str, turn_id: str | None, image_thread) -> None:
    """后台补图线程结束后再清理取消标记，避免取消与回写竞态。"""
    if image_thread is None or not image_thread.is_alive():
        _clear_image_cancel(session_id, turn_id)
        _forget_turn_record(session_id, turn_id)
        return

    def wait_and_clear():
        try:
            image_thread.join()
        finally:
            _clear_image_cancel(session_id, turn_id)
            _forget_turn_record(session_id, turn_id)

    threading.Thread(target=wait_and_clear, daemon=True).start()


def _clear_stale_image_cancel_for_new_turn(session_id: str, turn_id: str | None = None) -> None:
    """新一轮开始前清掉上一轮遗留的取消标记。

    兼容旧客户端不传 turn_id 的情况：同时移除会话级旧标记，避免它
    永久影响同一会话后续请求。
    """
    prefix = f"{session_id}:"
    with _CANCELLED_IMAGE_LOCK:
        for key in [
            key for key in _CANCELLED_IMAGE_TURNS
            if key == session_id or key.startswith(prefix)
        ]:
            _CANCELLED_IMAGE_TURNS.pop(key, None)
        _prune_cancel_state_locked()
        for key in [
            key for key in _CANCELLED_IMAGE_KEEP_TEXT
            if key == session_id or key.startswith(prefix)
        ]:
            _CANCELLED_IMAGE_KEEP_TEXT.pop(key, None)


def _is_image_cancelled(session_id: str, turn_id: str | None = None) -> bool:
    with _CANCELLED_IMAGE_LOCK:
        _prune_cancel_state_locked()
        return (
            _image_cancel_key(session_id, turn_id) in _CANCELLED_IMAGE_TURNS
            or session_id in _CANCELLED_IMAGE_TURNS
        )


def _should_keep_text_on_cancel(session_id: str, turn_id: str | None = None) -> bool:
    with _CANCELLED_IMAGE_LOCK:
        _prune_cancel_state_locked()
        return (
            _image_cancel_key(session_id, turn_id) in _CANCELLED_IMAGE_KEEP_TEXT
            or session_id in _CANCELLED_IMAGE_KEEP_TEXT
        )


def _active_turn_key(session_id: str, turn_id: str | None) -> str | None:
    return f"{session_id}:{turn_id}" if turn_id else None


def _remember_turn_record(session_id: str, turn_id: str | None, record_id: int | None) -> None:
    key = _active_turn_key(session_id, turn_id)
    if key and record_id is not None:
        now = time.monotonic()
        with _ACTIVE_TURN_RECORDS_LOCK:
            _ACTIVE_TURN_RECORDS[key] = (record_id, now)
            _prune_turn_records_locked(now)


def _get_turn_record(session_id: str, turn_id: str | None) -> int | None:
    key = _active_turn_key(session_id, turn_id)
    if not key:
        return None
    with _ACTIVE_TURN_RECORDS_LOCK:
        _prune_turn_records_locked()
        record = _ACTIVE_TURN_RECORDS.get(key)
        if isinstance(record, tuple):
            return record[0]
        return record


def _forget_turn_record(session_id: str, turn_id: str | None) -> None:
    key = _active_turn_key(session_id, turn_id)
    if key:
        with _ACTIVE_TURN_RECORDS_LOCK:
            _ACTIVE_TURN_RECORDS.pop(key, None)


def _apply_mode_prompt(message: str, mode: str) -> str:
    """保留旧参数兼容；当前统一交给自然语言分流。"""
    return message


def _wants_image(message: str, want_image: str | None) -> bool:
    if str(want_image or "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    text = str(message or "")
    strong_phrases = (
        "配图", "配张图", "补图", "换图", "生成图片", "生成一张图", "来张图",
        "发图", "发张图", "发图片", "发个图", "出图", "出个图", "图给我",
        "带图", "带图片", "有图", "有图片",
        "我要图片", "要图片", "要张图片", "要一张图片",
        "看看图", "看看图片", "看图片", "看图", "看一下图", "看一下图片", "看个图",
        "给我看图", "给我看看", "让我看看", "想看图片", "想看图", "图片欣赏",
        "成品图", "成品照", "实拍图", "示意图", "效果图", "样图", "参考图",
        "想看看", "长什么样", "什么样子", "啥样", "样式", "外观", "照片", "实拍",
        "换张图", "换一张", "再来一张", "另一张",
    )
    return any(phrase in text for phrase in strong_phrases)


def _find_recipe_image_cached(recipe_name: str, allow_ai_fallback: bool):
    """调用层图片缓存：不改 agent_tools，也避免同菜反复搜图/生图。"""
    name = str(recipe_name or "").strip()
    if not name:
        return None, "none"
    key = (name.lower(), bool(allow_ai_fallback))
    now = time.time()
    ttl = _AI_IMAGE_CACHE_TTL if allow_ai_fallback else _SEARCH_IMAGE_CACHE_TTL
    with _RECIPE_IMAGE_CACHE_LOCK:
        _prune_recipe_image_cache_locked(now)
        cached = _RECIPE_IMAGE_CACHE.get(key)
        if cached and now - cached[2] < ttl:
            return cached[0], cached[1]
    image_url, source = find_recipe_image(name, allow_ai_fallback=allow_ai_fallback)
    with _RECIPE_IMAGE_CACHE_LOCK:
        _RECIPE_IMAGE_CACHE[key] = (image_url, source, now)
        _prune_recipe_image_cache_locked(now)
    return image_url, source


def _is_pure_execute_plan_request(message: str) -> bool:
    """严格确认句才可复用上一轮成品，附加审计/份量等新请求必须走 Agent。"""
    text = str(message or "").strip()
    if not is_execute_plan_request(text):
        return False
    for phrase in ("就按这个方案执行", "按照这个方案执行", "按这个方案执行"):
        if phrase in text:
            remainder = text.replace(phrase, "", 1)
            break
    else:
        return False
    remainder = re.sub(r"[\s，。！？、,.!?;；：:\"'“”‘’（）()]+", "", remainder)
    return remainder in ("", "吧", "了", "吧了")



def _reusable_confirmation_answer(session_id: str, message: str) -> dict | None:
    """确认上一道已有配图的菜时，直接复用原答案，避免重新命名和换图。

    只有上一轮确实已经有图才走这个短路；没有图时仍交给原有确认流程补图，
    这样不会改变首次确认菜品的行为。
    """
    if is_execute_plan_request(message) and not _is_pure_execute_plan_request(message):
        return None
    try:
        from sessions_store import find_recent_recipe_for_image

        requested_name = _extract_requested_dish(message)
        target = find_recent_recipe_for_image(session_id, requested_name)
        if not target and requested_name:
            target = find_recent_recipe_for_image(session_id, None)
        if not target:
            return None

        answer = target.get("answer")
        if not isinstance(answer, dict):
            return None
        recipes = answer.get("recipes") or []
        recipe_index = int(target.get("recipe_index") or 0)
        if recipe_index < 0 or recipe_index >= len(recipes):
            return None
        recipe = recipes[recipe_index]
        if not isinstance(recipe, dict):
            return None

        image_url = recipe.get("image_url")
        if not image_url and recipe_index == 0:
            image_url = answer.get("image_url")
        if not image_url:
            return None

        # 通过 JSON 深拷贝，避免修改会话读取结果中的嵌套对象。
        reused = json.loads(json.dumps(answer, ensure_ascii=False))
        reused["opening"] = (
            f"好，就按「{target['dish_name']}」来。我沿用上一轮的方案和配图，"
            "下面给你最终做法。"
        )
        reused["image_requested"] = True
        reused_recipe = (reused.get("recipes") or [])[recipe_index]
        reused_recipe["image_url"] = image_url
        reused_recipe["image_ai_generated"] = bool(
            recipe.get("image_ai_generated")
            or (answer.get("image_ai_generated") if recipe_index == 0 else False)
        )
        if recipe_index == 0:
            reused["image_url"] = image_url
            reused["image_ai_generated"] = reused_recipe["image_ai_generated"]
            reused["image_note"] = (
                reused_recipe.get("image_note")
                or answer.get("image_note")
                or reused.get("image_note")
                or ""
            )
        return reused
    except Exception:
        return None


def _is_recipe_change_request(message: str) -> bool:
    """用户在追问里要求换一道/改做法时，应走完整对话，不当作给上一道补图。"""
    text = str(message or "")
    broad_change_words = ("没胃口", "不想吃这个", "不想吃了", "换一道", "换一个", "换别的", "没食欲")
    if any(word in text for word in broad_change_words):
        return True
    change_words = ("换成", "改成", "做成", "换做", "改做", "改为", "变成")
    recipe_words = ("面", "汤", "菜", "饭", "粥", "粉", "肉", "鱼", "鸡", "牛", "虾", "豆腐")
    return any(word in text for word in change_words) and any(word in text for word in recipe_words)


def _is_image_revision_request(message: str, want_image: str | None) -> bool:
    if not _wants_image(message, None):
        return False
    if _is_recipe_change_request(message):
        return False
    text = str(message or "")
    contextual_refs = ("上一道", "上一道菜", "刚才", "刚刚", "前面", "这道", "这道菜", "这个", "这种", "那种", "这份", "这个方案", "它", "上面", "上一份", "这张")
    revision_phrases = ("换图", "换张图", "换一张", "再来一张", "重新生成", "重新配", "重画", "不满意", "不好看", "另一张")
    return any(ref in text for ref in contextual_refs) or any(phrase in text for phrase in revision_phrases)


def _is_visual_dish_lookup_request(message: str, want_image: str | None) -> bool:
    """识别“某道菜长什么样/想看看”这类无历史菜谱也应直接出图的请求。"""
    if not _wants_image(message, None):
        return False
    if _is_recipe_change_request(message):
        return False
    text = str(message or "")
    visual_phrases = (
        "想看看", "长什么样", "什么样子", "啥样", "样式", "外观",
        "看看图", "看看图片", "看图片", "看图", "看一下图", "看一下图片", "看个图",
        "给我看图", "给我看看", "让我看看", "图片欣赏", "成品图", "成品照",
        "实拍图", "示意图", "效果图", "样图", "参考图", "照片", "实拍",
    )
    return any(phrase in text for phrase in visual_phrases) and bool(_extract_requested_dish(text))


def _looks_like_dining_request(message: str) -> bool:
    """只有菜谱/饮食/点餐类请求才允许进入配图链路。"""
    text = str(message or "").strip()
    if not text:
        return False
    markers = (
        "做饭", "做菜", "菜谱", "食谱", "菜品", "食材", "配方", "烹饪", "做法",
        "帮我做", "做道", "做个", "做一份", "做一下", "来道", "来个",
        "推荐", "吃", "饭", "餐", "早餐", "午餐", "晚餐", "夜宵", "外卖", "点餐", "食堂",
        "汤面", "面条", "米粉", "米线", "炒肉", "家常菜",
        "餐厅", "冰箱", "营养", "热量", "减脂", "控糖", "高血压", "糖尿病",
        "痛风", "尿酸", "健康饮食", "附近吃什么",
    )
    return any(marker in text for marker in markers)


def _is_restaurant_ordering_scene(text: str) -> bool:
    text = str(text or "").strip()
    if not text:
        return False
    restaurant_markers = (
        "餐厅", "饭店", "饭馆", "店里", "到店", "堂食", "外食", "外吃", "外出就餐",
        "出去吃", "出去吃饭", "在外吃", "在外面吃", "下馆子", "聚餐",
        "点餐", "点单", "菜单", "套餐", "档口", "食堂", "外卖", "附近",
    )
    restaurant_context = ("店", "餐厅", "饭店", "食堂", "外卖", "附近")
    signature_words = ("招牌", "推荐几道菜", "推荐几个菜", "点什么菜")
    cooking_markers = ("做法", "怎么做", "菜谱", "食谱", "烹饪", "开火", "下锅", "食材", "冰箱", "在家做", "自己做")
    if any(marker in text for marker in cooking_markers):
        return False
    return any(marker in text for marker in restaurant_markers) or (
        any(word in text for word in signature_words)
        and any(ctx in text for ctx in restaurant_context)
    )


# 只修高概率误判：以前裸匹配「上门」，于是「上门维修/上门取件」也会被当成私厨上门，
# 整轮被罐头文案接管（实测：「上周上门维修的师傅说我家冰箱该换了」）。
_HOME_SERVICE_STRONG = (
    "到家服务", "厨师到家", "私厨到家", "上门私厨", "私厨上门",
    "请厨师", "预约厨师", "请个厨师", "找个厨师", "上门做菜", "上门做饭",
)
# 「上门」只有和做饭语境同现才算私厨需求
_HOME_SERVICE_COOKING = (
    "做饭", "做菜", "烧菜", "下厨", "厨师", "私厨", "煮饭", "做顿饭", "做一桌", "上门服务",
)
# 这些语境里的「上门」是别的服务，明确排除
_NON_CATERING_UPSTREAM = (
    "维修", "安装", "取件", "送货", "快递", "拜访", "体检", "保修", "售后",
    "保洁", "清洗", "家政", "搬家", "测量", "拍照",
)


def _looks_like_home_service_request(text: str) -> bool:
    raw = str(text or "")
    if not raw:
        return False
    if any(marker in raw for marker in _HOME_SERVICE_STRONG):
        return True
    if "上门" not in raw:
        return False
    has_cooking = any(word in raw for word in _HOME_SERVICE_COOKING)
    if any(word in raw for word in _NON_CATERING_UPSTREAM) and not has_cooking:
        return False
    return has_cooking


def _classify_turn_intent(message: str) -> str:
    text = str(message or "").strip()
    if not text:
        return "other"
    if _looks_like_home_service_request(text):
        return "home_service"
    if _is_restaurant_ordering_scene(text):
        return "restaurant"
    if _is_recipe_change_request(text):
        return "change_one"
    if is_execute_plan_request(text):
        return "confirm_one"
    confirm_words = ("就做", "就吃", "来这个", "做这个", "吃这个", "定这个", "选这个", "就它", "就这道", "第一道", "第二道", "第三道")
    if any(word in text for word in confirm_words):
        return "confirm_one"
    followup_words = ("清淡", "少盐", "少油", "不要", "别放", "能不能", "可以吗", "适合吗", "热量", "钠", "糖", "脂肪")
    if any(word in text for word in followup_words) and not _looks_like_dining_request(text):
        return "followup"
    if _looks_like_dining_request(text) or is_specific_dish_request(text):
        return "recommend"
    return "other"


def _home_service_redirect_text() -> str:
    """说明上门私厨的能力边界，并把用户转到当前可用的饮食功能。"""
    return (
        "我理解你是想让别人直接帮你做好这顿饭。\n\n"
        "目前小膳管家还没有真实的私厨上门预约、派单、支付或上门履约功能，"
        "所以现在不能直接替你安排厨师上门，也不会假装已经进入预约流程。\n\n"
        "但我可以马上帮你换一种方式解决：\n\n"
        "1. 在家自己做：根据你的食材、口味和健康情况，给你一道能直接照做的菜谱。\n"
        "2. 推荐附近餐馆：结合位置、预算和健康要求，帮你筛选适合到店吃的餐馆和菜品。\n"
        "3. 帮你选外卖/看菜单：分析菜单里的高盐、高油、腌制品和不适合你的做法。\n"
        "4. 了解未来私厨服务：介绍以后选择私厨时需要确认的资质、价格、卫生和服务范围。\n\n"
        "你直接回复“1、2、3 或 4”，我就按对应方式继续。"
    )


def _is_recipe_selection_request(message: str) -> bool:
    """（历史判定，保留兼容）已不再作为配图门：配图只看「是否已选定一道菜」。"""
    text = str(message or "").strip()
    if any(marker in text for marker in ("帮我做", "做道", "做个", "做一份", "来道", "来个", "菜品", "菜谱", "食谱")):
        return True
    if any(marker in text for marker in ("推荐", "想吃")):
        return any(char in text for char in ("鸡", "鱼", "肉", "蛋", "虾", "豆腐", "面", "饭", "菜", "汤", "粥", "粉"))
    return False


def _resolve_picked_candidate(session_id: str, message: str) -> str | None:
    """把「就第2个」解析成候选清单里的具体菜名（没有候选锚点/越界时返回 None）。

    路由层只拿得到消息字符串，拿不到对话上下文，所以序号锚点从会话记录里的候选字段读；
    Agent 层读的是 checkpoint 里的同一份候选 payload，两层指向同一个锚点。
    """
    index = parse_candidate_index(message)
    if not index:
        return None
    try:
        from sessions_store import find_recent_candidates

        recent = find_recent_candidates(session_id)
    except Exception:
        return None
    if not recent:
        return None
    names = recent.get("candidates") or []
    if index > len(names):
        return None
    return names[index - 1]


def _count_reasoned_candidate_lines(text) -> int:
    """数正文里有几行是「序号. 菜名 —— 理由」形态的候选行。

    与 `agent_graph._extract_candidate_names` 同源、但更严，用来在**没有历史快照**
    时区分「这轮是候选清单」和「这轮是卡片（正文是带编号步骤的菜谱）」。

    判别依据（实测三组真实样本都成立）：
      - 候选行：`2. 青椒炒鸡丝 —— 鸡胸肉低脂，青椒切细丝…`，菜名是**纯菜名**；
      - 步骤行：`2. **上浆（决定嫩不嫩）**：鸡丝 + 半个蛋清 + 5ml料酒…`，
        菜名位置是**加粗的做法动作短语**（`**切丝**` / `**番茄去皮**`）。

    注意：不能靠「理由里含数字+单位」判步骤 —— 候选理由里也常写
    `—— 高蛋白低脂，10分钟出锅`、`—— 清淡好消化，5分钟`。真正的分水岭是
    **菜名位置是不是做法动作**（加粗包裹 + 动词开头），候选菜名永远是食材名词。
    """
    count = 0
    for line in str(text or "").splitlines():
        line = line.strip()
        match = re.match(r"^(?:[-*•]\s*)?(\d{1,2})\s*[.、)）．:：]\s*(.+)$", line)
        if not match:
            continue
        body = match.group(2).strip()
        parts = re.split(r"\s*(?:——|—|--|–|：|:|\||｜)\s*", body, maxsplit=1)
        if len(parts) < 2:
            continue
        raw_head = parts[0].strip()
        # 步骤行特征：菜名位置被 ** 包裹（加粗的步骤标题）
        if raw_head.startswith("**") or raw_head.endswith("**"):
            continue
        head = raw_head.strip("*`「」『』\"'“”").strip()
        if not (2 <= len(head) <= 14):
            continue
        # 菜名位置以做法动词开头 → 是步骤标题，不是菜名
        if re.match(r"^(切|放|加|下|倒|淋|撒|盖|转|关|开|取|把|用|将|煮|炒|煎|蒸|炖|焖|腌|盛|打|调|备)", head):
            continue
        count += 1
    return count


def _register_candidate_anchor(session_id: str, record_id, message: str, answer: str) -> bool:
    """泛推荐正文落库后补登记候选锚点（结构事件偶发缺失时的确定性兜底）。

    只认本轮确实是泛推荐，并复用 Agent 层同一套候选解析与过敏原过滤；
    普通菜谱的编号步骤不会被登记成候选。任何异常都静默返回 False，
    不能影响正常聊天落库。

    ⚠️ 只靠 `_is_candidate_turn([单条消息])` 不够 —— 它看不到历史，会误判（实测 T2 复现）：
    用户说「选第 2 个，但不要放青椒，改成两人份」时，正文是一份带编号步骤的菜谱，
    `_extract_candidate_names` 会把「切丝 / 上浆 / 番茄去皮 / 炒番茄」当成候选菜名登记；
    而单消息快照判不出「选第 2 个」的序号意图（那需要 has_prior_candidates），
    只靠「青椒」这个食材词就放行了 → 卡片轮的步骤名污染候选锚点。

    两道额外闸门：
      1. **正文形态**：候选行必须「序号. 短菜名 + 散文式理由」，菜谱步骤行不带这种形态；
      2. **卡片轮排除**：上一轮已经出了卡片时，本轮是围绕卡片的确认/追问，
         绝不能再登记候选（与 agent_graph 的 `_has_delivered_card` 同口径）。
    """
    if not session_id or not record_id or not str(answer or "").strip():
        return False
    try:
        from langchain_core.messages import HumanMessage
        from agent_graph import (
            _extract_candidate_names,
            _filter_candidate_names,
            _is_candidate_turn,
        )
        from sessions_store import set_message_candidates

        if not _is_candidate_turn([HumanMessage(content=str(message or ""))]):
            return False
        # 闸门 2：上一轮已经交付卡片 → 本轮不是候选轮（纯文本形态无法分辨的必须靠这里）。
        if _prev_record_has_card(session_id, record_id):
            return False
        # 闸门 1：正文必须真的长成候选清单的样子。
        if _count_reasoned_candidate_lines(answer) < 2:
            return False
        names = _filter_candidate_names(_extract_candidate_names(answer))
        if len(names) < 2:
            return False
        return bool(set_message_candidates(session_id, record_id, names))
    except Exception:
        return False


def _prev_record_has_card(session_id: str, record_id) -> bool:
    """同一会话里，`record_id` 之前最近一条记录是否带图片/卡片（即已经交付了菜谱）。

    只看「有没有图」这一个信号：候选轮一定不配图（产品形态决定），
    卡片轮一定有图槽（即使补图失败，`image_url` 字段也被写过）。
    这样能确定性区分「上一轮是候选清单」和「上一轮是卡片」。
    """
    try:
        from sessions_store import _read_session
        data = _read_session(session_id)
        if not isinstance(data, dict):
            return False
        target = str(record_id)
        prev = None
        for item in data.get("messages") or []:
            if str(item.get("id")) == target:
                break
            prev = item
        if not prev:
            return False
        # 卡片轮的判据：正文里出现做法段落（食材/做法标题），或该轮挂过图。
        text = str(prev.get("answer") or "")
        if str(prev.get("image_url") or "") not in ("", "None", "null"):
            return True
        return ("**【做法】**" in text) or ("## " in text and "食材" in text)
    except Exception:
        return False


def _should_enable_image_pipeline(message: str, want_image: str | None) -> bool:
    """配图只在「已选定一道菜」时开启：确认一道菜 / 换一道菜 / 点名一道具体菜 / 用户明确要图。

    泛推荐轮（只给食材、让我推荐几道）走候选清单，不出卡片也就没有图可配，
    这里必须与 agent_graph 的 `_wants_recipe_images` 同源，否则会出现
    「后端开了配图开关、前端却没有卡片」的空转。"""
    if is_candidate_revision_request(message):
        return False
    if _wants_image(message, want_image) and not _is_image_revision_request(message, want_image):
        # “推荐几道菜，配张图”仍然属于候选阶段：先给编号清单，选定后再出卡片和图片。
        return is_specific_dish_request(message) or _is_recipe_change_request(message)
    intent = _classify_turn_intent(message)
    return intent in {"confirm_one", "change_one"} or (
        intent == "recommend" and is_specific_dish_request(message)
    )


def _is_standalone_image_request(message: str, want_image: str | None) -> bool:
    """只保留“已有图片不满意，换一张”这类后续请求。"""
    return _is_image_revision_request(message, want_image) or _is_visual_dish_lookup_request(message, want_image)


def _extract_requested_dish(message: str) -> str | None:
    raw = str(message or "")
    contextual_refs = ("上一道", "上一道菜", "刚才", "刚刚", "前面", "这道", "这道菜", "这个", "这种", "那种", "这份", "这个方案", "它", "上面", "上一份")
    if any(ref in raw for ref in contextual_refs):
        return None
    # ① 书名号/引号内的菜名优先：用户把菜名括起来就是在显式「点名」，
    #    这个信号比后面的动作词剥离更可靠。实测 E4
    #    「给我一道「柠檬香茅烤鲈鱼」的成品图，配一道没听过的菜。」：
    #    长句走完剥离后仍 >12 字 → 旧实现直接 return None → 路由层
    #    `find_recent_recipe_for_image(sid, None)` 兜底抓到**上一道菜**，
    #    于是回「「冬瓜豆腐汤」上一轮已经有配图了」，用户要的新菜图一张没出。
    #    `_is_specific_dish_request` 的正则明确排除「」『』，也认不出这种写法。
    quoted = re.search(r"[「『\"“]([^」』\"”]{2,14})[」』\"”]", raw)
    if quoted:
        name = _LEADING_QUANTIFIER_STRIP.sub("", quoted.group(1).strip())
        if name and not any(word in name for word in _REQUEST_SENTENCE_WORDS):
            return name[:40]
    text = re.sub(r"【[^】]+】", "", raw)
    text = re.sub(r"\[[^\]]+\]", "", text)
    # ② 括号里的补注也先摘掉（「番茄炒蛋（少油版）的图」→「番茄炒蛋的图」），
    #    否则括号内容会被当成菜名的一部分。
    text = re.sub(r"[（(][^）)]{0,12}[）)]", "", text)
    text = re.sub(r"(帮我|给我|我想|想要|想看看|可以|能不能|能否|麻烦|请|一下|看看|看下|看一看|展示|来展示|欣赏|来张|来一张|来份|来个)", "", text)
    action_phrases = (
        "重新生成一张图片", "重新生成一张图", "重新生成图片", "重新生成",
        "重新配张图片", "重新配张图", "重新配图", "重新配", "重画",
        "换一张图片", "换一张图", "换一张", "换张图片", "换张图", "换图",
        "再来一张图片", "再来一张图", "再来一张", "另一张图片", "另一张图", "另一张",
        "配张图片", "配张图", "配图片", "配图", "补张图片", "补张图", "补图",
        "生成一张图片", "生成一张图", "生成图片", "来张图片", "来张图", "来图片",
        "发张图片", "发张图", "发图片", "发个图", "发图", "出个图", "出图",
        "我要图片", "我要图", "要张图片", "要一张图片", "要图片", "要图",
        "图片欣赏", "成品图片", "成品图", "成品照",
        "实拍图片", "实拍图", "示意图片", "示意图", "效果图片", "效果图",
        "样图", "参考图片", "参考图", "图片", "照片", "实拍",
    )
    text = re.sub(
        "|".join(
            re.escape(phrase)
            for phrase in sorted(action_phrases, key=len, reverse=True)
        ),
        "",
        text,
    )
    # 再清一次复合动作，覆盖“配张图片”被局部替换后可能残留的首字/尾字。
    text = re.sub(
        r"(重新生成一张图|重新生成|重新配|重画|换一张|换张图|再来一张|另一张|"
        r"不满意|不好看|配图|配张图|补图|换图|生成图片|生成一张图|来张图|"
        r"发图|发张图|发图片|发个图|出图|出个图|图片欣赏|成品图|成品照|"
        r"实拍图|示意图|效果图|样图|参考图|图片|照片|实拍|图)",
        "",
        text,
    )
    text = re.sub(r"(给我看图|给我看看|让我看看|看一下图|看一下图片|看个图|长什么样|什么样子|啥样|什么样|样式|外观)", "", text)
    text = re.sub(r"[，。！？、,.!?：:\s]+", "", text).strip()
    text = text.strip("的")
    text = _LEADING_QUANTIFIER_STRIP.sub("", text)
    if not text or all(char in "片张图要" for char in text):
        return None
    # 需求句防护：只有「点一道菜 + 要图」才该走到这里。用户把多条件需求写成一段话时
    # （「我今晚想吃清淡低盐少油的晚餐…再给我一张对应的成品图」），上面的动作词剥离
    # 会把整段需求留在 text 里，再被截成 40 字当菜名去搜图 —— 实测会把整句用户消息
    # 当成菜名，出一张「菜名叫用户原话」的空卡片，且聊天区正文全空。
    # 判据用「菜名」的结构特征，不依赖具体菜品词表：
    #   1) 长度：真菜名极少超过 12 字（含括号备注）；
    #   2) 转折/条件词：出现「但是 / 然后 / 如果 / 请 / 帮我 / 我想 / 之后 / 等」说明是需求句；
    #   3) 多个逗号分句痕迹：剥标点前若含 ≥3 个顿号/逗号，基本是列举式需求。
    if len(text) > 12:
        return None
    if any(word in text for word in _REQUEST_SENTENCE_WORDS):
        return None
    # 残片防护：真菜名至少 2 字。剥完动作词只剩一个字（「再来一张」→「再」）
    # 说明这句根本没有菜名，不能拿它去搜图/生图。
    if len(text) < 2:
        return None
    return text[:40] or None


def _missing_recent_image_text() -> str:
    """纯换图动作找不到历史菜谱时，如实说明，不能把动作词当菜名生图。"""
    return "上一轮没有可换图的菜谱。你可以先让我推荐一道菜，或者直接说“来张红烧肉的图”。"


def _looks_like_control_json(text: str) -> bool:
    """防止结构化 JSON/控制信令被当作正文 token 外露。"""
    raw = str(text or "").strip()
    if not raw:
        return False
    if not ((raw.startswith("{") and raw.endswith("}")) or (raw.startswith("[") and raw.endswith("]"))):
        return False
    try:
        data = json.loads(raw)
    except Exception:
        return False
    if isinstance(data, dict):
        return any(key in data for key in ("recipes", "image_url", "image_requested", "health_lights", "guardrails", "token", "answer", "stage"))
    return isinstance(data, list)


def _ask_which_dish_image_text() -> str:
    """要图但没锁定菜品时的追问文案（不假装有图，也不凭空造一张卡片）。"""
    return (
        "你想看哪一道菜的图？\n\n"
        "直接告诉我菜名就行，比如「看看红烧肉的图」；"
        "也可以先让我按你手上的食材推荐几道，你选定后我再把成品图和完整做法一起给你。"
    )


_DISH_IMAGE_HINT_PATTERN = re.compile(
    r"(?:看看|看一下|瞧瞧|来张|来一张|给我看|想看)\s*([^\s，。、！？；;：:]{2,14})"
)
_DISH_HINT_NOISE = (
    "的图", "图片", "照片", "实拍", "成品图", "示意图", "长什么样", "什么样", "图",
)
_DISH_HINT_GENERIC = ("一道", "几道", "什么", "这个", "这道", "哪个", "一点", "一下")

# 菜名前的量词：「给我一道柠檬香茅烤鲈鱼的成品图」要剥成「柠檬香茅烤鲈鱼」。
_LEADING_QUANTIFIER_STRIP = re.compile(r"^(?:一|两|三|四|五|几|个|道|份|款|些|点|盘|碗|锅|条|只)+")

# 需求句特征词：出现这些说明用户发的是一段需求描述，不是一个菜名。
# 用于拦住 `_extract_requested_dish` 把整段需求当菜名（实测「我今晚想吃清淡低盐少油的
# 晚餐…再给我一张对应的成品图」被截成 40 字菜名，出了一张菜名=用户原话的空卡片）。
_REQUEST_SENTENCE_WORDS = (
    "但是", "不过", "然后", "如果", "请先", "请给", "请你", "帮我", "帮我做", "我想", "我要",
    "之后", "等我", "还有", "并且", "而且", "另外", "然后", "顺便", "记得", "不要直接",
    "候选", "方案", "建议给", "明确告诉", "告诉我", "缺什么", "做什么", "怎么", "多少",
    "家里有", "手上有", "我有", "今晚", "今天", "明天", "口味", "吃的", "晚餐", "午餐",
    "早餐", "夜宵", "过敏", "高血压", "糖尿病", "减脂", "增肌", "营养", "热量",
)


def _names_dish_for_image(message: str) -> bool:
    """用户要图时是否点明了菜名（「看看红烧肉的图」算，只说「补张图」不算）。

    ⚠️ 这里必须和 `_extract_requested_dish` 同源。旧实现只靠
    `is_specific_dish_request` + `_DISH_IMAGE_HINT_PATTERN`（要求消息里出现
    「看看/来张/给我看…」这类**视觉动词**），于是「给我柠檬香茅烤鲈鱼的成品图」
    「给我一道「柠檬香茅烤鲈鱼」的成品图」这种**动词缺失但菜名明确**的句子
    被判成「没点名菜」→ 明明抽得出菜名却不去配图。
    改造：先取 `_extract_requested_dish` 的结果（它已经处理了书名号、量词、
    动作词剥离、需求句防护），有结果即可直接认定点名；视觉动词正则退化为兜底。
    """
    named = _extract_requested_dish(message)
    if named and not any(word in named for word in _DISH_HINT_GENERIC):
        return True
    if is_specific_dish_request(message):
        return True
    for match in _DISH_IMAGE_HINT_PATTERN.finditer(str(message or "")):
        name = match.group(1)
        for noise in _DISH_HINT_NOISE:
            name = name.replace(noise, "")
        name = name.strip("的了吧呢")
        if len(name) >= 2 and not any(word in name for word in _DISH_HINT_GENERIC):
            return True
    return False


def _standalone_image_answer(
    dish_name: str,
    image_url: str | None,
    image_ai: bool,
    note: str,
    recipe_base: dict | None = None,
    opening: str | None = None,
):
    base = recipe_base if isinstance(recipe_base, dict) else {}
    recipe = {
        "name": dish_name,
        "intro": str(base.get("intro") or note or ""),
        "difficulty": base.get("difficulty") or 1,
        "nutrition": base.get("nutrition") or 3,
        "seasonings": base.get("seasonings") or [],
        "steps": base.get("steps") or [],
        "image_url": image_url,
        "image_ai_generated": bool(image_ai),
        "image_note": note,
    }
    return {
        "opening": opening or (f"已为「{dish_name}」补上配图。" if image_url else f"暂时没能为「{dish_name}」生成可靠配图。"),
        "recipes": [recipe],
        "image_url": image_url,
        "image_ai_generated": bool(image_ai),
        "image_requested": True,
        "image_note": note,
    }


def _strip_answer_images_for_cancel(answer: str) -> str:
    try:
        data = json.loads(answer)
    except Exception:
        return answer
    if not isinstance(data, dict):
        return answer
    data["image_requested"] = False
    data["image_url"] = None
    data["image_ai_generated"] = False
    data["image_note"] = ""
    for recipe in data.get("recipes") or []:
        if not isinstance(recipe, dict):
            continue
        recipe["image_url"] = None
        recipe["image_ai_generated"] = False
        recipe["image_note"] = ""
    return json.dumps(data, ensure_ascii=False)



async def _handle_image(image: UploadFile | None, image_url: str | None):
    """统一处理图片上传：返回 (save_img_name, save_img_type, save_img_url)。
    优先用前端已传的 OSS URL（重新生成时不重复上传），否则把上传文件存到 OSS。"""
    save_img_name = None
    save_img_type = None
    save_img_url = image_url
    if image_url:
        pass  # 前端已提供 OSS URL，视觉模型直接读公网地址
    elif image is not None:
        # 体积 + 真实格式双重校验：declared content_type 不可信，且 read() 会吃满内存。
        file_bytes, real_mime = await validate_image_upload(image, ALLOWED_MIME)
        save_img_name = image.filename
        save_img_type = real_mime
        save_img_url = image_bytes_to_oss_url(file_bytes, real_mime)
    return save_img_name, save_img_type, save_img_url


def _save_record(session_id, message, answer, save_img_name, save_img_type, save_img_url):
    """每轮问答自动落库：前端刷新/重进都能从后端恢复历史。流式共用。"""
    now_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return append_message(
        sid=session_id,
        user_text=message,
        answer=answer,
        time=now_time,
        image_name=save_img_name,
        image_type=save_img_type,
        image_url=save_img_url,
    )


@router.get("/")
def health():
    return {"code": 200, "messages": "服务正常", "data": None}


@router.post("/chat/cancel-image")
async def cancel_image_decision(
    session_id: str = Form(...),
    turn_id: str | None = Form(None),
    keep_text: bool = Form(False),
):
    _cancel_image_for_turn(session_id, turn_id, keep_text=keep_text)
    record_id = _get_turn_record(session_id, turn_id)
    if record_id is not None:
        try:
            if keep_text:
                from sessions_store import mark_message_image_cancelled
                mark_message_image_cancelled(session_id, record_id)
            else:
                from sessions_store import mark_message_cancelled
                mark_message_cancelled(session_id, record_id)
        except Exception:
            pass
    message = "已取消本轮回答" if not keep_text else "已取消配图，保留文字"
    return {
        "code": 200,
        "messages": message,
        "data": {
            "session_id": session_id,
            "turn_id": turn_id,
            "record_id": record_id,
            "keep_text": keep_text,
        },
    }


@router.post("/chat")
async def chat(
    session_id: str = Form(...),
    message: str = Form(...),
    image: UploadFile | None = File(None),
    image_url: str | None = Form(None),
    mode: str = Form("home"),
    want_image: str | None = Form(None),
    location_context: str | None = Form(None),
    turn_id: str | None = Form(None),
    target_record_id: int | None = Form(None),
    target_recipe_index: int | None = Form(None),
    target_dish_name: str | None = Form(None),
):
    """统一聊天入口：仅流式分支。
    - SSE 逐事件推：正文 token -> 打字机；'structuring' -> 卡片占位动画；'answer' -> 整包 ChefAnswer JSON 渲染卡片
    - 全程走同一张 LangGraph + structure_answer 结构化节点
    """
    if not message.strip() and image is None and not image_url:
        raise HTTPException(status_code=400, detail="请至少输入文字、上传图片或提供图片 URL")

    # 取消标记只属于上一轮图片任务，不能跨轮污染新的对话。
    _clear_stale_image_cancel_for_new_turn(session_id, turn_id)

    try:
        save_img_name, save_img_type, save_img_url = await _handle_image(image, image_url)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"图片处理失败：{exc}") from exc

    # 同会话串行：建 pending 记录之前就挡掉，避免并发轮次互相覆盖落库结果。
    busy_reason = _try_begin_turn(session_id)
    if busy_reason:
        return _notice_stream(busy_reason, session_id)

    turn_intent = _classify_turn_intent(message)
    # 两阶段点菜第二阶段：「就第2个」是明确的选定动作，必须按确认处理并开启配图
    # （路由层拿不到上下文，只能靠会话记录里的候选锚点解析，见 _resolve_picked_candidate）。
    picked_candidate = _resolve_picked_candidate(session_id, message)
    if picked_candidate:
        turn_intent = "confirm_one"
    image_requested = _should_enable_image_pipeline(message, want_image) or bool(picked_candidate)
    effective_message = f"【配图开关：开启】\n{message}" if image_requested else message
    if picked_candidate:
        # ⚠️ 把解析出的菜名**显式注入**给 Agent。
        # 路由层靠会话记录里的候选锚点解析出「第2个 = 青椒炒鸡丝」，但 Agent 层读的是
        # checkpoint 的候选 payload —— 两处锚点并不总是同时在场（实测 T2 就断了：
        # checkpoint 里没有候选 payload，`resolve_candidate_pick` 返回 None）。
        # 不注入的后果：模型自己去猜「第2个」是哪道，实测凭空造了一个
        # 「滑炒鸡丝（无青椒·番茄提鲜版）」—— 候选清单里根本没有这道菜，
        # 用户看到的第2道是「青椒炒鸡丝」。菜名一旦漂移，后面所有轮次（含配图、
        # 过敏原审计、份量表）都建立在错误菜名上。
        effective_message = (
            f"【已选定候选：{picked_candidate}】\n"
            f"（用户用序号选定了上一轮候选清单里的这一道，本轮必须围绕它展开，"
            f"不得改名、不得替换成别的菜。）\n\n{effective_message}"
        )
    asset_prompt = _global_asset_prompt(message)
    if asset_prompt:
        effective_message = f"{asset_prompt}\n\n{effective_message}"
    memory_candidate_ids: list[str] = []
    try:
        # 先提取为本轮 pending 约束，确保当前回答立即遵守；只有回答成功后才会
        # 在 finish 后由前端展示确认提示，失败轮次会在持久化兜底中清理。
        from api.routes.preferences_route import _migrate, _read_family
        from memory_candidates import extract_candidates, remember_candidates

        family = _read_family() or _migrate({})
        candidates = extract_candidates(
            message,
            family.get("members") or [],
            session_id=session_id,
        )
        remember_candidates(candidates)
        memory_candidate_ids = [str(item.get("id") or "") for item in candidates]
    except Exception:
        pass
    human_message = build_human_message(
        _apply_mode_prompt(effective_message, mode),
        save_img_url,
        location_context,
        session_id,
    )
    config = {"configurable": {"thread_id": session_id}}

    # 入口先落用户消息（answer=__pending__）：客户端随时断开，用户的话必须已经在库里，
    # 否则前端 syncActiveSession 拉后端真相时会把用户消息『撤走』。
    # 完成态由 run_agent 线程 finally / generator finally 双路径幂等更新。
    _PENDING = "__pending__"
    pending_rec_id = None
    try:
        from sessions_store import append_message as _append, update_message_answer as _update_answer
        pending_rec_id = _append(session_id, message, _PENDING, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), save_img_name, save_img_type, save_img_url)
        _remember_turn_record(session_id, turn_id, pending_rec_id)
    except Exception:
        pending_rec_id = None

    if turn_intent == "home_service":
        def home_service_generator():
            answer = _home_service_redirect_text()
            try:
                if pending_rec_id is not None:
                    _update_answer(
                        session_id,
                        pending_rec_id,
                        answer,
                        save_img_name,
                        save_img_type,
                        save_img_url,
                    )
            except Exception:
                pass
            try:
                yield f"data: {json.dumps({'token': answer}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'finish': True, 'session_id': session_id, 'record_id': pending_rec_id}, ensure_ascii=False)}\n\n"
            finally:
                _clear_image_cancel(session_id, turn_id)
                _forget_turn_record(session_id, turn_id)
                _end_turn(session_id)

        return StreamingResponse(
            home_service_generator(),
            media_type="text/event-stream",
        )

    standalone_image_request = _is_standalone_image_request(message, want_image)
    if not standalone_image_request and _wants_image(message, want_image) and not _is_recipe_change_request(message):
        try:
            from sessions_store import find_recent_recipe_for_image

            requested_dish = _extract_requested_dish(message)
            if _names_dish_for_image(message) and not picked_candidate:
                # ⚠️ 用户**点名了一道菜**并要图 → 直接走配图链路。
                # 旧实现只在「历史里能找到这道菜」时才开配图（`find_recent_recipe_for_image`
                # 命中才置位），于是点名一道没听过的菜 + 要图时落回常规 Agent 链路 ——
                # 而常规链路里 `agent_graph._is_candidate_turn` 判定为 True（E4 实测），
                # 反手甩出一份候选清单，用户明确要的成品图一张没出。
                # 要图是硬意图，优先于「泛推荐先出候选」的产品形态。
                # 序号选定（「就第2个」）除外：那是在候选清单里挑，交给候选链路。
                standalone_image_request = True
            elif find_recent_recipe_for_image(session_id, requested_dish or None) or find_recent_recipe_for_image(session_id, None):
                standalone_image_request = True
        except Exception:
            pass

    # 确认已有图片的上一轮方案时，复用原答案，不再重新跑 Agent/搜图/生图。
    # 明确“换图/重新生成”等请求不会进入这里。
    # 「就第2个」这类候选选定不走复用短路：复用是拿最近一张带图卡片，
    # 而用户要的是刚列出的候选里第 N 道，复用会给出完全不相关的一道旧菜。
    reused_confirmation = None
    if (
        turn_intent == "confirm_one"
        and not picked_candidate
        and not _is_image_revision_request(message, want_image)
        and not _is_recipe_change_request(message)
    ):
        reused_confirmation = _reusable_confirmation_answer(session_id, message)

    def reused_confirmation_generator():
        final_answer = ""
        try:
            answer_obj = reused_confirmation or {}
            final_answer = json.dumps(answer_obj, ensure_ascii=False)
            yield f"data: {json.dumps({'answer': answer_obj}, ensure_ascii=False)}\n\n"
        finally:
            try:
                if pending_rec_id is not None:
                    _update_answer(
                        session_id,
                        pending_rec_id,
                        final_answer or "（本轮确认未能完成。）",
                        save_img_name,
                        save_img_type,
                        save_img_url,
                    )
            except Exception:
                pass
            _clear_image_cancel(session_id, turn_id)
            _forget_turn_record(session_id, turn_id)
            _end_turn(session_id)
            yield f"data: {json.dumps({'finish': True, 'session_id': session_id, 'record_id': pending_rec_id}, ensure_ascii=False)}\n\n"

    def standalone_image_generator():
        final_answer = ""
        try:
            from sessions_store import find_recent_recipe_for_image, find_recipe_for_image_target, update_answer_image_at_index
            requested_dish = _extract_requested_dish(message)
            target = None
            if target_record_id is not None:
                target = find_recipe_for_image_target(
                    session_id,
                    target_record_id,
                    target_recipe_index or 0,
                    target_dish_name,
                )
            if not target and requested_dish:
                target = find_recent_recipe_for_image(session_id, requested_dish)
            if not target and not requested_dish:
                # ⚠️ 兜底「抓最近一道菜」只能在用户**没点名新菜**时用。
                # 用户点名了一道库里没有的菜（E4「给我一道「柠檬香茅烤鲈鱼」的成品图」），
                # 这里抓到上一道旧菜（冬瓜豆腐汤）后，下面的 existing_image_url 判定为真，
                # 于是回「「冬瓜豆腐汤」上一轮已经有配图了」—— 用户要的菜一张图没出，
                # 还被答非所问。点名新菜时宁可按「无历史菜谱」走直接生图分支。
                target = find_recent_recipe_for_image(session_id, target_dish_name)
            if not target and not requested_dish:
                target = find_recent_recipe_for_image(session_id, None)
            if not target:
                dish_hint = str(requested_dish or target_dish_name or "").strip()
                if not dish_hint and target_record_id is None:
                    text = _missing_recent_image_text()
                    final_answer = text
                    yield f"data: {json.dumps({'token': text}, ensure_ascii=False)}\n\n"
                    return
                if not dish_hint:
                    dish_hint = "这道菜"
                if target_record_id is not None:
                    text = f"我知道你想给「{dish_hint}」换图，但这张菜谱卡片还没匹配上。请等回答保存完成后再试一次。"
                    final_answer = text
                    yield f"data: {json.dumps({'token': text}, ensure_ascii=False)}\n\n"
                    return
                # ① 没锁定菜品就先追问是哪一道：既不把「补张图」这类动作词当菜名去搜图，
                # 也不凭空新造一张卡片（方案A：不新增消息）。
                if not _names_dish_for_image(message):
                    text = _ask_which_dish_image_text()
                    final_answer = text
                    yield f"data: {json.dumps({'token': text}, ensure_ascii=False)}\n\n"
                    return
                yield f"data: {json.dumps({'stage': 'generating_image'}, ensure_ascii=False)}\n\n"
                try:
                    image_url, source = _find_recipe_image_cached(dish_hint, allow_ai_fallback=True)
                except Exception:
                    image_url, source = None, "none"
                if not image_url:
                    asset = _find_global_dish_asset(dish_hint)
                    if asset:
                        image_url = asset.get("image_url") or (asset.get("recipe") or {}).get("image_url")
                        source = "ai" if asset.get("image_ai_generated") else "real"
                image_ai = source == "ai"
                if image_url:
                    note = "AI 生成示意图（非真实成品照，仅供样式参考）" if image_ai else ""
                    answer_obj = _standalone_image_answer(dish_hint, image_url, image_ai, note)
                    final_answer = json.dumps(answer_obj, ensure_ascii=False)
                    yield f"data: {json.dumps({'answer': answer_obj}, ensure_ascii=False)}\n\n"
                    try:
                        if pending_rec_id is not None:
                            _update_answer(session_id, pending_rec_id, final_answer, save_img_name, save_img_type, save_img_url)
                    except Exception:
                        pass
                    _save_global_dish_assets(answer_obj, session_id)
                    return
                note = "暂无成品图，文字做法完整可照做"
                answer_obj = _standalone_image_answer(dish_hint, None, False, note)
                final_answer = json.dumps(answer_obj, ensure_ascii=False)
                yield f"data: {json.dumps({'answer': answer_obj}, ensure_ascii=False)}\n\n"
                return

            dish_name = target["dish_name"]
            recipe_index = int(target["recipe_index"])
            recipes = target.get("answer", {}).get("recipes") or []
            current_recipe = recipes[recipe_index] if 0 <= recipe_index < len(recipes) else {}
            existing_image_url = (
                current_recipe.get("image_url")
                or (target.get("answer", {}).get("image_url") if recipe_index == 0 else None)
            )
            if existing_image_url:
                # 方案A·就地补图：这张卡片本来就有图，只回一句话，不再新增一张同菜卡片
                # （否则同一道菜在会话里出现两次，用户无法判断以哪个为准）。
                final_answer = f"「{dish_name}」上一轮已经有配图了，就在上面那张卡片里。"
                yield f"data: {json.dumps({'token': final_answer}, ensure_ascii=False)}\n\n"
                return
            if _is_image_cancelled(session_id, turn_id):
                final_answer = "已取消配图决策"
                yield f"data: {json.dumps({'token': final_answer}, ensure_ascii=False)}\n\n"
                return
            yield f"data: {json.dumps({'stage': 'generating_image'}, ensure_ascii=False)}\n\n"
            try:
                image_url, source = _find_recipe_image_cached(dish_name, allow_ai_fallback=True)
            except Exception:
                image_url, source = None, "none"
            if _is_image_cancelled(session_id, turn_id):
                final_answer = "已取消配图决策"
                return
            image_ai = source == "ai"
            if image_url:
                note = "AI 生成示意图（非真实成品照，仅供样式参考）" if image_ai else ""
                update_answer_image_at_index(
                    session_id,
                    int(target["record_id"]),
                    int(target["recipe_index"]),
                    image_url,
                    image_ai,
                    note,
                )
                yield f"data: {json.dumps({'image': {'record_id': target['record_id'], 'turn_id': turn_id, 'index': target['recipe_index'], 'url': image_url, 'ai_generated': image_ai}}, ensure_ascii=False)}\n\n"
                # 方案A·就地补图：图通过 image 事件贴回原来那张卡片，这里只回一句确认话。
                # 以前同时再推一份 answer（新卡片）会把同一道菜变成两条，前端「补图 + 新卡片 + 回填上一轮」叠在一起。
                final_answer = f"已为「{dish_name}」补上配图，就在上面那张卡片里。"
                yield f"data: {json.dumps({'token': final_answer}, ensure_ascii=False)}\n\n"
            else:
                # 没有可靠图时如实说，并推 image_failed 让原卡片进入明确失败态——
                # 不新增卡片，避免用一张空新卡冒充「补好了」。
                final_answer = f"暂时没能为「{dish_name}」生成可靠配图，原卡片的文字做法完整可照做。"
                yield f"data: {json.dumps({'token': final_answer}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'image_failed': {'record_id': target['record_id'], 'turn_id': turn_id, 'indexes': [target['recipe_index']]}}, ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"
            return
        finally:
            try:
                if pending_rec_id is not None:
                    _update_answer(session_id, pending_rec_id, final_answer or "（本轮配图请求未能完成。）", save_img_name, save_img_type, save_img_url)
            except Exception:
                pass
            _clear_image_cancel(session_id, turn_id)
            _forget_turn_record(session_id, turn_id)
            _end_turn(session_id)
            yield f"data: {json.dumps({'finish': True, 'session_id': session_id, 'record_id': pending_rec_id}, ensure_ascii=False)}\n\n"

    def event_generator():
        full_parts = []     # 正文 token 碎片（兜底落库用）
        final_answer = None  # structure_answer 节点产出的 ChefAnswer JSON 字符串
        events = queue.Queue()
        finished = object()
        saved_flag = [False]  # 兜底落库幂等标记

        def _persist_once():
            """把入口预落的 __pending__ 记录更新为最终态（幂等）。

            双路径调用：generator finally（正常完成，及时更新）与 run_agent
            线程 finally（客户端断开时 generator 的 finally 不会执行，线程 finally 必跑）。"""
            if saved_flag[0]:
                return
            saved_flag[0] = True
            answer = final_answer if final_answer else "".join(full_parts)
            print(f"[persist] sid={session_id} rec={pending_rec_id} answer_len={len(answer or '')} parts={len(full_parts)}")
            if not (answer and answer.strip()) or answer.strip() == "__pending__":
                try:
                    from memory_candidates import dismiss

                    for candidate_id in memory_candidate_ids:
                        dismiss(candidate_id)
                except Exception:
                    pass
            # T2-P0 饮食记录：结构化答案产出菜品时自动记账（失败不阻塞聊天）
            if final_answer:
                try:
                    import json as _json
                    from api.routes.reports_route import record_meal
                    record_meal(session_id, _json.loads(final_answer))
                except Exception:
                    pass
            if not (answer and answer.strip()):
                answer = "（本轮回答未能完成：上游模型超时或连接中断，请重问一次。）"
            if _is_image_cancelled(session_id, turn_id):
                if pending_rec_id is not None:
                    try:
                        if _should_keep_text_on_cancel(session_id, turn_id):
                            from sessions_store import mark_message_image_cancelled
                            mark_message_image_cancelled(session_id, pending_rec_id, answer)
                        else:
                            from sessions_store import mark_message_cancelled
                            mark_message_cancelled(session_id, pending_rec_id)
                    except Exception:
                        pass
                return
            if pending_rec_id is not None:
                try:
                    updated = _update_answer(
                        session_id,
                        pending_rec_id,
                        answer,
                        save_img_name,
                        save_img_type,
                        save_img_url,
                    )
                    if updated:
                        if not final_answer:
                            _register_candidate_anchor(
                                session_id, pending_rec_id, message, answer
                            )
                        return
                except Exception:
                    pass  # 更新失败退回追加完整记录
            try:
                new_rec_id = _save_record(
                    session_id,
                    message,
                    answer,
                    save_img_name,
                    save_img_type,
                    save_img_url,
                )
                if not final_answer:
                    _register_candidate_anchor(
                        session_id, new_rec_id, message, answer
                    )
            except Exception:
                pass

        answer_dict = None  # structure 产出的 ChefAnswer dict；图片线程原地补图后重新序列化落库
        img_thread = None
        _img_lock = threading.Lock()
        image_failed_sent = False  # image_failed 去重：补图线程早到 / done 分支补发只发一次

        def _fill_images():
            try:
                _fill_images_once()
            finally:
                events.put(("image_thread_done", None))

        def _fill_images_once():
            """后台补图：先复用资产/搜现成图，找不到时统一允许 AI 兜底。"""
            # 默认推荐也必须保持“有菜就尽量有图”的原有体验；
            # 全局资产命中时不会走到生图，只有没有可用资产和现成图时才消耗 AI 兜底。
            allow_ai_fallback = True
            deadline = time.time() + 60
            for index, recipe in enumerate(list(answer_dict.get("recipes") or [])):
                if _is_image_cancelled(session_id, turn_id):
                    return
                if time.time() > deadline or recipe.get("image_url"):
                    continue
                name = str(recipe.get("name") or "").strip()
                if not name:
                    continue
                try:
                    image_url, source = _find_recipe_image_cached(name, allow_ai_fallback=allow_ai_fallback)
                except Exception:
                    continue
                if _is_image_cancelled(session_id, turn_id):
                    return
                if not image_url:
                    continue
                ai_flag = source == "ai"
                with _img_lock:
                    if _is_image_cancelled(session_id, turn_id):
                        return
                    recipe["image_url"] = image_url
                    recipe["image_ai_generated"] = ai_flag
                    if index == 0:
                        answer_dict["image_url"] = image_url
                        answer_dict["image_ai_generated"] = ai_flag
                    note = str(recipe.get("image_note") or answer_dict.get("image_note") or "")
                    if ai_flag:
                        note = ("AI 生成示意图：" + note) if note and "AI 生成示意图" not in note else (note or "AI 生成示意图（非真实成品照，仅供样式参考）")
                    elif not note:
                        note = ""
                    if note:
                        recipe["image_note"] = note
                        if index == 0:
                            answer_dict["image_note"] = note
                    # 回写落库：AI 生图瀑布可达 170s，远超 finish 前的 25s join 窗口；
                    # 图好后立即更新 __pending__ 记录，客户端断开/已刷新也能在重进会话时看到图。
                    if pending_rec_id is not None:
                        try:
                            _update_answer(session_id, pending_rec_id, json.dumps(answer_dict, ensure_ascii=False), save_img_name, save_img_type, save_img_url)
                        except Exception:
                            pass
                    try:
                        from dish_assets_store import update_dish_asset_image
                        update_dish_asset_image(name, image_url, ai_flag, note)
                    except Exception:
                        pass
                if _is_image_cancelled(session_id, turn_id):
                    return
                events.put(("item", ("image", {
                    "record_id": pending_rec_id,
                    "turn_id": turn_id,
                    "index": index,
                    "url": image_url,
                    "ai_generated": ai_flag,
                })))
            # 第5问：补图结束给失败终态——收集最终仍无图的菜品推 image_failed 事件，
            # 前端据此把含糊的「暂无可靠成品图」占位升级为明确的失败说明（数据诚实原则）。
            if _is_image_cancelled(session_id, turn_id):
                return
            failed_indexes = [
                i for i, r in enumerate(answer_dict.get("recipes") or [])
                if not r.get("image_url")
            ]
            if failed_indexes:
                events.put(("item", ("image_failed", {
                    "record_id": pending_rec_id,
                    "turn_id": turn_id,
                    "indexes": failed_indexes,
                })))

        def run_agent():
            try:
                for item in stream_agent(human_message, session_id):
                    events.put(("item", item))
                return
            except Exception as exc:
                # A 方案 failover：主 provider 超时/连接黑洞时，切备用 provider 整轮重跑一次。
                # 重跑用派生 thread_id（-fo 后缀），避免同一用户消息重复写入 checkpoint 状态；
                # 代价是重跑轮拿不到此前多轮上下文，但答案仍正常落库，属异常兜底的诚实降级。
                if not is_provider_failure(exc):
                    events.put(("error", exc))
                    return
                try:
                    switched = failover_llms()
                except Exception:
                    switched = None
                if not switched:
                    events.put(("error", exc))
                    return
                events.put(("item", ("stage", "switching_model")))
                try:
                    fo_thread = f"{session_id}-fo{int(time.time())}"
                    for item in stream_agent(human_message, fo_thread):
                        events.put(("item", item))
                except Exception as exc2:
                    events.put(("error", exc2))
            finally:
                # 客户端断开时 generator 的 finally 不会执行（ASGI 取消 task 不 aclose
                # sync generator），线程 finally 是落库的可靠兜底；幂等，双路径安全。
                try:
                    _persist_once()
                except Exception:
                    pass
                # 生成器 finally 在客户端断开时可能不执行，轮次槽/并发槽必须在这里兜住；
                # 两处都调用是幂等的（pop 带默认值、信号量释放有 ValueError 保护）。
                _end_turn(session_id)
                _release_agent_slot()
                events.put(("done", finished))

        # 全局 Agent 背压：槽位满就立刻如实拒绝，不排队——排队会一直占着 HTTP 连接和心跳。
        if not _acquire_agent_slot():
            yield f"data: {json.dumps({'token': '当前同时在处理的需求有点多，请等十几秒再发一次。'}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'finish': True, 'session_id': session_id, 'record_id': pending_rec_id}, ensure_ascii=False)}\n\n"
            _end_turn(session_id)
            return

        # Agent 内部可能在联网搜索、图片下载或结构化模型调用中等待较久。
        # 放到后台线程后，主生成器可以每隔几秒发送心跳，避免前端误判为断线。
        threading.Thread(target=run_agent, daemon=True).start()
        yield f"data: {json.dumps({'status': 'working'}, ensure_ascii=False)}\n\n"

        started = time.time()
        image_wait_deadline = None
        agent_done_seen = False
        image_thread_done_seen = False
        try:
            while True:
                poll_timeout = _IMAGE_THREAD_POLL_TIMEOUT_S
                if image_wait_deadline is not None:
                    remaining = image_wait_deadline - time.monotonic()
                    if remaining <= 0:
                        if answer_dict is not None and not image_failed_sent and not _is_image_cancelled(session_id, turn_id):
                            with _img_lock:
                                failed_indexes = [
                                    i for i, r in enumerate(answer_dict.get("recipes") or [])
                                    if not r.get("image_url")
                                ]
                                for i in failed_indexes:
                                    r = answer_dict["recipes"][i]
                                    r["image_note"] = "成品图未能生成（搜图与 AI 生图均不可用），文字做法完整可照做"
                                    if i == 0:
                                        answer_dict["image_note"] = r["image_note"]
                                final_answer = json.dumps(answer_dict, ensure_ascii=False)
                            if failed_indexes:
                                yield f"data: {json.dumps({'image_failed': {'record_id': pending_rec_id, 'turn_id': turn_id, 'indexes': failed_indexes}}, ensure_ascii=False)}\n\n"
                        break
                    poll_timeout = min(poll_timeout, max(0.05, remaining))
                try:
                    event_type, event = events.get(timeout=poll_timeout)
                except queue.Empty:
                    elapsed = int(time.time() - started)
                    yield f"data: {json.dumps({'heartbeat': {'elapsed': elapsed}}, ensure_ascii=False)}\n\n"
                    continue

                if event_type == "error":
                    raise event
                if event_type == "done":
                    # done 只表示 Agent 主链结束，补图线程仍可能稍晚返回。
                    # 继续消费队列并保持心跳，直到 image_thread_done 到达。
                    agent_done_seen = True
                    if img_thread is None or _is_image_cancelled(session_id, turn_id) or image_thread_done_seen:
                        break
                    image_wait_deadline = time.monotonic() + _IMAGE_THREAD_MAX_WAIT_S
                    continue
                if event_type == "image_thread_done":
                    image_thread_done_seen = True
                    if agent_done_seen:
                        break
                    continue

                kind, payload = event
                if kind == "token":
                    if not isinstance(payload, str) or _looks_like_control_json(payload):
                        continue
                    full_parts.append(payload)
                    yield f"data: {json.dumps({'token': payload}, ensure_ascii=False)}\n\n"
                elif kind == "stage":
                    yield f"data: {json.dumps({'stage': payload}, ensure_ascii=False)}\n\n"
                elif kind == "answer":
                    try:
                        answer_dict = json.loads(payload)
                    except Exception as _pe:
                        print(f"[flow] answer json-parse failed: {_pe}")
                        raise
                    if isinstance(answer_dict, dict) and answer_dict.get("answer_kind") == "candidates":
                        # 两阶段点菜第一阶段：正文就是用户看到的编号候选清单（已流式推完），
                        # 这里绝不推 answer —— 前端收到 answer 会清空正文，而候选没有卡片可渲染。
                        # 候选只登记进消息的独立字段，作为下一轮「就第2个」的确定性锚点。
                        candidate_names = [
                            str(name).strip()
                            for name in (answer_dict.get("candidates") or [])
                            if str(name).strip()
                        ]
                        if candidate_names and pending_rec_id is not None:
                            try:
                                from sessions_store import set_message_candidates
                                set_message_candidates(session_id, pending_rec_id, candidate_names)
                            except Exception:
                                pass
                        answer_dict = None
                        continue
                    final_answer = payload
                    if isinstance(answer_dict, dict):
                        # 只有模型确实产出具体菜品时，推荐阶段才进入图片展示；
                        # 普通健康问答即使命中饮食关键词，也不生成空图片槽。
                        has_recipes = any(
                            isinstance(recipe, dict) and str(recipe.get("name") or "").strip()
                            for recipe in (answer_dict.get("recipes") or [])
                        )
                        answer_dict["image_requested"] = bool(image_requested and has_recipes)
                        if answer_dict["image_requested"]:
                            # 跨会话资产只复用基础菜品和图片；健康结论仍来自本轮 Agent。
                            for recipe in answer_dict.get("recipes") or []:
                                if not isinstance(recipe, dict) or recipe.get("image_url"):
                                    continue
                                asset = _find_global_dish_asset(recipe.get("name"))
                                if not asset:
                                    continue
                                asset_recipe = asset.get("recipe") or {}
                                cached_url = asset.get("image_url") or asset_recipe.get("image_url")
                                if cached_url:
                                    recipe["image_url"] = cached_url
                                    recipe["image_ai_generated"] = bool(
                                        asset.get("image_ai_generated")
                                        or asset_recipe.get("image_ai_generated")
                                    )
                                    recipe["image_note"] = (
                                        asset.get("image_note")
                                        or asset_recipe.get("image_note")
                                        or ""
                                    )
                            if answer_dict.get("recipes"):
                                first = answer_dict["recipes"][0]
                                answer_dict["image_url"] = first.get("image_url")
                                answer_dict["image_ai_generated"] = bool(first.get("image_ai_generated"))
                                answer_dict["image_note"] = first.get("image_note") or ""
                        _save_global_dish_assets(answer_dict, session_id)
                        # 复用缓存图片（上面那步）时不会启动补图线程，于是 done 分支
                        # 不会有 img_thread 来把 answer_dict 回填进 final_answer。
                        # 不在这里同步一次，落库的就是「富化图片之前」的原始包：
                        # 用户先看到有图的卡片，前端 +1.8s/+6s 同步后图片又消失。
                        final_answer = json.dumps(answer_dict, ensure_ascii=False)
                    # 卡片先出、图片后补：无图菜名交给后台线程（25s 预算），
                    # 搜到即推 image 事件让前端动态填图，不再阻塞 answer 120s。
                    if answer_dict.get("image_requested") and not _is_image_cancelled(session_id, turn_id) and any(not r.get("image_url") for r in (answer_dict.get("recipes") or [])):
                        img_thread = threading.Thread(target=_fill_images, daemon=True)
                        img_thread.start()
                    # 先通知前端"正文说完了，正在整理卡片"，再推整包 JSON
                    yield f"data: {json.dumps({'structuring': True}, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'answer': answer_dict}, ensure_ascii=False)}\n\n"
                elif kind == "image":
                    if _is_image_cancelled(session_id, turn_id):
                        continue
                    yield f"data: {json.dumps({'image': payload}, ensure_ascii=False)}\n\n"
                elif kind == "image_failed":
                    if _is_image_cancelled(session_id, turn_id):
                        continue
                    image_failed_sent = True
                    yield f"data: {json.dumps({'image_failed': payload}, ensure_ascii=False)}\n\n"

            # 正常路径落库（幂等；finally 亦兜底）
            _persist_once()
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"
            return
        finally:
            # 无论正常完成、后端异常还是客户端断开（GeneratorExit），都兜底落库一次
            _persist_once()
            # 当前流已经结束，取消状态不能泄漏到下一轮。
            _clear_image_cancel_after_thread(session_id, turn_id, img_thread)
            _end_turn(session_id)

        # 整轮正常结束（持久化已由 finally 完成，此处幂等）
        yield f"data: {json.dumps({'finish': True, 'session_id': session_id, 'record_id': pending_rec_id}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        reused_confirmation_generator()
        if reused_confirmation is not None
        else standalone_image_generator()
        if standalone_image_request
        else event_generator(),
        media_type="text/event-stream",
    )
