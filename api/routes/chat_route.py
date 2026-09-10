# chat_route.py：只负责"AI 对话"这一类接口（图片/文本 -> 大模型 -> 存库）
# 当前只保留流式分支（SSE 打字机 + 整包卡片 JSON），非流式分支已删除。
from fastapi import APIRouter, File, Form, UploadFile, HTTPException
from fastapi.responses import StreamingResponse
import json  # 把 token / structuring / answer / finish 打包成 SSE 事件
import queue
import threading

from main import (
    build_human_message,
    stream_agent,
    image_bytes_to_oss_url,
)
from agent_graph import failover_llms
from agent_tools import find_recipe_image
from model_name import is_provider_failure
from sessions_store import append_message
import time
from datetime import datetime
import re

router = APIRouter()  # 分文件写接口的小路由

# 图片 MIME 白名单：挡掉非图片和可能的恶意文件
ALLOWED_MIME = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
_CANCELLED_IMAGE_TURNS: set[str] = set()
_CANCELLED_IMAGE_KEEP_TEXT: set[str] = set()
_CANCELLED_IMAGE_LOCK = threading.Lock()
_RECIPE_IMAGE_CACHE: dict[tuple[str, bool], tuple[str | None, str, float]] = {}
_RECIPE_IMAGE_CACHE_LOCK = threading.Lock()
_SEARCH_IMAGE_CACHE_TTL = 60 * 60 * 6
_AI_IMAGE_CACHE_TTL = 60 * 60 * 24 * 7
_ACTIVE_TURN_RECORDS: dict[str, int] = {}
_ACTIVE_TURN_RECORDS_LOCK = threading.Lock()


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
    with _CANCELLED_IMAGE_LOCK:
        _CANCELLED_IMAGE_TURNS.add(key)
        if keep_text:
            _CANCELLED_IMAGE_KEEP_TEXT.add(key)
        else:
            _CANCELLED_IMAGE_KEEP_TEXT.discard(key)


def _clear_image_cancel(session_id: str, turn_id: str | None = None) -> None:
    key = _image_cancel_key(session_id, turn_id)
    with _CANCELLED_IMAGE_LOCK:
        _CANCELLED_IMAGE_TURNS.discard(key)
        _CANCELLED_IMAGE_KEEP_TEXT.discard(key)


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
        _CANCELLED_IMAGE_TURNS.difference_update(
            {
                key
                for key in _CANCELLED_IMAGE_TURNS
                if key == session_id or key.startswith(prefix)
            }
        )
        _CANCELLED_IMAGE_KEEP_TEXT.difference_update(
            {
                key
                for key in _CANCELLED_IMAGE_KEEP_TEXT
                if key == session_id or key.startswith(prefix)
            }
        )


def _is_image_cancelled(session_id: str, turn_id: str | None = None) -> bool:
    with _CANCELLED_IMAGE_LOCK:
        return (
            _image_cancel_key(session_id, turn_id) in _CANCELLED_IMAGE_TURNS
            or session_id in _CANCELLED_IMAGE_TURNS
        )


def _should_keep_text_on_cancel(session_id: str, turn_id: str | None = None) -> bool:
    with _CANCELLED_IMAGE_LOCK:
        return (
            _image_cancel_key(session_id, turn_id) in _CANCELLED_IMAGE_KEEP_TEXT
            or session_id in _CANCELLED_IMAGE_KEEP_TEXT
        )


def _active_turn_key(session_id: str, turn_id: str | None) -> str | None:
    return f"{session_id}:{turn_id}" if turn_id else None


def _remember_turn_record(session_id: str, turn_id: str | None, record_id: int | None) -> None:
    key = _active_turn_key(session_id, turn_id)
    if key and record_id is not None:
        with _ACTIVE_TURN_RECORDS_LOCK:
            _ACTIVE_TURN_RECORDS[key] = record_id


