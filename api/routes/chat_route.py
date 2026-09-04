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
_CANCELLED_IMAGE_LOCK = threading.Lock()


def _image_cancel_key(session_id: str, turn_id: str | None = None) -> str:
    return f"{session_id}:{turn_id}" if turn_id else session_id


def _cancel_image_for_turn(session_id: str, turn_id: str | None = None) -> None:
    with _CANCELLED_IMAGE_LOCK:
        _CANCELLED_IMAGE_TURNS.add(_image_cancel_key(session_id, turn_id))


def _clear_image_cancel(session_id: str, turn_id: str | None = None) -> None:
    with _CANCELLED_IMAGE_LOCK:
        _CANCELLED_IMAGE_TURNS.discard(_image_cancel_key(session_id, turn_id))


def _is_image_cancelled(session_id: str, turn_id: str | None = None) -> bool:
    with _CANCELLED_IMAGE_LOCK:
        return (
            _image_cancel_key(session_id, turn_id) in _CANCELLED_IMAGE_TURNS
            or session_id in _CANCELLED_IMAGE_TURNS
        )


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
        "看看图", "看看图片", "看图片", "看图", "看一下图", "看一下图片", "看个图",
        "给我看图", "给我看看", "让我看看", "想看图片", "想看图", "图片欣赏",
        "成品图", "成品照", "实拍图", "示意图", "效果图", "样图", "参考图",
        "想看看", "长什么样", "什么样子", "啥样", "样式", "外观", "照片", "实拍",
        "换张图", "换一张", "再来一张", "另一张", "重新生成",
    )
    return any(phrase in text for phrase in strong_phrases)


def _is_image_revision_request(message: str, want_image: str | None) -> bool:
    if not _wants_image(message, want_image):
        return False
    text = str(message or "")
    contextual_refs = ("上一道", "上一道菜", "刚才", "刚刚", "前面", "这道", "这道菜", "这个", "它", "上面", "上一份", "这张")
    revision_phrases = ("换图", "换张图", "换一张", "再来一张", "重新生成", "重新配", "重画", "不满意", "不好看", "另一张")
    return any(ref in text for ref in contextual_refs) or any(phrase in text for phrase in revision_phrases)


def _is_visual_dish_lookup_request(message: str, want_image: str | None) -> bool:
    """识别“某道菜长什么样/想看看”这类无历史菜谱也应直接出图的请求。"""
    if not _wants_image(message, want_image):
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
        "吃", "饭", "餐", "早餐", "午餐", "晚餐", "夜宵", "外卖", "点餐", "食堂",
        "餐厅", "冰箱", "营养", "热量", "减脂", "控糖", "高血压", "糖尿病",
        "痛风", "尿酸", "健康饮食", "附近吃什么",
    )
    return any(marker in text for marker in markers)


def _should_enable_image_pipeline(message: str, want_image: str | None) -> bool:
    """首次菜谱流程：只有明确开配图且属于饮食场景时才启动补图。"""
    return (
        _wants_image(message, want_image)
        and _looks_like_dining_request(message)
        and not _is_image_revision_request(message, want_image)
    )


def _is_standalone_image_request(message: str, want_image: str | None) -> bool:
    """只保留“已有图片不满意，换一张”这类后续请求。"""
    return _is_image_revision_request(message, want_image) or _is_visual_dish_lookup_request(message, want_image)


def _extract_requested_dish(message: str) -> str | None:
    raw = str(message or "")
    contextual_refs = ("上一道", "上一道菜", "刚才", "刚刚", "前面", "这道", "这道菜", "这个", "它", "上面", "上一份")
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


