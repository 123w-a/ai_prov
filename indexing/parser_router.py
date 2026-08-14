"""
indexing/parser_router.py — PDF 解析器分层路由（确定性规则，无 LLM）

设计原则：
  - 解析器选择是文件的「结构属性」，不是推理任务 → 用确定性规则路由，不用 LLM
  - 策略模式（BaseParser 接口）收口复杂度，下游 chunker / 向量库不感知解析器
  - 路由结果透明展示给用户 + Human-in-the-loop 确认入库

两层解析器：
  SimplePDFParser   — pdfplumber / PyMuPDF（本地·免费·脱敏友好）
  LlamaParseParser — LlamaParse Cloud API（付费·图文混排/扫描件）

探测规则（route() 函数）：
  ① 扫描件判定：每页提取字符数 < 阈值 → 判为扫描件（需 OCR 类解析器）
  ② 页数阈值：小文件（≤N 页）走便宜解析器
  ③ 图文密度：PDF 内图片/XObject 数量超过阈值 → 图文混排
  ④ 表格密度：检测到复杂表格结构 → 倾向强解析器

使用方式：
  from indexing.parser_router import route, SimplePDFParser

  result = route("path/to/file.pdf")       # 返回 RoutingDecision
  parser = result.parser_class()            # 实例化推荐解析器
  parsed = parser.parse("path/to/file.pdf") # 得到 ParsedDoc（归一化输出）
"""

from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


# ============================================================
# 数据结构
# ============================================================

class ParserType(Enum):
    SIMPLE = "simple"        # pdfplumber / PyMuPDF（免费）
    ADVANCED = "advanced"    # LlamaParse / Doc Intelligence（付费）


@dataclass
class ParsedDoc:
    """归一化解析输出——所有 Parser 必须返回此结构。"""
    text: str                           # 提取的纯文本
    metadata: dict = field(default_factory=dict)
    # 可选字段（各 Parser 尽量填充）：
    #   page_count: int          总页数
    #   source_file: str         原始文件名
    #   parser_used: str         实际使用的解析器名称
    #   extraction_method: str   "text" | "ocr" | "mixed"
    #   table_count: int         检测到的表格数
    #   image_count: int         检测到的图片数
    #   has_scanned_pages: bool  是否包含扫描页
    #   confidence: float        解析置信度 0-1（可选）

    def __post_init__(self):
        self.metadata.setdefault("parser_used", "unknown")


@dataclass
class RoutingDecision:
    """路由决策结果。"""
    parser_type: ParserType              # 推荐用哪个解析器
    parser_class: type                   # 对应的 Parser 类（可直接实例化）
    reason: str                          # 为什么这么选（给用户看）
    details: dict = field(default_factory=dict)  # 探测原始数据（调试/展示用）
    can_override: bool = True            # 用户是否可手动覆盖


# ============================================================
# 探测规则配置（可调阈值）
# ============================================================

@dataclass
class DetectionThresholds:
    """探测规则的阈值参数——按需调整，无需改代码逻辑。"""

    # --- ① 扫描件判定 ---
    # 每页平均字符数低于此值 → 判为扫描件（纯文字 PDF 通常 > 500 字符/页）
    SCAN_CHARS_PER_PAGE_MIN: float = 80.0
    # 抽样检测的页数（避免大文件全扫）
    SCAN_SAMPLE_PAGES: int = 3

    # --- ② 页数阈值 ---
    # 页数 ≤ 此值 → 默认走简单解析器（小文件不值得上重武器）
    SMALL_FILE_PAGES: int = 10

    # --- ③ 图文密度 ---
    # 图片/XObject 数量 ≥ 此值 → 判为图文混排
    IMAGE_DENSITY_THRESHOLD: int = 5
    # 图片占比（图片数/总页数）≥ 此值 → 图文密集
    IMAGE_RATIO_THRESHOLD: float = 0.3

    # --- ④ 表格密度 ---
    # 表格数量 ≥ 此值 → 可能有复杂表格结构
    TABLE_COUNT_THRESHOLD: int = 3

    # --- 综合评分 ---
    # 加权分 ≥ 此值 → 强制走高级解析器
    FORCE_ADVANCED_SCORE: float = 2.0


# 全局默认阈值（可通过环境变量或构造函数覆盖）
DEFAULT_THRESHOLDS = DetectionThresholds()


# ============================================================
# 抽象基类
# ============================================================

