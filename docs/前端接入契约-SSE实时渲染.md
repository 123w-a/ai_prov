# 前端接入契约：SSE 实时渲染与去重

> 读者：接前后端的另一个前端（重建版 / 融合版）。
> 权威端：本仓库的后端（`main.py`、`api/routes/chat_route.py`）。
> 这份文档只讲一件事：**后端一轮里到底发了什么，前端必须怎么渲染才不会把同一段话说两遍。**
>
> 所有结论都标了来源行号，可以逐条打开核对。文档与代码冲突时以代码为准。

---

## 0. 一句话结论

**后端一轮会把同一段正文发两次，这是设计，不是 bug：**

1. 先生成正文，边生成边以 `token` 事件流式推给前端（打字机效果）；
2. 正文同时被送进结构化节点，作为 `answer.opening` 跟整包答案再推一次（供卡片渲染与落库）。

所以「答案说了两遍」的根因在**渲染侧**：前端把 `token` 累积的气泡正文留着，又把 `answer.opening` 渲染了一遍。
后端不负责帮前端去重，它只保证两个通道的内容一致。

---

## 1. 一轮的真实事件形状（源码级）

一轮正常回答，后端按下面的顺序产出事件（`api/routes/chat_route.py:1686-1790`）：

| 顺序 | 事件 | 含义 | 前端该做什么 |
|---|---|---|---|
| 1 | `{"status":"working"}` | 开始 | 建占位气泡，阶段=思考 |
| 2 | `{"stage":"…"}` | 阶段流转 | 更新阶段文案 |
| 3 | `{"heartbeat":{"elapsed":n}}` | 心跳 | 更新计时 |
| 4 | `{"token":"…"}` × N | 流式正文 | 追加到当前气泡正文 |
| 5 | `{"reset":true}` | **可能出现在任意时刻**：护栏打回重生成 | **立刻清空当前气泡正文**（见规则 R2） |
| 6 | `{"structuring":true}` | 正文说完了，正在整理卡片 | 阶段=整理 |
| 7 | `{"answer":{ChefAnswer}}` | 整包结构化答案（含 `opening`） | 见规则 R1 |
| 8 | `{"image":{…}}` | 补图完成（可能几十秒后） | 就地更新卡片，**不新增消息** |
| 9 | `{"image_failed":{…}}` | 补图失败 | 卡片进入明确失败态，不新增消息 |
| 10 | `{"finish":true,"session_id":…,"record_id":…}` | 本轮结束 | 回填 `record_id`，用于后续同步配对 |

关键事实（`main.py`）：

- `token` 来自 `chef_think` / `ask_user` 节点的流式块（`main.py:565-579`）。
- `answer` 里的 `opening` 就是**同一段已经推给前端的正文**（`agent/graph.py:3921-3926` 注释原文：
  「chef_think 最后一轮的自然语言回答 = 流式已经推给前端的正文，原样保留进 opening」）。
- 当本轮没有可见正文时，后端会**先把 `answer.opening` 按 `token` 推一遍**，再推 `answer`
  （`main.py:518-545`）——即「先用打字机说一遍，再用卡片显示一遍」是刻意的。
- `reset` 只在护栏打回时发，客户端必须和状态机一起丢弃上一版正文（`main.py:450-463`）。

---

## 2. 硬规则（照做即可消除重复）

### R1 · `answer` 接管正文

收到 `answer` 时，用 `recipes` 是否为空决定气泡正文的去留：

- `answer.recipes` 非空：**气泡正文清空**，这段讲解由卡片渲染 `answer.opening` 显示（一次）。
- `answer.recipes` 为空（安全拒绝 / 澄清 / 纯文字回答）：气泡正文 = `answer.opening || answer.chef_tip || ''`。

参考实现（本仓库已验证）：`frontend/web/src/App.tsx:659-696`。

### R2 · `reset` 必须清空

`reset` 到达时，把当前气泡已累积的正文**全部丢弃**，再开始接收第二版 `token`。
不清的后果是两版正文首尾拼接，用户在同一轮里看到同一道菜讲两遍。

参考实现：`frontend/web/src/App.tsx:617-626`。

### R3 · 服务端同步不能追加新气泡

后端在**请求入口**就先落一条用户消息（`answer="__pending__"`，`api/routes/chat_route.py:1114-1117`），
回答完成后原地更新（`storage/sessions.py:347-366`）。`GET /api/sessions` 返回的每条消息形如：

```json
{
  "id": 12,
  "user_text": "…",
  "answer": "{\"opening\":\"…\",\"recipes\":[…] }",
  "time": "12:03",
  "image_url": null,
  "starred": false,
  "feedback": null
}
```

所以：

- `answer` 是**字符串**，需要 `JSON.parse`；为 `"__pending__"` 时表示「回答生成中」。
- 同步回来时按 `recordId` 配对合并，**绝不允许把服务端消息直接 append 成第二条助手气泡**。
- 本地流式气泡此刻可能还没有 `recordId`（要等 `finish` 才回填），需要用
  「服务端 `__pending__` 占位 + 本地正在流式/已有 answer」做兜底配对。

