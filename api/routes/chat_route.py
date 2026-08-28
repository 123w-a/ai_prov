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

router = APIRouter()  # 分文件写接口的小路由

# 图片 MIME 白名单：挡掉非图片和可能的恶意文件
ALLOWED_MIME = {"image/jpeg", "image/jpg", "image/png", "image/webp"}

MODE_PROMPTS = {
    "home": "[场景：在家做饭，优先用现有食材给出可执行菜谱]",
    "dining": "[场景：外出吃饭 / 懒人点单，优先给附近餐厅、点单搭配与预算建议]",
    "fridge": "[场景：识别现有食材，优先基于图片或文字清单做菜谱决策]",
    "health": "[场景：健康问答，优先检索权威营养知识并触发健康护栏]",
}


def _apply_mode_prompt(message: str, mode: str) -> str:
    """把前端选择的决策场景转成后端可控的提示词前缀，而不是只把前缀当普通文本。"""
    prompt = MODE_PROMPTS.get((mode or "home").strip(), MODE_PROMPTS["home"])
    return f"{prompt}\n{message}"



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


@router.post("/chat")
async def chat(
    session_id: str = Form(...),
    message: str = Form(...),
    image: UploadFile | None = File(None),
    image_url: str | None = Form(None),
    mode: str = Form("home"),
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
    human_message = build_human_message(_apply_mode_prompt(message, mode), save_img_url)
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

        def _fill_images():
            """后台补图：对无图菜名搜成品图，25s 预算内逐个补，实时推 image 事件。"""
            deadline = time.time() + 25
            for index, recipe in enumerate(list(answer_dict.get("recipes") or [])):
                if time.time() > deadline or recipe.get("image_url"):
                    continue
                name = str(recipe.get("name") or "").strip()
                if not name:
                    continue
                try:
                    image_url, source = find_recipe_image(name)
                except Exception:
                    continue
                if not image_url:
                    continue
                ai_flag = source == "ai"
                with _img_lock:
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
                events.put(("item", ("image", {"index": index, "url": image_url, "ai_generated": ai_flag})))

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
                    break

                kind, payload = event
                if kind == "token":
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
                    # 卡片先出、图片后补：无图菜名交给后台线程（25s 预算），
                    # 搜到即推 image 事件让前端动态填图，不再阻塞 answer 120s。
                    if any(not r.get("image_url") for r in (answer_dict.get("recipes") or [])):
                        img_thread = threading.Thread(target=_fill_images, daemon=True)
                        img_thread.start()
                    # 先通知前端"正文说完了，正在整理卡片"，再推整包 JSON
                    yield f"data: {json.dumps({'structuring': True}, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'answer': answer_dict}, ensure_ascii=False)}\n\n"
                elif kind == "image":
                    yield f"data: {json.dumps({'image': payload}, ensure_ascii=False)}\n\n"

            # done 后给补图线程最多 25s：搜到图的重新序列化进落库与 finish 前最终态
            if img_thread is not None:
                img_thread.join(timeout=25)
                with _img_lock:
                    if answer_dict is not None:
                        final_answer = json.dumps(answer_dict, ensure_ascii=False)
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"
            return
        finally:
            # 无论正常完成、后端异常还是客户端断开（GeneratorExit），都兜底落库一次
            _persist_once()

        # 整轮正常结束（持久化已由 finally 完成，此处幂等）
        yield f"data: {json.dumps({'finish': True, 'session_id': session_id}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