def _get_turn_record(session_id: str, turn_id: str | None) -> int | None:
    key = _active_turn_key(session_id, turn_id)
    if not key:
        return None
    with _ACTIVE_TURN_RECORDS_LOCK:
        return _ACTIVE_TURN_RECORDS.get(key)


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
        "看看图", "看看图片", "看图片", "看图", "看一下图", "看一下图片", "看个图",
        "给我看图", "给我看看", "让我看看", "想看图片", "想看图", "图片欣赏",
        "成品图", "成品照", "实拍图", "示意图", "效果图", "样图", "参考图",
        "想看看", "长什么样", "什么样子", "啥样", "样式", "外观", "照片", "实拍",
        "换张图", "换一张", "再来一张", "另一张", "重新生成",
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
        cached = _RECIPE_IMAGE_CACHE.get(key)
        if cached and now - cached[2] < ttl:
            return cached[0], cached[1]
    image_url, source = find_recipe_image(name, allow_ai_fallback=allow_ai_fallback)
    with _RECIPE_IMAGE_CACHE_LOCK:
        _RECIPE_IMAGE_CACHE[key] = (image_url, source, now)
    return image_url, source


def _reusable_confirmation_answer(session_id: str, message: str) -> dict | None:
    """确认上一道已有配图的菜时，直接复用原答案，避免重新命名和换图。

    只有上一轮确实已经有图才走这个短路；没有图时仍交给原有确认流程补图，
    这样不会改变首次确认菜品的行为。
    """
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
        "餐厅", "饭店", "店里", "到店", "堂食", "外食", "外吃", "外出就餐",
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


def _looks_like_home_service_request(text: str) -> bool:
    markers = ("上门", "到家服务", "私厨", "厨师到家", "请厨师", "预约厨师", "上门做")
    return any(marker in str(text or "") for marker in markers)


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
    confirm_words = ("就做", "就吃", "来这个", "做这个", "吃这个", "定这个", "选这个", "就它", "就这道", "第一道", "第二道", "第三道")
    if any(word in text for word in confirm_words):
        return "confirm_one"
    followup_words = ("清淡", "少盐", "少油", "不要", "别放", "能不能", "可以吗", "适合吗", "热量", "钠", "糖", "脂肪")
    if any(word in text for word in followup_words) and not _looks_like_dining_request(text):
        return "followup"
    if _looks_like_dining_request(text):
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
    """只把已经进入菜品选择的请求送入图片链路，健康泛问答不启动搜图。"""
    text = str(message or "").strip()
    if any(marker in text for marker in ("帮我做", "做道", "做个", "做一份", "来道", "来个", "菜品", "菜谱", "食谱")):
        return True
    if any(marker in text for marker in ("推荐", "想吃")):
        return any(char in text for char in ("鸡", "鱼", "肉", "蛋", "虾", "豆腐", "面", "饭", "菜", "汤", "粥", "粉"))
    return False


def _should_enable_image_pipeline(message: str, want_image: str | None) -> bool:
    """菜品推荐/确认流程允许配图；没有具体 recipes 时由 answer 阶段关闭展示。"""
    if _wants_image(message, want_image) and not _is_image_revision_request(message, want_image):
        return _looks_like_dining_request(message)
    intent = _classify_turn_intent(message)
    return intent in {"confirm_one", "change_one"} or (
        intent == "recommend" and _is_recipe_selection_request(message)
    )


def _is_standalone_image_request(message: str, want_image: str | None) -> bool:
    """只保留“已有图片不满意，换一张”这类后续请求。"""
    return _is_image_revision_request(message, want_image) or _is_visual_dish_lookup_request(message, want_image)


