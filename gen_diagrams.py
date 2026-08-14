# -*- coding: utf-8 -*-
"""把《理解.md》里的 9 张 Mermaid 图重写为高对比 SVG 图片文件。
纯离线、不依赖任何外部库/网络。配色遵循用户可读性偏好：高饱和、大字号、强对比。
"""
import os

OUT = r"C:\Users\31834\Desktop\理解_图"

# 配色（与文档 mermaid classDef 一致）
C = {
    "blue":   ("#2563EB", "#1E3A8A", "#FFFFFF"),
    "amber":  ("#D97706", "#92400E", "#FFFFFF"),
    "green":  ("#16A34A", "#14532D", "#FFFFFF"),
    "red":    ("#DC2626", "#7F1D1D", "#FFFFFF"),
    "purple": ("#7C3AED", "#4C1D95", "#FFFFFF"),
    "gray":   ("#E2E8F0", "#64748B", "#0F172A"),
}

FONT = "Microsoft YaHei, PingFang SC, Hiragino Sans GB, sans-serif"


class Node:
    def __init__(self, id, label, x, y, color="blue", shape="box", w=None, h=None, tc=None):
        self.id = id
        self.label = label
        self.x = x
        self.y = y
        self.color = color
        self.shape = shape  # box | diamond
        self.w = w or (210 if shape == "box" else 180)
        self.h = h or (64 if shape == "box" else 96)
        self.tc = tc  # text color override

    @property
    def cx(self):
        return self.x + self.w / 2

    @property
    def cy(self):
        return self.y + self.h / 2


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def wrap_lines(label):
    # 支持显式 \n 换行；同时也按宽度软折行
    out = []
    for part in label.split("\n"):
        out.append(part)
    return out


def draw_node(n):
    fill, stroke, tcol = C[n.color]
    if n.tc:
        tcol = n.tc
    if n.shape == "diamond":
        # 菱形：四点
        pts = f"{n.cx},{n.y} {n.x+n.w},{n.cy} {n.cx},{n.y+n.h} {n.x},{n.cy}"
        shape = f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="2.5"/>'
    else:
        shape = (f'<rect x="{n.x}" y="{n.y}" width="{n.w}" height="{n.h}" rx="10" ry="10" '
                 f'fill="{fill}" stroke="{stroke}" stroke-width="2.5"/>')
    # 文本（多行居中）
    lines = wrap_lines(n.label)
    fs = 16 if len(lines) <= 2 else 14
    lh = fs + 5
    total = lh * len(lines)
    start_y = n.cy - total / 2 + lh / 2
    tspans = ""
    for i, ln in enumerate(lines):
        y = start_y + i * lh
        tspans += f'<tspan x="{n.cx}" y="{y}">{esc(ln)}</tspan>'
    text = (f'<text font-family="{FONT}" font-size="{fs}" font-weight="700" '
            f'fill="{tcol}" text-anchor="middle">{tspans}</text>')
    return shape + text


def edge_points(src, dst):
    dx = dst.cx - src.cx
    dy = dst.cy - src.cy
    if abs(dy) >= abs(dx):
        if dy >= 0:
            return (src.cx, src.y + src.h), (dst.cx, dst.y)
        else:
            return (src.cx, src.y), (dst.cx, dst.y + dst.h)
    else:
        if dx >= 0:
            return (src.x + src.w, src.cy), (dst.x, dst.cy)
        else:
            return (src.x, src.cy), (dst.x + dst.w, dst.cy)