class BaseParser(ABC):
    """解析器统一接口。所有具体解析器必须实现此接口。"""

    @abstractmethod
    def parse(self, file_path: str) -> ParsedDoc:
        """
        解析文件，返回归一化的 ParsedDoc。

        Args:
            file_path: 文件路径（PDF / Word 等）

        Returns:
            ParsedDoc: 归一化解析结果

        Raises:
            FileNotFoundError: 文件不存在
            ValueError: 文件格式不支持
            RuntimeError: 解析失败（网络错误、API key 缺失等）
        """
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """检查此解析器当前是否可用（依赖已装？API key 已配？）。"""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """解析器人类可读名称。"""
        ...

    @property
    @abstractmethod
    def cost_type(self) -> str:
        """成本类型："free" / "api_paid" / "local_gpu"。"""
        ...


# ============================================================
# 具体实现 1：SimplePDFParser（免费、本地）
# ============================================================

class SimplePDFParser(BaseParser):
    """
    简单 PDF 解析器 —— 使用 pdfplumber 或 PyMuPDF（fitz）。

    特点：
      - 本地运行，零 API 成本
      - 适合：纯文字 PDF、简单表格、标准排版文档
      - 不适合：扫描件（无 OCR）、复杂图文混排
      - 隐私利好：文件不出本机
    """

    def __init__(self, backend: str = "auto"):
        """
        Args:
            backend: "auto" | "pdfplumber" | "pymupdf"
                     auto = 优先 pdfplumber，不可用则降级 PyMuPDF
        """
        self._backend = backend
        self._engine = None  # lazy load

    def _get_engine(self):
        if self._engine is not None:
            return self._engine

        preferred = self._backend if self._backend != "auto" else "pdfplumber"

        if preferred == "pdfplumber":
            try:
                import pdfplumber
                self._engine = "pdfplumber"
                return pdfplumber
            except ImportError:
                pass

        # 降级 PyMuPDF
        try:
            import fitz  # PyMuPDF
            self._engine = "pymupdf"
            return fitz
        except ImportError:
            raise ImportError(
                "SimplePDFParser 需要 pdfplumber 或 PyMuPDF (fitz) 之一。"
                " 安装: pip install pdfplumber  或  pip install pymupdf"
            )

    @property
    def name(self) -> str:
        engine = getattr(self, "_engine", None) or self._backend
        return f"SimplePDF({engine})"

    @property
    def cost_type(self) -> str:
        return "free"

    def is_available(self) -> bool:
        try:
            self._get_engine()
            return True
        except ImportError:
            return False

    def parse(self, file_path: str) -> ParsedDoc:
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"文件不存在: {file_path}")

        engine = self._get_engine()

        if self._engine == "pdfplumber":
            text, meta = self._parse_pdfplumber(engine, file_path)
        else:
            text, meta = self._parse_pymupdf(engine, file_path)

        meta["source_file"] = os.path.basename(file_path)
        meta["parser_used"] = self.name

        return ParsedDoc(text=text, metadata=meta)

    def _parse_pdfplumber(self, pdfplumber, file_path: str):
        """用 pdfplumber 提取文本 + 元数据。"""
        all_text = []
        page_count = 0
        table_count = 0
        chars_per_page = []

        with pdfplumber.open(file_path) as pdf:
            page_count = len(pdf.pages)
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                all_text.append(page_text)
                chars_per_page.append(len(page_text.strip()))
                tables = page.extract_tables() or []
                table_count += len(tables)

        text = "\n\n".join(all_text)
        avg_chars = sum(chars_per_page) / max(len(chars_per_page), 1)

        meta = {
            "page_count": page_count,
            "table_count": table_count,
            "avg_chars_per_page": round(avg_chars, 1),
            "extraction_method": "text",
            "has_scanned_pages": avg_chars < DEFAULT_THRESHOLDS.SCAN_CHARS_PER_PAGE_MIN,
        }
        return text, meta

    def _parse_pymupdf(self, fitz, file_path: str):
        """用 PyMuPDF (fitz) 提取文本 + 元数据。"""
        doc = fitz.open(file_path)
        all_text = []
        page_count = len(doc)
        chars_per_page = []

        for page in doc:
            page_text = page.get_text()
            all_text.append(page_text)
            chars_per_page.append(len(page_text.strip()))

        doc.close()
        text = "\n\n".join(all_text)
        avg_chars = sum(chars_per_page) / max(len(chars_per_page), 1)

        meta = {
            "page_count": page_count,
            "table_count": 0,  # PyMuPDF 表格检测需额外处理，暂填 0
            "avg_chars_per_page": round(avg_chars, 1),
            "extraction_method": "text",
            "has_scanned_pages": avg_chars < DEFAULT_THRESHOLDS.SCAN_CHARS_PER_PAGE_MIN,
        }
        return text, meta