def _extract_requested_dish(message: str) -> str | None:
    raw = str(message or "")
    contextual_refs = ("上一道", "上一道菜", "刚才", "刚刚", "前面", "这道", "这道菜", "这个", "这种", "那种", "这份", "这个方案", "它", "上面", "上一份")
    if any(ref in raw for ref in contextual_refs):
        return None
    text = re.sub(r"【[^】]+】", "", raw)
    text = re.sub(r"\[[^\]]+\]", "", text)
    text = re.sub(r"(帮我|给我|我想|想要|想看看|可以|能不能|能否|麻烦|请|一下|看看|看下|看一看|展示|来展示|欣赏)", "", text)
    text = re.sub(r"(配图|配张图|补图|换图|生成图片|生成一张图|来张图|发图|发张图|发图片|发个图|出图|出个图|图片欣赏|成品图|成品照|实拍图|示意图|效果图|样图|参考图|图片|照片|实拍|图)", "", text)
    text = re.sub(r"(给我看图|给我看看|让我看看|看一下图|看一下图片|看个图|长什么样|什么样子|啥样|什么样|样式|外观)", "", text)
    text = re.sub(r"[，。！？、,.!?：:\s]+", "", text).strip()
    return text[:40] or None


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
        if image.content_type not in ALLOWED_MIME:
            raise HTTPException(status_code=400, detail="只支持 JPG、PNG、WEBP 图片")
        file_bytes = await image.read()
        save_img_name = image.filename
        save_img_type = image.content_type
        save_img_url = image_bytes_to_oss_url(file_bytes, image.content_type)
    return save_img_name, save_img_type, save_img_url