参考实现：`frontend/web/src/App.tsx:256-330`。

### R4 · 候选轮没有 `answer` 事件

`answer_kind === "candidates"` 的轮次，后端**不发 `answer`**（`api/routes/chat_route.py:1721-1742`），
候选清单只走 `token` 流。前端不要在这类轮次里等待或渲染结构化卡片。

### R5 · 图片永远在 `answer` 之后，且不新增消息

补图是 `answer` 之后的独立 `image` / `image_failed` 事件，靠 `record_id` + `turn_id` 定位到**已有卡片**就地更新
（`api/routes/chat_route.py:1777-1790`）。用它新建一条消息就会出现「同一道菜两条」。

---

## 3. 会直接导致「说两遍」的写法

对照检查另一个前端的实现，命中任何一条都会复现重复：

| 写法 | 现象 |
|---|---|
| 收到 `answer` 后不清 `token` 气泡正文，同时卡片渲染 `answer.opening` | 同一段讲解在气泡与卡片各出现一次 |
| 忽略 `reset` 事件 | 护栏打回时两版正文拼接，一轮里讲两遍 |
| 同步 `GET /api/sessions` 时直接 append 服务端消息 | 整轮再出现一条助手气泡 |
| 用 `user_text` 或时间戳配对，而不是 `record_id` | 配对失败，同样产生第二条气泡 |
| 把候选轮的 `token` 正文再拿 `answer` 渲染一次 | 候选清单重复 |
| 把 `image` 事件当新消息 push | 同菜两张卡 |

---

## 4. 参考实现（框架无关）

如果新前端不是 React，把下面的纯函数当成渲染契约的落地骨架：

```ts
type Turn = {
  id: string                 // 本地气泡 id
  recordId?: number          // finish 后回填，服务端同步用
  text: string               // 气泡正文（token 累积）
  answer?: ChefAnswer | null // 结构化答案
  stage?: string
}

// R1 + R2：本轮气泡的两个入口事件
function onToken(turn: Turn, token: string): Turn {
  return { ...turn, text: turn.text + token, stage: 'writing' }
}

function onReset(turn: Turn): Turn {
  return { ...turn, text: '' }               // 丢弃上一版正文
}

function onAnswer(turn: Turn, answer: ChefAnswer): Turn {
  const recipes = answer.recipes ?? []
  const text = recipes.length === 0
    ? (answer.opening || answer.chef_tip || '')   // 纯文字回答：气泡保留正文
    : ''                                          // 卡片接管：气泡正文清空，防重复
  return { ...turn, answer, text, stage: undefined }
}

function onFinish(turn: Turn, payload: { record_id?: number }): Turn {
  return { ...turn, recordId: turn.recordId ?? payload.record_id }
}

// R3：服务端同步的合并键就是 recordId
function mergeSynced(local: Turn[], server: Turn[]): Turn[] {
  const localByRecord = new Map(local.filter(t => t.recordId != null).map(t => [t.recordId!, t]))
  const localStreaming = local.find(t => !t.recordId && !t.answer) // 未落定的流式气泡
  return server.map(s => {
    const hit = localByRecord.get(s.recordId!) ?? (s.pending ? localStreaming : undefined)
    return hit ? mergeTurn(hit, s) : s   // 命中即合并，绝不 append 新气泡
  })
}
```

其中 `answer.recipes` 为空时的正文兜底、`opening_authoritative` 下的版本选择，
可参考 `frontend/web/src/answerMerge.ts`（纯函数 + 单元测试，已有 3 条用例）。

---

## 5. 对接自测（五分钟）

1. 新建临时会话：`POST /api/sessions`，记下 `session_id`。
2. 打开浏览器 Network，用 `POST /api/chat`（multipart，`want_image="0"`）发一条推荐类问题，
   确认事件顺序是 §1 那张表，且 `token` 之后必然跟一条 `answer`。
3. 看界面：推荐类问题应**只有卡片一处**讲解；纯文字问答应**只有气泡一处**。
4. 触发一次护栏重生成（对过敏原 / 健康红线提要求），确认 `reset` 后气泡正文被清空而不是拼接。
5. 等 2 秒后刷新页面，确认同步回来的历史**没有多出重复气泡**。
6. 验证完删除临时会话：`DELETE /api/sessions/{sid}`。

> 付费提醒：验证时用 `want_image="0"`；带 `want_image="1"` 会真实调用生图并计费。

---

## 6. 与后端的分工边界

- 后端负责：正文生成、结构化、落库、事件顺序、`record_id` 一致性。
- 前端负责：按 §2 的规则渲染与去重，不再重复调用业务判定。
- 后端已把「同一段正文的两个通道」做成**内容一致**；前端不需要比较两个通道的文本，
  只需要在 `answer` 到达时决定「气泡留不留」。