def _standalone_image_answer(dish_name: str, image_url: str | None, image_ai: bool, note: str):
    return {
        "opening": f"已为「{dish_name}」补上配图。" if image_url else f"暂时没能为「{dish_name}」生成可靠配图。",
        "recipes": [
            {
                "name": dish_name,
                "intro": note,
                "difficulty": 1,
                "nutrition": 3,
                "seasonings": [],
                "steps": [],
                "image_url": image_url,
                "image_ai_generated": bool(image_ai),
                "image_note": note,
            }
        ],
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
async def cancel_image_decision(session_id: str = Form(...), turn_id: str | None = Form(None)):
    _cancel_image_for_turn(session_id, turn_id)
    return {"code": 200, "messages": "已取消配图决策", "data": {"session_id": session_id, "turn_id": turn_id}}


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

    try:
        save_img_name, save_img_type, save_img_url = await _handle_image(image, image_url)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"图片处理失败：{exc}") from exc
    image_requested = _should_enable_image_pipeline(message, want_image) or _looks_like_dining_request(message)
    effective_message = f"【配图开关：开启】\n{message}" if image_requested else message
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
    except Exception:
        pending_rec_id = None

    standalone_image_request = _is_standalone_image_request(message, want_image)
    if not standalone_image_request and _wants_image(message, want_image):
        try:
            from sessions_store import find_recent_recipe_for_image

            requested_dish = _extract_requested_dish(message)
            if find_recent_recipe_for_image(session_id, requested_dish or None) or find_recent_recipe_for_image(session_id, None):
                standalone_image_request = True
        except Exception:
            pass

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
                    image_url, source = find_recipe_image(dish_hint, allow_ai_fallback=True)
                except Exception:
                    image_url, source = None, "none"
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
            if _is_image_cancelled(session_id, turn_id):
                final_answer = "已取消配图决策"
                yield f"data: {json.dumps({'token': final_answer}, ensure_ascii=False)}\n\n"
                return
            yield f"data: {json.dumps({'stage': 'generating_image'}, ensure_ascii=False)}\n\n"
            try:
                image_url, source = find_recipe_image(dish_name, allow_ai_fallback=True)
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
                yield f"data: {json.dumps({'image': {'record_id': target['record_id'], 'index': target['recipe_index'], 'url': image_url, 'ai_generated': image_ai}}, ensure_ascii=False)}\n\n"
                text = f"已给上一道「{dish_name}」补上配图。"
                final_answer = text
                yield f"data: {json.dumps({'token': text}, ensure_ascii=False)}\n\n"
            else:
                note = "暂无成品图，文字做法完整可照做"
                text = f"暂时没能为上一道「{dish_name}」生成可靠配图。"
                final_answer = text
                yield f"data: {json.dumps({'token': text}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'image_failed': {'record_id': target['record_id'], 'indexes': [target['recipe_index']]}}, ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"
            return
        finally:
            try:
                if pending_rec_id is not None:
                    _update_answer(session_id, pending_rec_id, final_answer or "（本轮配图请求未能完成。）", save_img_name, save_img_type, save_img_url)
            except Exception:
                pass
        yield f"data: {json.dumps({'finish': True, 'session_id': session_id}, ensure_ascii=False)}\n\n"

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
                answer = _strip_answer_images_for_cancel(answer)
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
            """后台补图：对无图菜名搜成品图，25s 预算内逐个补，实时推 image 事件。"""
            deadline = time.time() + 25
            for index, recipe in enumerate(list(answer_dict.get("recipes") or [])):
                if _is_image_cancelled(session_id, turn_id):
                    return
                if time.time() > deadline or recipe.get("image_url"):
                    continue
                name = str(recipe.get("name") or "").strip()
                if not name:
                    continue
                try:
                    image_url, source = find_recipe_image(name, allow_ai_fallback=True)
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
                if _is_image_cancelled(session_id, turn_id):
                    return
                events.put(("item", ("image", {"index": index, "url": image_url, "ai_generated": ai_flag})))
            # 第5问：补图结束给失败终态——收集最终仍无图的菜品推 image_failed 事件，
            # 前端据此把含糊的「暂无可靠成品图」占位升级为明确的失败说明（数据诚实原则）。
            if _is_image_cancelled(session_id, turn_id):
                return
            failed_indexes = [
                i for i, r in enumerate(answer_dict.get("recipes") or [])
                if not r.get("image_url")
            ]
            if failed_indexes:
                events.put(("item", ("image_failed", {"indexes": failed_indexes})))

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
                                        img_event = {"index": i, "url": r["image_url"], "ai_generated": bool(r.get("image_ai_generated"))}
                                        yield f"data: {json.dumps({'image': img_event}, ensure_ascii=False)}\n\n"
                                    elif not img_thread.is_alive():
                                        # 真正走完补图流程仍无图，才落明确失败态。
                                        r["image_note"] = "成品图未能生成（搜图与 AI 生图均不可用），文字做法完整可照做"
                                        if i == 0:
                                            answer_dict["image_note"] = r["image_note"]
                                        failed_indexes.append(i)
                                final_answer = json.dumps(answer_dict, ensure_ascii=False)
                                if failed_indexes and not image_failed_sent and not img_thread.is_alive() and not _is_image_cancelled(session_id, turn_id):
                                    yield f"data: {json.dumps({'image_failed': {'indexes': failed_indexes}}, ensure_ascii=False)}\n\n"
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
                        answer_dict["image_requested"] = bool(image_requested)
                    # 卡片先出、图片后补：无图菜名交给后台线程（25s 预算），
                    # 搜到即推 image 事件让前端动态填图，不再阻塞 answer 120s。
                    if image_requested and not _is_image_cancelled(session_id, turn_id) and any(not r.get("image_url") for r in (answer_dict.get("recipes") or [])):
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

        # 整轮正常结束（持久化已由 finally 完成，此处幂等）
        yield f"data: {json.dumps({'finish': True, 'session_id': session_id}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        standalone_image_generator() if standalone_image_request else event_generator(),
        media_type="text/event-stream",
    )
