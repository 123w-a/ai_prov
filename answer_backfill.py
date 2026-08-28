"""历史纯文本回答的 AI 增强回填（CLI 工具）。

背景：旧链路部分回答是 markdown 源码长文（**加粗**、## 标题等），
前端 message-text 是纯文本插值渲染，星号井号会原样裸露给用户。

本工具确定性分类扫描 sessions/*.json，只对「完整长文建议」做一次
LLM 重写（去标记、段落化、要点前置，不改任何事实与数字），
原文备份到 answer_original 字段后回写。

分类规则（不动 AI）：
  - '__pending__' / 失败占位（'回答未能完成'）→ 跳过
  - len < 300（追问/短句）→ 跳过
  - 其余 → 待增强

用法：
  python answer_backfill.py             # dry-run：只列出将处理的记录
  python answer_backfill.py --apply     # 真正调用 LLM 重写并回写
"""
import argparse
import glob
import json

from model_name import get_langchain_llm, resolve_provider
from sessions_store import _lock, _read_session, _write_session

_MIN_LEN = 300
_SKIP_MARKS = ("回答未能完成",)


def classify_answer(answer: str) -> str:
    """返回 'pending' | 'short' | 'candidate'。确定性规则，单测覆盖。"""
    if not answer or answer == "__pending__":
        return "pending"
    if any(mark in answer for mark in _SKIP_MARKS):
        return "pending"
    return "candidate" if len(answer) >= _MIN_LEN else "short"


def scan_targets() -> list[dict]:
    """扫全部会话，返回待增强记录的定位信息（不读内容进内存过多）。"""
    targets = []
    for path in sorted(glob.glob("sessions/*.json")):
        try:
            data = json.loads(_read_file(path))
        except Exception:
            continue
        sid = data.get("session_id") or path
        for m in data.get("messages", []):
            answer = m.get("answer") or ""
            if classify_answer(answer) != "candidate":
                continue
            try:
                json.loads(answer)
                continue  # 已是结构化卡片
            except Exception:
                pass
            targets.append(
                {"sid": sid, "rec_id": m["id"], "len": len(answer),
                 "user_text": (m.get("user_text") or "")[:30]}
            )
    return targets


def _read_file(path: str) -> str:
    from pathlib import Path
    return Path(path).read_text(encoding="utf-8")


_PROMPT = (
    "你是文本排版助手。把这段膳食建议重写成无任何 markdown 标记的中文纯文本："
    "去掉所有 **、##、-、|、表格符号；结论放最前；用自然段落和小标题式短句组织；"
    "要点用『一是…二是…』或『1) 2)』衔接；严禁改动任何事实、数字、菜名、价格；"
    "保留全部信息量，不要新增内容。只输出重写后的文本。\n\n原文：\n{source}"
)


def enhance_text(source: str) -> str:
    """调用 LLM 重写一段 markdown 长文为卡片友好纯文本。"""
    llm = get_langchain_llm(resolve_provider(), temperature=0.2, max_tokens=1600)
    response = llm.invoke(_PROMPT.format(source=source))
    return str(response.content).strip()


def write_back(sid: str, rec_id: int, new_answer: str) -> bool:
    """锁内回写：answer 换新文本，原文备份 answer_original（只备份一次）。"""
    with _lock:
        data = _read_session(sid)
        if data is None:
            return False
        for m in data["messages"]:
            if m.get("id") != rec_id:
                continue
            m.setdefault("answer_original", m.get("answer"))
            m["answer"] = new_answer
            _write_session(data)
            return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="历史纯文本回答 AI 增强回填")
    parser.add_argument("--apply", action="store_true", help="真正重写并回写（缺省 dry-run）")
    args = parser.parse_args()

    targets = scan_targets()
    if not targets:
        print("没有需要增强的纯文本长回答。")
        return
    print(f"待增强 {len(targets)} 条：")
    for t in targets:
        print(f"  {t['sid']} rec{t['rec_id']} len={t['len']} Q={t['user_text']}")
    if not args.apply:
        print("（dry-run，加 --apply 执行重写回写）")
        return

    import sessions_store

    for t in targets:
        data = sessions_store._read_session(t["sid"])
        record = next(
            (m for m in (data or {}).get("messages") or [] if m.get("id") == t["rec_id"]),
            None,
        )
        if record is None:
            continue
        print(f"[增强] {t['sid']} rec{t['rec_id']} ...", flush=True)
        try:
            rewritten = enhance_text(record["answer"])
        except Exception as exc:
            print(f"  失败：{exc}")
            continue
        if write_back(t["sid"], t["rec_id"], rewritten):
            print(f"  完成：{len(record['answer'])} -> {len(rewritten)} 字（原文已备份）")


if __name__ == "__main__":
    main()