def draw_edge(src, dst, label="", dashed=False, color="#475569", ecolor=None):
    (x1, y1), (x2, y2) = edge_points(src, dst)
    stroke = ecolor or color
    dash = ' stroke-dasharray="7,5"' if dashed else ""
    arrow = f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{stroke}" stroke-width="2.5"{dash} marker-end="url(#arrow)"/>'
    if label:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        # 白底标签
        w = len(label) * 13 + 10
        lab = (f'<rect x="{mx-w/2}" y="{my-12}" width="{w}" height="20" rx="5" fill="#FFFFFF" stroke="{stroke}" stroke-width="1"/>'
               f'<text x="{mx}" y="{my+3}" font-family="{FONT}" font-size="13" font-weight="700" '
               f'fill="{stroke}" text-anchor="middle">{esc(label)}</text>')
        return arrow + lab
    return arrow


def build(spec):
    nodes = {n.id: n for n in spec["nodes"]}
    # 计算画布边界
    xs = [n.x for n in nodes.values()] + [n.x + n.w for n in nodes.values()]
    ys = [n.y for n in nodes.values()] + [n.y + n.h for n in nodes.values()]
    pad = 30
    minx, maxx = min(xs) - pad, max(xs) + pad
    miny, maxy = min(ys) - pad, max(ys) + pad
    W = int(maxx - minx)
    H = int(maxy - miny)
    body = ""
    # 背景
    body += f'<rect x="{minx}" y="{miny}" width="{W}" height="{H}" fill="#FFFFFF"/>'
    # 先画边
    for e in spec["edges"]:
        s = nodes[e[0]]; d = nodes[e[1]]
        kw = e[2] if len(e) > 2 else {}
        body += draw_edge(s, d, label=kw.get("label", ""), dashed=kw.get("dashed", False),
                          color=kw.get("color", "#475569"), ecolor=kw.get("ecolor"))
    # 再画节点（盖在边上）
    for n in spec["nodes"]:
        body += draw_node(n)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
           f'viewBox="{minx} {miny} {W} {H}">'
           f'<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" '
           f'orient="auto" markerUnits="strokeWidth"><path d="M0,0 L8,3 L0,6 Z" fill="#475569"/></marker></defs>'
           f'{body}</svg>')
    return svg


