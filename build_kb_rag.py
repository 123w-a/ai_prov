"""标准 RAG 索引构建入口。

运行：
    uv run python build_kb_rag.py
    uv run python build_kb_rag.py --preview
    uv run python build_kb_rag.py --contextual     # 为每个 chunk 生成上下文前缀（需可用模型 key）
"""

from __future__ import annotations#直接写还没定义的类名

from argparse import ArgumentParser#让终端能控制你脚本的建表

from rag.ingest import build_index, self_test


def _make_contextual_llm():
    """从项目模型工厂构造一个注入式 LLM：llm(system, user) -> str。

    只在 --contextual 开启时调用；调用失败（无 key / 离线）会抛错，
    由 main 捕获后提示用户，不影响普通建库。
    """
    from langchain_core.messages import SystemMessage, HumanMessage

    from model_name import get_langchain_llm

    # 上下文标签（Contextual Retrieval）是建库期高频、低难度任务，
    # 默认用便宜的 deepseek 生成前缀，不占用贵的 gpt 额度。
    llm = get_langchain_llm("deepseek", temperature=0.3, max_tokens=80)

    def call(system: str, user: str) -> str:
        try:
            return llm.invoke(
                [SystemMessage(content=system), HumanMessage(content=user)]
            ).content
        except Exception:
            return ""

    return call


def main() -> None:
    parser = ArgumentParser(description="构建本地 RAG 知识库")
    parser.add_argument(
        "--preview",#在终端控制要不要输出清洗后的文本
        action="store_true",#决定是True还是False
        help="输出清洗后的文本到 resources/cleaned_preview",#键表时显示在终端
    )
    parser.add_argument(
        "--contextual",
        action="store_true",
        help="为每个 chunk 生成上下文前缀（Contextual Retrieval），需要可用的模型 key",
    )
    args = parser.parse_args()#接受终端给的信息

    contextual_llm = None
    if args.contextual:
        print("[rag] 加载查询转换/上下文 LLM ...")
        contextual_llm = _make_contextual_llm()

    result = build_index(
        preview=args.preview,
        contextual=args.contextual,
        contextual_llm=contextual_llm,
    )#建数据库
    self_test(result.store)#自检


if __name__ == "__main__":
    main()