# ============================================================
# 具体实现 2：LlamaParseParser（付费、云端）
# ============================================================

class LlamaParseParser(BaseParser):
    """
    高级 PDF 解析器 —— 使用 LlamaIndex LlamaParse Cloud API。

    特点：
      - 云端 OCR + 版面理解，适合扫描件和图文混排
      - 按 页 收费（有免费额度）
      - 需要 LLAMA_CLOUD_API_KEY 环境变量
      - 输出带版面结构的 Markdown（保留标题层级、表格、图片位置）
    """

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or os.getenv("LLAMA_CLOUD_API_KEY", "")

    @property
    def name(self) -> str:
        return "LlamaParse(Cloud)"

    @property
    def cost_type(self) -> str:
        return "api_paid"

    def is_available(self) -> bool:
        if not self._api_key:
            return False
        try:
            from llama_parse import LlamaParse
            return True
        except ImportError:
            return False

    def parse(self, file_path: str, LlamaParse=None) -> ParsedDoc:
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"文件不存在: {file_path}")
        if not self._api_key:
            raise RuntimeError(
                "LlamaParse 需要 LLAMA_CLOUD_API_KEY 环境变量。"
                " 获取: https://cloud.llamaindex.ai/"
            )

        from llama_parse import LlamaParse#文档预处理解析工具

        parser = LlamaParse(
            api_key=self._api_key,
            result_type="markdown",     # 输出 Markdown 格式（方便后续 chunker 处理）
            use_vendor_parsing=True,    # 启用供应商增强解析（更好 OCR）
            language="chinese",         # 中文文档优化
        )
        results = parser.load_data(file_path)

        # 合并所有页面为单一文本
        text_parts = []
        total_images = 0
        for result in results:
            text_parts.append(result.text or "")
            # LlamaParse 可能返回图片信息
            if hasattr(result, "images"):
                total_images += len(result.images) if result.images else 0

        text = "\n\n".join(text_parts)

        # 粗略估算页数（基于结果数或换行分割）
        page_estimate = len(results)

        meta = {
            "source_file": os.path.basename(file_path),
            "page_count": page_estimate,
            "parser_used": self.name,
            "extraction_method": "ocr_mixed",
            "image_count": total_images,
            "confidence": 0.9,  # LlamaParse 对复杂文档置信度较高
        }
        return ParsedDoc(text=text, metadata=meta)


# ============================================================
# 核心确定性路由函数（无 LLM）
# ============================================================