def save(name, svg):
    p = os.path.join(OUT, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(svg)
    print("wrote", p, len(svg), "bytes")


# ============ 图1 · 全局后端链路 ============
def fig1():
    nodes = [
        Node("U", "用户：文字 / 图片", 300, 20, "gray"),
        Node("CR", "chat_route.py\nPOST /api/chat", 280, 110, "blue"),
        Node("IMG", "有图片？", 330, 210, "amber", "diamond"),
        Node("OSS", "oss_utils\n上传 OSS 拿 URL", 560, 200, "purple"),
        Node("BM", "main.py\nbuild_human_message\n注入偏好+图文拼装", 250, 320, "blue"),
        Node("SA", "main.py\nstream_agent", 300, 430, "blue"),
        Node("AG", "agent_graph.py\nLangGraph 图", 300, 530, "red"),
        Node("SSE", "SSE 事件流\nworking→token→structuring→answer→finish", 270, 640, "amber"),
        Node("SR", "sessions_store\nappend_message 落 JSON", 270, 750, "green"),
        Node("CK", "checkpoint.db\n(断点记忆)", 620, 560, "gray"),
    ]
    edges = [
        ("U", "CR"),
        ("CR", "IMG"),
        ("IMG", "OSS", {"label": "是"}),
        ("IMG", "BM", {"label": "否"}),
        ("OSS", "BM"),
        ("BM", "SA"),
        ("SA", "AG"),
        ("AG", "SSE"),
        ("SSE", "SR"),
        ("AG", "CK", {"dashed": True, "ecolor": "#64748B", "label": "断点记忆"}),
    ]
    return {"nodes": nodes, "edges": edges}


# ============ 图2 · 两套存储解耦 ============
def fig2():
    nodes = [
        Node("AJ", "sessions/{sid}.json\n业务聊天历史(可展示)", 60, 120, "green", w=240, h=90),
        Node("BJ", "resources/checkpoint.db\nAgent 循环断点(图状态)", 560, 120, "blue", w=260, h=90),
        Node("N", "thread_id == session_id\n纽带", 360, 280, "amber", w=240, h=80),
    ]
    edges = [
        ("AJ", "N", {"dashed": True, "ecolor": "#64748B"}),
        ("BJ", "N", {"dashed": True, "ecolor": "#64748B"}),
    ]
    return {"nodes": nodes, "edges": edges}


# ============ 图3 · LangGraph 节点流转 ============
def fig3():
    nodes = [
        Node("C", "condense_history\n长对话压缩", 320, 20, "gray"),
        Node("T", "chef_think\n(LLM 推理)", 320, 120, "red"),
        Node("R", "run_tools\n(ToolNode)", 600, 120, "purple"),
        Node("V", "verify_answer\n(L3 硬护栏)", 320, 250, "amber"),
        Node("ST", "structure_answer\n(结构化卡片)", 320, 360, "green"),
        Node("E", "END", 330, 470, "gray", w=180, h=56),
        Node("CK", "checkpoint.db", 620, 250, "gray"),
    ]
    edges = [
        ("C", "T"),
        ("T", "R", {"label": "调了工具"}),
        ("R", "T", {"label": "工具结果返回"}),
        ("T", "V", {"label": "不再调工具"}),
        ("V", "T", {"dashed": True, "ecolor": "#DC2626", "label": "打回 retry ≤3"}),
        ("V", "ST", {"label": "通过"}),
        ("ST", "E"),
        ("T", "CK", {"dashed": True, "ecolor": "#64748B"}),
        ("R", "CK", {"dashed": True, "ecolor": "#64748B"}),
    ]
    return {"nodes": nodes, "edges": edges}


# ============ 图4 · 三层健康护栏 ============
def fig4():
    nodes = [
        Node("IN", "用户请求 / chef_think 产出", 330, 20, "gray", w=260, h=64),
        Node("L1", "L1 提示词软约束\nagent_prompts.py", 80, 160, "blue", w=240, h=80),
        Node("L2", "L2 RAG 检索依据\nrag/", 340, 160, "purple", w=240, h=80),
        Node("L3", "L3 确定性规则硬审计\nnutrition_rules.py.audit()", 600, 160, "red", w=270, h=80),
        Node("OUT", "verify_answer_node\n放行 or 打回", 330, 300, "amber", w=260, h=70),
    ]
    edges = [
        ("IN", "L1"), ("IN", "L2"), ("IN", "L3"),
        ("L1", "OUT"), ("L2", "OUT"), ("L3", "OUT"),
    ]
    return {"nodes": nodes, "edges": edges}


# ============ 图5 · RAG 混合检索流水线 ============
def fig5():
    nodes = [
        Node("Q", "用户查询", 340, 20, "gray"),
        Node("QT", "查询转换\n(HyDE / 多查询)", 320, 110, "amber"),
        Node("D", "稠密向量检索", 300, 210, "blue"),
        Node("B", "BM25 字面检索", 560, 210, "blue"),
        Node("F", "RRF 融合\n(BM25 + 向量)", 320, 310, "amber"),
        Node("M", "元数据过滤", 330, 410, "blue"),
        Node("RR", "CrossEncoder 重排", 310, 510, "purple"),
        Node("HITS", "Top-K 命中\n返回 SearchResult", 310, 610, "green", w=230, h=70),
    ]
    edges = [
        ("Q", "QT"),
        ("QT", "D"), ("QT", "B"),
        ("D", "F"), ("B", "F"),
        ("F", "M"),
        ("M", "RR"),
        ("RR", "HITS"),
    ]
    return {"nodes": nodes, "edges": edges}


# ============ 图6 · SSE 流式事件时序 ============
def fig6():
    # 用纵向步骤列表模拟时序
    lanes = ["前端", "chat_route", "agent_graph", "LLM/tools", "sessions"]
    step_w = 720
    x0 = 120
    y0 = 40
    gap = 56
    steps = [
        ("前端", "chat_route", "POST /api/chat", "blue"),
        ("chat_route", "agent_graph", "agent.stream(stream_mode=\"messages\")", "blue"),
        ("agent_graph", "LLM/tools", "LLM 流式 token", "purple"),
        ("agent_graph", "前端", "event: token", "amber"),
        ("agent_graph", "LLM/tools", "工具调用 (run_tools)", "purple"),
        ("agent_graph", "前端", "event: structuring", "amber"),
        ("agent_graph", "LLM/tools", "结构化生成", "purple"),
        ("agent_graph", "前端", "event: answer (ChefAnswer JSON)", "amber"),
        ("agent_graph", "sessions", "append_message 落库", "green"),
        ("agent_graph", "前端", "event: finish", "amber"),
    ]
    nodes = []
    edges = []
    # 车道路标
    for i, ln in enumerate(lanes):
        nodes.append(Node(f"L{i}", ln, x0 + i * (step_w / len(lanes)), y0 - 30, "gray", w=int(step_w/len(lanes)) - 8, h=34, tc="#0F172A"))
    for i, (a, b, lab, col) in enumerate(steps):
        y = y0 + i * gap
        nodes.append(Node(f"S{i}", lab, x0 + 20, y, col, w=step_w - 40, h=40))
        # 指向：从 a 车道到 b 车道的小箭头（顶部）
        ai = lanes.index(a); bi = lanes.index(b)
        # 画一条细箭头从 a 列到 b 列（在同一行上方）
        xa = x0 + ai * (step_w/len(lanes)) + (step_w/len(lanes))/2
        xb = x0 + bi * (step_w/len(lanes)) + (step_w/len(lanes))/2
        edges.append((f"_L{i}_a", f"_L{i}_b", "", False, "#94A3B8"))
        # 用虚拟节点实现箭头
    # 用简单方式：直接在 steps 行上方画车道间箭头
    body_extra = ""
    for i, (a, b, lab, col) in enumerate(steps):
        y = y0 + i * gap
        ai = lanes.index(a); bi = lanes.index(b)
        xa = x0 + ai * (step_w/len(lanes)) + (step_w/len(lanes))/2
        xb = x0 + bi * (step_w/len(lanes)) + (step_w/len(lanes))/2
        yy = y - 6
        if xa != xb:
            body_extra += (f'<line x1="{xa}" y1="{yy}" x2="{xb}" y2="{yy}" stroke="#94A3B8" stroke-width="2" '
                           f'marker-end="url(#arrow)"/>')
        else:
            body_extra += f'<circle cx="{xa}" cy="{yy}" r="3" fill="#94A3B8"/>'
    spec = {"nodes": nodes, "edges": [], "_extra": body_extra}
    return spec


# ============ 图7 · Provider 三级优先级决策树 ============
def fig7():
    nodes = [
        Node("S", "resolve_provider()", 360, 20, "amber", w=200, h=56),
        Node("P", "显式参数 preferred？", 360, 110, "amber", "diamond", w=220, h=90),
        Node("A", "用 preferred", 90, 250, "green", w=180, h=56),
        Node("E", "环境变量\nCHEF_PROVIDER？", 360, 250, "amber", "diamond", w=220, h=90),
        Node("B", "用 CHEF_PROVIDER", 90, 400, "green", w=180, h=56),
        Node("F", "第一个配好\n密钥的？", 360, 400, "amber", "diamond", w=220, h=90),
        Node("C", "用第一个配置", 90, 550, "green", w=180, h=56),
        Node("D", "报错 / 默认", 400, 550, "red", w=180, h=56),
    ]
    edges = [
        ("S", "P"),
        ("P", "A", {"label": "有"}),
        ("P", "E", {"label": "无"}),
        ("E", "B", {"label": "有"}),
        ("E", "F", {"label": "无"}),
        ("F", "C", {"label": "有"}),
        ("F", "D", {"label": "无"}),
    ]
    return {"nodes": nodes, "edges": edges}


# ============ 图8 · PDF 解析确定性路由 ============
def fig8():
    nodes = [
        Node("IN", "输入 PDF", 360, 20, "gray"),
        Node("PR", "探测结构\n_probe_pdf_structure", 330, 120, "blue"),
        Node("SC", "加权评分\nroute()", 350, 230, "amber", "diamond", w=200, h=90),
        Node("SI", "SimplePDFParser\n(pdfplumber/pymupdf)", 90, 370, "green", w=250, h=70),
        Node("AD", "LlamaParseParser\n(云端)", 470, 370, "purple", w=230, h=70),
    ]
    edges = [
        ("IN", "PR"),
        ("PR", "SC"),
        ("SC", "SI", {"label": "分数低/简单"}),
        ("SC", "AD", {"label": "分数高/复杂"}),
    ]
    return {"nodes": nodes, "edges": edges}


# ============ 图9 · 启动端口治理 ============
def fig9():
    nodes = [
        Node("RUN", "python run.py", 340, 20, "blue", w=220, h=56),
        Node("CHK", "端口 8010 占用？", 350, 120, "amber", "diamond", w=200, h=90),
        Node("KILL", "taskkill 仅杀\n自己上次启动的 PID", 90, 270, "red", w=250, h=70),
        Node("START", "uvicorn 启动后端", 430, 270, "green", w=230, h=70),
        Node("OK", "服务就绪 /api/", 360, 390, "green", w=220, h=56),
    ]
    edges = [
        ("RUN", "CHK"),
        ("CHK", "KILL", {"label": "占用且是自己"}),
        ("CHK", "START", {"label": "空闲"}),
        ("KILL", "START"),
        ("START", "OK"),
    ]
    return {"nodes": nodes, "edges": edges}


# 渲染
figs = {
    "图1.svg": fig1, "图2.svg": fig2, "图3.svg": fig3, "图4.svg": fig4,
    "图5.svg": fig5, "图6.svg": fig6, "图7.svg": fig7, "图8.svg": fig8, "图9.svg": fig9,
}

for name, fn in figs.items():
    spec = fn()
    if "_extra" in spec:
        # 图6 特殊处理：先正常 build 再追加车道箭头
        svg = build(spec)
        # 在 </svg> 前插入 extra
        svg = svg.replace("</svg>", spec["_extra"] + "</svg>")
    else:
        svg = build(spec)
    save(name, svg)

print("ALL DONE")