def _save_record(session_id, message, answer, save_img_name, save_img_type, save_img_url):
    """每轮问答自动落库：前端刷新/重进都能从后端恢复历史。流式共用。"""
    now_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    append_message(
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
    turn_intent = _classify_turn_intent(message)
    image_requested = _should_enable_image_pipeline(message, want_image)
    effective_message = f"【配图开关：开启】\n{message}" if image_requested else message
    asset_prompt = _global_asset_prompt(message)
    if asset_prompt:
        effective_message = f"{asset_prompt}\n\n{effective_message}"
    human_message = build_human_message(
        _apply_mode_prompt(effective_message, mode),
        save_img_url,
        location_context,
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

        return StreamingResponse(
            home_service_generator(),
            media_type="text/event-stream",
        )

    standalone_image_request = _is_standalone_image_request(message, want_image)
    if not standalone_image_request and _wants_image(message, want_image) and not _is_recipe_change_request(message):
        try:
            from sessions_store import find_recent_recipe_for_image

            requested_dish = _extract_requested_dish(message)
            if find_recent_recipe_for_image(session_id, requested_dish or None) or find_recent_recipe_for_image(session_id, None):
                standalone_image_request = True
        except Exception:
            pass

    # 确认已有图片的上一轮方案时，复用原答案，不再重新跑 Agent/搜图/生图。
    # 明确“换图/重新生成”等请求不会进入这里。
    reused_confirmation = None
    if (
        turn_intent == "confirm_one"
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
            if not target:
                target = find_recent_recipe_for_image(session_id, target_dish_name)
            if not target:
                target = find_recent_recipe_for_image(session_id, None)
            if not target:
                dish_hint = str(requested_dish or target_dish_name or "").strip()
                if not dish_hint:
                    dish_hint = "这道菜"
                if target_record_id is not None:
                    text = f"我知道你想给「{dish_hint}」换图，但这张菜谱卡片还没匹配上。请等回答保存完成后再试一次。"
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
                image_ai = bool(
                    current_recipe.get("image_ai_generated")
                    or (target.get("answer", {}).get("image_ai_generated") if recipe_index == 0 else False)
                )
                note = str(
                    current_recipe.get("image_note")
                    or target.get("answer", {}).get("image_note")
                    or ("AI 生成示意图（非真实成品照，仅供样式参考）" if image_ai else "")
                )
                answer_obj = _standalone_image_answer(
                    dish_name,
                    existing_image_url,
                    image_ai,
                    note,
                    current_recipe,
                    f"「{dish_name}」上一轮已经有配图，我直接给你贴出来。",
                )
                final_answer = json.dumps(answer_obj, ensure_ascii=False)
                yield f"data: {json.dumps({'answer': answer_obj}, ensure_ascii=False)}\n\n"
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
                answer_obj = _standalone_image_answer(dish_name, image_url, image_ai, note, current_recipe)
                final_answer = json.dumps(answer_obj, ensure_ascii=False)
                yield f"data: {json.dumps({'answer': answer_obj}, ensure_ascii=False)}\n\n"
            else:
                note = "暂无成品图，文字做法完整可照做"
                answer_obj = _standalone_image_answer(dish_name, None, False, note, current_recipe)
                final_answer = json.dumps(answer_obj, ensure_ascii=False)
                yield f"data: {json.dumps({'answer': answer_obj}, ensure_ascii=False)}\n\n"
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
                    _update_answer(session_id, pending_rec_id, answer, save_img_name, save_img_type, save_img_url)
                    return
                except Exception:
                    pass  # 更新失败退回追加完整记录
            try:
                _save_record(session_id, message, answer, save_img_name, save_img_type, save_img_url)
            except Exception:
                pass

        answer_dict = None  # structure 产出的 ChefAnswer dict；图片线程原地补图后重新序列化落库
        img_thread = None
        _img_lock = threading.Lock()
        image_failed_sent = False  # image_failed 去重：补图线程早到 / done 分支补发只发一次

        def _fill_images():
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
                events.put(("done", finished))

        # Agent 内部可能在联网搜索、图片下载或结构化模型调用中等待较久。
        # 放到后台线程后，主生成器可以每隔几秒发送心跳，避免前端误判为断线。
        threading.Thread(target=run_agent, daemon=True).start()
        yield f"data: {json.dumps({'status': 'working'}, ensure_ascii=False)}\n\n"

        started = time.time()
        try:
            while True:
                try:
                    event_type, event = events.get(timeout=10)
                except queue.Empty:
                    elapsed = int(time.time() - started)
                    yield f"data: {json.dumps({'heartbeat': {'elapsed': elapsed}}, ensure_ascii=False)}\n\n"
                    continue

                if event_type == "error":
                    raise event
                if event_type == "done":
                    # done 由 run_agent 主线在 structure 完成后立刻 put，而 image 事件
                    # 由补图线程几秒后才 put——不在这里等一小窗并补推，image 事件会
                    # 永远被 break 跳过（用户看到卡片一直无图的根因）。
                    if img_thread is not None and not _is_image_cancelled(session_id, turn_id):
                        img_thread.join(timeout=25)
                        with _img_lock:
                            if answer_dict is not None and not _is_image_cancelled(session_id, turn_id):
                                failed_indexes = []
                                for i, r in enumerate(answer_dict.get("recipes") or []):
                                    if r.get("image_url"):
                                        img_event = {
                                            "record_id": pending_rec_id,
                                            "turn_id": turn_id,
                                            "index": i,
                                            "url": r["image_url"],
                                            "ai_generated": bool(r.get("image_ai_generated")),
                                        }
                                        yield f"data: {json.dumps({'image': img_event}, ensure_ascii=False)}\n\n"
                                    elif not img_thread.is_alive():
                                        # 真正走完补图流程仍无图，才落明确失败态。
                                        r["image_note"] = "成品图未能生成（搜图与 AI 生图均不可用），文字做法完整可照做"
                                        if i == 0:
                                            answer_dict["image_note"] = r["image_note"]
                                        failed_indexes.append(i)
                                final_answer = json.dumps(answer_dict, ensure_ascii=False)
                                if failed_indexes and not image_failed_sent and not img_thread.is_alive() and not _is_image_cancelled(session_id, turn_id):
                                    yield f"data: {json.dumps({'image_failed': {'record_id': pending_rec_id, 'turn_id': turn_id, 'indexes': failed_indexes}}, ensure_ascii=False)}\n\n"
                    break

                kind, payload = event
                if kind == "token":
                    if not isinstance(payload, str) or _looks_like_control_json(payload):
                        continue
                    full_parts.append(payload)
                    yield f"data: {json.dumps({'token': payload}, ensure_ascii=False)}\n\n"
                elif kind == "stage":
                    yield f"data: {json.dumps({'stage': payload}, ensure_ascii=False)}\n\n"
                elif kind == "answer":
                    final_answer = payload
                    try:
                        answer_dict = json.loads(payload)
                    except Exception as _pe:
                        print(f"[flow] answer json-parse failed: {_pe}")
                        raise
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