def route(
    file_path: str,
    thresholds: Optional[DetectionThresholds] = None,
    force_parser: Optional[ParserType] = None,
) -> RoutingDecision:
    """
    确定性路由：根据文件结构特征决定用哪个解析器。

    不调用任何 LLM。全部基于轻量文件探测（毫秒级）。

    规则优先级（从高到低）：
      1. force_parser 参数手动指定 → 直接返回，跳过探测
      2. 扫描件检测（每页字符数极低）→ 强制走高级
      3. 综合加权评分（图文密度 + 表格密度 + 页数）→ 分高走高级
      4. 默认 → 走简单解析器（省钱）

    Args:
        file_path: 待解析的 PDF 文件路径
        thresholds: 自定义探测阈值（None = 用默认值）
        force_parser: 手动强制指定解析器（用户覆盖时使用）

    Returns:
        RoutingDecision: 包含推荐的 Parser 类型、原因、探测详情
    """
    t = thresholds or DEFAULT_THRESHOLDS

    # ---- 手动覆盖优先 ----
    if force_parser == ParserType.SIMPLE:
        return RoutingDecision(
            parser_type=ParserType.SIMPLE,
            parser_class=SimplePDFParser,
            reason="用户手动指定：使用简单解析器（本地免费）",
            details={"override": True},
        )
    if force_parser == ParserType.ADVANCED:
        return RoutingDecision(
            parser_type=ParserType.ADVANCED,
            parser_class=LlamaParseParser,
            reason="用户手动指定：使用高级解析器（LlamaParse Cloud）",
            details={"override": True},
        )

    # ---- 基础校验 ----
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"路由探测失败：文件不存在: {file_path}")

    fname = os.path.basename(file_path)
    ext = os.path.splitext(fname)[1].lower()
    if ext not in (".pdf"):
        # 非 PDF 文件默认走简单解析（markdown/txt 等）
        return RoutingDecision(
            parser_type=ParserType.SIMPLE,
            parser_class=SimplePDFParser,
            reason=f"非 PDF 文件（.{ext}），使用简单文本解析",
            details={"ext": ext},
        )

    # ---- 探测阶段（轻量，不加载全文内容进内存）----
    details = _probe_pdf_structure(file_path, t)
    score = 0.0
    reasons = []

    # 规则 ①：扫描件判定（最高优先级）
    if details.get("is_scanned", False):
        score += 1.5
        reasons.append(
            f"检测到扫描件"
            f"（平均每页仅 {details['avg_chars_per_page']:.0f} 字符"
            f" < 阈值 {t.SCAN_CHARS_PER_PAGE_MIN}）"
        )

    # 规则 ②：页数（大文件更值得用好解析器）
    page_count = details.get("page_count", 0)
    if page_count > t.SMALL_FILE_PAGES:
        score += 0.3
        reasons.append(f"文件较大（{page_count} 页 > {t.SMALL_FILE_PAGES} 页阈值）")

    # 规则 ③：图文密度
    img_count = details.get("image_count", 0)
    if img_count >= t.IMAGE_DENSITY_THRESHOLD:
        score += 0.8
        reasons.append(
            f"图文密集（{img_count} 张图片"
            f" >= 阈值 {t.IMAGE_DENSITY_THRESHOLD}）"
        )

    # 规则 ④：表格密度
    tbl_count = details.get("table_count", 0)
    if tbl_count >= t.TABLE_COUNT_THRESHOLD:
        score += 0.5
        reasons.append(
            f"含复杂表格（{tbl_count} 个"
            f" >= 阈值 {t.TABLE_COUNT_THRESHOLD}）"
        )

    # ---- 决策 ----
    if score >= t.FORCE_ADVANCED_SCORE:
        decision = RoutingDecision(
            parser_type=ParserType.ADVANCED,
            parser_class=LlamaParseParser,
            reason=f"建议使用高级解析器（综合评分={score:.1f}>=阈值{t.FORCE_ADVANCED_SCORE}"
                  f"；原因：{'；'.join(reasons) if reasons else '综合评估'}）",
            details=details,
        )
    else:
        decision = RoutingDecision(
            parser_type=ParserType.SIMPLE,
            parser_class=SimplePDFParser,
            reason=f"使用简单解析器即可（综合评分={score:.1f}<阈值{t.FORCE_ADVANCED_SCORE}"
                  f"；文件结构较简单，省成本）",
            details=details,
        )

    # 把评分也写进 details 方便展示
    details["_routing_score"] = round(score, 2)
    details["_routing_reasons"] = reasons

    # ---- 可用性兜底：推荐的解析器若实际不可用，降级到可用的 ----
    recommended = decision.parser_class()
    if recommended.is_available():
        return decision

    # 高级解析器不可用（多半缺 LLAMA_CLOUD_API_KEY）→ 降级简单解析器
    if decision.parser_type == ParserType.ADVANCED:
        simple = SimplePDFParser()
        if simple.is_available():
            return RoutingDecision(
                parser_type=ParserType.SIMPLE,
                parser_class=SimplePDFParser,
                reason=f"{decision.reason}；但高级解析器不可用（缺依赖/API key），"
                       f"已降级为本地简单解析器（免费·脱敏）",
                details=details,
            )
        # 连简单解析器依赖都没有：明确报错原因，但仍返回 simple 让 parse() 抛清晰异常
        return RoutingDecision(
            parser_type=ParserType.SIMPLE,
            parser_class=SimplePDFParser,
            reason="未检测到任何 PDF 解析依赖（请 pip install pdfplumber 或 pymupdf）；"
                   "将尝试本地简单解析器，若失败请先安装依赖",
            details=details,
        )

    return decision


def _probe_pdf_structure(file_path: str, t: DetectionThresholds) -> dict:
    """
    轻量 PDF 结构探测（不提取全文，只抽样检测）。

    返回探测原始数据字典，供 route() 决策和用户展示。
    """
    details = {
        "page_count": 0,
        "avg_chars_per_page": 0,
        "image_count": 0,
        "table_count": 0,
        "is_scanned": False,
        "file_size_kb": 0,
        "engine_ok": False,   # 是否有任何本地引擎成功读到内容
    }

    # 文件大小
    details["file_size_kb"] = round(os.path.getsize(file_path) / 1024, 1)

    # --- 策略：尝试多种引擎做轻量探测 ---
    # 优先用 PyMuPDF（快、能探图片），降级 pdfplumber（能探表格）

    # 尝试 1：PyMuPDF 探测页数 + 图片
    try:
        import fitz
        doc = fitz.open(file_path)
        details["page_count"] = len(doc)
        details["engine_ok"] = True

        # 抽样检测字符数（只读前 N 页或随机采样页）
        sample_indices = _sample_indices(len(doc), t.SCAN_SAMPLE_PAGES)
        char_counts = []
        for idx in sample_indices:
            page = doc[idx]
            char_counts.append(len(page.get_text().strip()))
            # 统计图片
            details["image_count"] += len(page.get_images())

        details["avg_chars_per_page"] = (
            sum(char_counts) / max(len(char_counts), 1)
        )
        doc.close()
    except ImportError:
        pass

    # 尝试 2：pdfplumber 探测表格（如果上面没拿到页数/字符）
    try:
        import pdfplumber
        if details["page_count"] == 0:
            with pdfplumber.open(file_path) as pdf:
                details["page_count"] = len(pdf.pages)
                details["engine_ok"] = True
                sample_indices = _sample_indices(
                    len(pdf.pages), t.SCAN_SAMPLE_PAGES
                )
                char_counts = []
                for idx in sample_indices:
                    page = pdf.pages[idx]
                    text = page.extract_text() or ""
                    char_counts.append(len(text.strip()))
                    tables = page.extract_tables() or []
                    details["table_count"] += len(tables)
                if char_counts:
                    details["avg_chars_per_page"] = (
                        sum(char_counts) / len(char_counts)
                    )
    except ImportError:
        pass

    # 只有引擎成功读到内容，才判定扫描件；
    # 两引擎都不可用 → 字符数为 0 是「未知」，不能误判成「扫描件」
    details["is_scanned"] = (
        details["engine_ok"]
        and details["avg_chars_per_page"] < t.SCAN_CHARS_PER_PAGE_MIN
    )
    return details


def _sample_indices(total: int, sample_n: int) -> list[int]:
    """生成抽样页索引（均匀分布：首尾+中间）。"""
    if total <= sample_n:
        return list(range(total))
    indices = [0]  # 第一页
    step = max(1, (total - 2) // (sample_n - 1))
    i = step
    while i < total - 1 and len(indices) < sample_n - 1:
        indices.append(i)
        i += step
    indices.append(total - 1)  # 最后一页
    return indices


# ============================================================
# 便捷函数：一步完成路由 + 解析
# ============================================================

def parse_with_routing(
    file_path: str,
    force_parser: Optional[ParserType] = None,
    thresholds: Optional[DetectionThresholds] = None,
) -> tuple[ParsedDoc, RoutingDecision]:
    """
    一步完成：路由决策 → 实例化解析器 → 解析文件。

    Returns:
        (ParsedDoc, RoutingDecision): 解析结果 + 路由决策（用于展示/日志）
    """
    decision = route(file_path, thresholds=thresholds, force_parser=force_parser)
    parser = decision.parser_class()
    parsed = parser.parse(file_path)
    return parsed, decision


# ============================================================
# CLI 快速测试
# ============================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python parser_router.py <pdf文件路径>")
        print("\n示例:")
        print("  python parser_router.py docs/guide2022.pdf")
        print("  python parser_router.py docs/scanned_menu.pdf")
        sys.exit(1)

    target = sys.argv[1]
    print(f"[parser_router] 探测文件: {target}")
    print("-" * 50)

    decision = route(target)
    print(f"推荐解析器: {decision.parser_type.value}")
    print(f"原因: {decision.reason}")
    print(f"\n探测详情:")
    for k, v in decision.details.items():
        print(f"  {k}: {v}")
    print(f"\n可用性检查:")

    for cls in [SimplePDFParser, LlamaParseParser]:
        p = cls()
        avail = "✓" if p.is_available() else "✗ (缺依赖/API key)"
        print(f"  {p.name}: {avail}  [{p.cost_type}]")

    # 如果用户确认，直接解析
    print(f"\n如需解析，调用: parse_with_routing('{target}')")
