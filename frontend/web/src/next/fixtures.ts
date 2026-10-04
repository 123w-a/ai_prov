import type { ChefAnswer } from '../types.ts'
import type { RunState } from './data/model.ts'
import type { FamilyMemberRow, WeekView } from './data/firstScreen.ts'

/**
 * 只读夹具（/fixture.html 专用，**故意不进 vite build 的 input**）。
 *
 * ═══ 为什么需要它 ═══════════════════════════════════════════════════════════
 *
 * 结果页与等待页只有真跑一轮 POST /api/chat 才会出现，而真跑要花额度、会写真实会话、
 * 耗时 52–111s（实测区间不稳定）。做视觉重设计要反复截图比对，不能每次都真跑。
 *
 * 所以这里把**后端真实的结构形态**固定下来当成验收靶子。字段与取值按
 * docs/后端API清单.md 的 ChefAnswer 与 src/types.ts 逐字对齐；
 * **文案与数值是构造样本，不是真实抓取**——它只用来撑起版式，不声称是真实返回。
 *
 * 覆盖的形态全部来自实测分布（历史 35 条）：
 *   structured 22/23 · prose 13/35 · dish_matrix 1/23 · guardrails 22/23 为空
 *   health_lights 缺失、sources 为空、image_url 为 null 都有真实出处
 *
 * 纪律：夹具只喂**真实存在的字段组合**。不许为了好看造出后端给不出的结构
 * （例如 health_lights 里塞数值、dish_matrix 按人呢称结论），那会把降级路径隐藏掉。
 */

/** 夹具配图：从已裁决的设计稿里裁出的真实菜品照片，放在 public/fixture/ 下，离线可用。 */
const DISH = '/fixture/dish.png'

function run(over: Partial<RunState>): RunState {
  return {
    status: 'succeeded',
    elapsed: 84000,
    request: '今晚想吃鱼，清淡一点，别太油，两个人的分量',
    // 需求回显框的记录时刻（第 4 项）。取「两小时前」而不是写死一个日期：
    // 这样它稳定落在「今天」那一档读法上，明天跑也还是同一档断言。
    askedAt: Date.now() - 2 * 60 * 60 * 1000,
    events: [],
    heartbeats: [],
    currentStage: null,
    body: '',
    answer: null,
    firstTokenAt: 12000,
    error: null,
    ...over,
  }
}

/** 候选清单轮：**没有 answer**。
 *
 *  这不是我造的特例，而是后端的既有行为：answer_kind === 'candidates' 时
 *  chat_route.py 刻意把 answer 丢掉不推（见该文件 1580 行附近的 continue），
 *  因为候选清单本身已经由正文 token 流式展示过了。
 *
 *  这一档之前没有任何夹具，于是"候选轮到底渲染成什么"从来没被验过——
 *  而用户正是在这条路径上踩到了坑（点了首屏、拿到三道菜、然后找不到继续输入的入口）。 */
const CANDIDATES = run({
  body: `先给你三道今晚就能做的，你挑一道我再往下展开：

1. 香煎三文鱼配蒜香西兰花 —— 一口平底锅，18 分钟，钠最低
2. 清蒸鲈鱼配姜丝 —— 更清淡，蒸锅 12 分钟，刺少好挑
3. 番茄龙利鱼汤 —— 汤菜，酸口开胃，适合孕期口味

回一句「第几个」就行，也可以直接说要改什么。`,
  answer: null,
  elapsed: 41000,
  events: [{ stage: 'searching', at: 8000, nth: 1 }],
})

/** 常规形态：22/23 走这条。没有 dish_matrix、guardrails 为空、三盏灯。 */
const TYPICAL: ChefAnswer = {
  opening:
    '你今晚想吃鱼、要清淡，家里冰箱里还有西兰花和鸡蛋。三文鱼本身脂肪构成清爽，用香煎做法既能保住口感又不用重油，配蒜和黑胡椒提味就够。这一顿按两个人的身体来配——一位在孕期、需要控钠，一位在增肌、需要蛋白质，所以调味收在蒜香和黑胡椒上，不另外加盐。',
  recipes: [
    {
      name: '香煎三文鱼配蒜香西兰花',
      intro: '三文鱼两面各煎一分半，外皮脆、里面还是嫩的；西兰花焯水后再用蒜末快炒，保持脆感。整道菜只用一口平底锅和一个汤锅。',
      // 口味标签（第 6 项）：正文里确实说了「清爽」「不重油」「配蒜提味」，
      // 这三个词是从那些说法里提的，不是凭空贴的。
      flavor_tags: ['清淡', '蒜香', '家常'],
      difficulty: 3,
      nutrition: 4,
      // 主食材与克数（第 5 项营养表的输入）。正文里的用量是散在步骤中的，
      // 结构化阶段把它抽成这个列表——没有这个列表就没有那张六列的表。
      ingredients: [
        { name: '三文鱼', amount_g: 300 },
        { name: '西兰花', amount_g: 200 },
        { name: '大蒜', amount_g: 10 },
        { name: '食用油', amount_g: 15 },
      ],
      seasonings: [
        { name: '黑胡椒', amount: '适量' },
        { name: '大蒜', amount: '4 瓣' },
        { name: '橄榄油', amount: '1 汤匙' },
        { name: '柠檬', amount: '半个' },
        { name: '生抽', amount: '1 茶匙' },
      ],
      steps: [
        '三文鱼从冰箱取出，用厨房纸把两面彻底按干——表面有水就煎不出脆皮。两面撒黑胡椒，静置 10 分钟回温。',
        '西兰花掰成小朵，汤锅烧水，水开后加一点点油，西兰花下锅焯 90 秒立刻捞出过一下凉水，颜色和脆度都保住。',
        '大蒜切末分两份。平底锅中火预热，倒橄榄油，三文鱼皮朝下入锅，不要翻动，煎 90 秒。',
        '翻面后再煎 90 秒，把第一份蒜末撒在鱼身上，让蒜香沾上去，出锅前挤半个柠檬。',
        '同一口锅不用洗，下第二份蒜末炒香，把焯好的西兰花倒进去快炒 40 秒，沿着锅边淋 1 茶匙生抽，拌匀出锅。',
        '装盘：三文鱼放一侧，西兰花放另一侧，锅里的蒜油淋在西兰花上。趁热吃，凉了皮就不脆了。',
      ],
      image_url: DISH,
      image_ai_generated: true,
      image_note: '这是 AI 生成的示意图，不是这道菜的真实照片，摆盘仅供参考。',
    },
  ],
  image_url: DISH,
  image_ai_generated: true,
  image_note: '这是 AI 生成的示意图，不是这道菜的真实照片，摆盘仅供参考。',
  chef_tip:
    '三文鱼下锅前一定要把表面按干，这一步决定皮脆不脆；煎的时候别急着翻，等它自己脱锅。如果这条鱼偏厚，最后 30 秒盖上锅盖让它里面熟透。孕期这一位要注意：生抽是这顿里唯一额外的钠来源，1 茶匙就够，别再补盐。',
  sources: [
    { source: '中国食物成分表（第 6 版）· 三文鱼.pdf', section: '鱼虾类', category: '营养' },
    { source: '孕期膳食营养与食品安全指南.pdf', section: '水产类摄入建议', category: '健康' },
    { source: '香煎鱼类火候与去腥工艺研究.pdf', section: '煎制温度', category: '做法' },
    { source: '常见水产过敏原分布与交叉反应.pdf', category: '安全' },
  ],
  // 这顿的营养数值表：后端按上面那份 ingredients 查国标《中国食物成分表》算出。
  // 下面这些数字是脚本跑**后端同一个函数**得来的，不是手写的——所以夹具口径与线上一致。
  nutrition_facts: {
    status: 'complete',
    nutrients: {
      energy_kcal: { value: 636.5, status: 'known' },
      protein_g: { value: 60.2, status: 'known' },
      fat_g: { value: 39.6, status: 'known' },
      carb_g: { value: 11.4, status: 'known' },
      fiber_g: { value: 3.3, status: 'known' },
      sodium_mg: { value: 230.2, status: 'known' },
    },
    ingredients: [
      { name: '三文鱼', amount_g: 300, lookup_name: '三文鱼（鲑鱼）', status: 'covered' },
      { name: '西兰花', amount_g: 200, lookup_name: '西兰花（绿菜花）', status: 'covered' },
      { name: '大蒜', amount_g: 10, lookup_name: '大蒜（蒜头）', status: 'covered' },
      { name: '食用油', amount_g: 15, lookup_name: '大豆油', status: 'covered' },
    ],
    basis: 'recipe_ingredient_amounts',
    covered: ['三文鱼', '西兰花', '大蒜', '食用油'],
    missing: [],
    no_amount: [],
  },
  health_lights: [
    { label: '钠', level: 'yellow', reason: '生抽带来额外钠，孕期建议控制在每餐 1 茶匙酱油以内。' },
    { label: '脂肪', level: 'green', reason: '三文鱼以不饱和脂肪为主，煎制用油 1 汤匙，总量在合理区间。' },
    { label: '蛋白质', level: 'green', reason: '两人份约 300 克三文鱼，增肌这一位能拿到约 60 克蛋白质。' },
  ],
  primary_member: '小美',
}

/** 满数据形态：1/23 才有 dish_matrix，且护栏非空——必须单独验，否则这条路径永不出现。 */
const FULL: ChefAnswer = {
  ...TYPICAL,
  opening:
    '你今晚想吃鱼、要清淡。三文鱼配蒜香西兰花是这个方向的稳妥选择，两个人的身体约束在这一顿里只有一处冲突：孕期这一位要控钠，增肌这一位要足够的蛋白质。解决方案是在调味上收盐、在分量上给足鱼——所以这顿的钠来自生抽，蛋白质来自鱼本身，两者不打架。',
  guardrails: [
    {
      condition: '孕期',
      rule: '水产需彻底加热，避免生食',
      status: '已遵守',
      reason: '香煎做法两面各 90 秒，鱼中心完全熟透，未采用生食做法。',
    },
    {
      condition: '孕期',
      rule: '每日钠摄入建议不超过 2000 毫克',
      status: '需留意',
      reason: '本顿额外钠来自 1 茶匙生抽，约 330 毫克，占建议量约六分之一。',
    },
  ],
  health_lights: [
    { label: '钠', level: 'red', reason: '生抽用量若超过 1 茶匙，孕期这一位的钠摄入会明显偏高。' },
    { label: '脂肪', level: 'yellow', reason: '煎制需用油，建议控制在 1 汤匙以内。' },
    { label: '蛋白质', level: 'green', reason: '两人份三文鱼约提供 60 克蛋白质，满足增肌需求。' },
  ],
  member_adjustments: [
    '小美（孕期）：生抽减到 1 茶匙，不再补盐；鱼必须完全熟透。',
    '我（增肌）：鱼的分量给到 180 克，西兰花不够可以再加一个鸡蛋。',
  ],
  dish_matrix: [
    { dish: '香煎三文鱼配蒜香西兰花', member: '小美', verdict: '需调整', reason: '生抽减量后即可，鱼需完全熟透。' },
    { dish: '香煎三文鱼配蒜香西兰花', member: '我', verdict: '可吃', reason: '蛋白质充足，无冲突。' },
  ],
}

/** 缺口形态：算得出来，但少算了几样。
 *
 *  这一档最容易骗人——六个数字看着齐，其实有两样没进去（一样国标表没收录、
 *  一样正文没写克数）。所以表头必须改口说「已覆盖食材小计」，并把缺口列出来。
 *  夹具里必须有它：只验 complete 与 unavailable，恰好会漏掉最危险的那一档。 */
const NUTRI_PARTIAL: ChefAnswer = {
  ...TYPICAL,
  recipes: [
    {
      ...TYPICAL.recipes[0],
      ingredients: [
        { name: '三文鱼', amount_g: 300 },
        { name: '西兰花', amount_g: 200 },
        { name: '牛油果', amount_g: 80 },   // 国标表未收录
        { name: '大蒜', amount_g: null },   // 正文只写了「4 瓣」，没有克数
      ],
    },
  ],
  nutrition_facts: {
    status: 'partial',
    nutrients: {
      energy_kcal: { value: 489, status: 'known' },
      protein_g: { value: 59.8, status: 'known' },
      fat_g: { value: 24.6, status: 'known' },
      carb_g: { value: 8.6, status: 'known' },
      fiber_g: { value: 3.2, status: 'known' },
      sodium_mg: { value: 227.5, status: 'known' },
    },
    ingredients: [
      { name: '三文鱼', amount_g: 300, lookup_name: '三文鱼（鲑鱼）', status: 'covered' },
      { name: '西兰花', amount_g: 200, lookup_name: '西兰花（绿菜花）', status: 'covered' },
      { name: '牛油果', amount_g: 80, lookup_name: null, status: 'missing' },
      { name: '大蒜', amount_g: null, lookup_name: '大蒜（蒜头）', status: 'no_amount' },
    ],
    basis: 'recipe_ingredient_amounts',
    covered: ['三文鱼', '西兰花'],
    missing: ['牛油果'],
    no_amount: ['大蒜'],
  },
}

/** 最薄形态：字段大面积缺失。降级路径全靠它验，否则"没有数据"会被画成空白。
 *  它特意**不给** nutrition_facts：这里要验的是「后端没这个字段 ⇒ 整块不渲染」，
 *  而不是「渲染一张写着算不出来的表」——那是别的 case 的事。 */
const THIN: ChefAnswer = {
  recipes: [
    {
      name: '清炒时蔬',
      intro: '',
      difficulty: 1,
      nutrition: 3,
      seasonings: [],
      steps: ['蔬菜洗净切段。', '热锅下油，下蔬菜快炒两分钟。', '出锅前调一点点味，装盘。'],
      image_url: null,
      image_ai_generated: false,
    },
  ],
  health_lights: [],
  sources: [],
}

/** 溢出压力：超长菜名、超长步骤、超长来源名。窄栏与右栏都在这里露底。 */
const LONG: ChefAnswer = {
  ...TYPICAL,
  recipes: [
    {
      ...TYPICAL.recipes[0],
      name: '香煎三文鱼配蒜香西兰花与柠檬黄油汁佐烤小土豆及季节时蔬拼盘',
      steps: [
        '三文鱼从冰箱取出后用厨房纸把两面彻底按干，表面残留的水分会让它在锅里变成蒸而不是煎，脆皮这一步就废了；两面均匀撒上现磨黑胡椒，静置十分钟让它回到室温，冷肉直接下锅会让外层先老而里面还生。',
        '西兰花掰成大小一致的小朵，汤锅烧到水滚，水里加一点点油，西兰花下锅焯九十秒立刻捞出过凉水，颜色和脆度都能保住；这一步的八十秒到一百秒之间差别很明显，久了就软塌。',
        '平底锅中火预热到滴一滴水会立刻发出嘶声，倒橄榄油，三文鱼皮朝下入锅后不要翻动，等它自己脱锅，大约九十秒。',
      ],
      image_url: DISH,
      image_ai_generated: true,
      image_note: 'AI 生成示意图。',
    },
  ],
  sources: [
    { source: '中国食物成分表（第 6 版）· 鱼虾类及制品分册（含淡水鱼与海水鱼脂肪酸构成对比）.pdf', section: '鱼虾类', category: '营养' },
    { source: '孕期膳食营养与食品安全指南（2024 修订版）· 水产类摄入建议与汞含量分级.pdf', section: '水产类摄入建议', category: '健康' },
    { source: '香煎鱼类火候控制与腥味物质去除工艺研究报告.pdf', category: '做法' },
  ],
  chef_tip: '三文鱼下锅前一定要把表面按干，这一步决定皮脆不脆；煎的时候别急着翻，等它自己脱锅。',
}

/** 纯散文形态：13/35 走这条。它必须和卡片形态**同等级**，不许被画成次品。 */
const PROSE: ChefAnswer = {
  opening:
    '你今晚想吃鱼。冰箱里有鸡蛋、西兰花和面条，楼下买一条鱼就够。\n\n先说结论：可以做清蒸，但考虑到一位在孕期，蒸鱼的火候要到位，中心必须完全熟透。\n\n做法上，鱼身两面各划两刀，抹一点点料酒去腥，姜片垫底，水开后大火蒸八分钟，关火再焖两分钟。出锅后把盘里的水倒掉，淋一点生抽，铺上葱丝，最后浇一勺热油。\n\n西兰花另起一锅焯水，配着吃就能平衡掉蒸鱼偏咸的那点口感。',
  recipes: [],
  chef_tip: undefined,
  sources: [
    { source: '孕期膳食营养与食品安全指南.pdf', section: '水产类摄入建议', category: '健康' },
    { source: '清蒸鱼类火候控制与腥味去除工艺研究.pdf', category: '做法' },
  ],
  health_lights: [
    { label: '钠', level: 'yellow', reason: '生抽是主要钠来源，建议半汤匙以内。' },
  ],
}

/** 空答案：正文到了但没抽出任何结构。必须显示成"有内容但整理失败"，不是空白页。 */
const EMPTY: ChefAnswer = {
  opening: '',
  recipes: [],
}

/* ── 首屏右栏四块（kind='ask'）的构造样本 ────────────────────────────────
 *
 * 与上面的 result/wait 同理：四块在真实页面要靠后端四条只读接口才有，
 * 而降级态（无候选 / 空冰箱 / 双空 / 本周无记录 / 四块全空）在真实数据里
 * 要等用户把状态改出来才看得到——验收不能等，所以在这里固定下来。
 *
 * 纪律同源：字段与取值按 data/firstScreen.ts 的 FamilyMemberRow / WeekView
 * 逐字对齐，数值是**构造样本**，不声称是真实返回。
 *
 * 值得注意：`fridge: null` 不是"冰箱空着"的占位，而是 fridgeView([]) 的
 * 返回值——空数组 ⇒ null ⇒ FridgeBlock 整块不渲染（不留空框、不写"暂无"）。 */
type RailFam = { members: FamilyMemberRow[]; shared: string[] }
type RailCand = { member: string; source: string; severity: string }
type RailFridge = { count: number; names: string }

export type RailProps = {
  fam: RailFam | null
  week: WeekView | null
  cand: RailCand | null
  fridge: RailFridge | null
}

const RAIL_FAM: RailFam = {
  members: [
    { id: 'm_xiaomei', name: '小美', isActive: false, digits: ['30岁', '165cm'], tags: ['孕妇'] },
    { id: 'm_self', name: '我', isActive: true, digits: ['19岁', '176cm', '63kg'], tags: ['增肌'] },
  ],
  // 共同忌口只写一次：两人都忌芹菜，写两遍只是噪音。
  shared: ['芹菜'],
}

const RAIL_WEEK: WeekView = {
  isEmpty: false,
  range: ['2026-09-21', '2026-09-28'],
  meals: 2,
  dishes: [
    ['西兰花鸡蛋汤面', 1],
    ['蒜香香菇红烧肉（全家可吃版）', 1],
  ],
  // 按营养素聚合后的计数（复合键 "钠:green" 拆分的结果形态）。
  lights: [
    { nutrient: '钠', green: 2, yellow: 0, red: 0 },
    { nutrient: '糖', green: 1, yellow: 1, red: 0 },
    { nutrient: '脂肪', green: 1, yellow: 1, red: 0 },
  ],
  // 后端实测全给 insufficient ⇒ 照实写"数据不足"，**不画趋势线**。
  trends: [
    { label: '钙', state: 'insufficient' },
    { label: '脂肪', state: 'insufficient' },
    { label: '钠', state: 'insufficient' },
  ],
  guardrail: 1,
  // 2026-09-29 curl 实测：/api/reports/weekly **不返回** recommendations
  // （types.ts 定义了可选字段，接口不给），feedback_summary 给的是
  // count:0 / tags:{}。夹具照真实形态造，不替后端编内容——与「趋势
  // 照实写」是同一条纪律。
  recommendations: [],
  feedback: { count: 0, tags: {} },
}

const RAIL_CAND: RailCand = {
  member: '小美',
  source: '今晚想用冰箱里的东西做一顿，我增肌、小美患孕，做个我们俩都能吃的。',
  severity: 'chronic',
}

const RAIL_FRIDGE: RailFridge = { count: 4, names: '鸡蛋、西兰花、面条、大蒜' }

export type FixtureKind = 'result' | 'wait' | 'ask'

export interface FixtureCase {
  kind: FixtureKind
  label: string
  run: RunState
  /** 仅 kind='ask' 带：右栏四块的输入（AskRail 的四个具名参数）。 */
  rail?: RailProps
}

const EVENTS = [
  { at: 1200, stage: 'searching' as const, nth: 1 },
  { at: 18400, stage: 'auditing' as const, nth: 1 },
  { at: 52300, stage: 'searching' as const, nth: 2 },
  { at: 60100, stage: 'auditing' as const, nth: 2 },
  { at: 79000, stage: 'structuring' as const, nth: 1 },
]

export const CASES: Record<string, FixtureCase> = {
  /* ── 首屏四块（kind='ask'）───────────────────────────────────────────
   * 夹具台此前只有 result/wait 两形态 ⇒ 右栏的降级态在验收里根本不存在，
   * lave 要的「无候选 / 空冰箱 / 双空 / 本周无记录 / 四块全空」一个都验不了。
   * 括号里的字母对应契约降级态编号（A 处理候选 → B 清空冰箱 → C 双空 → D 本周空）。
   * 每个 case 的断言见 scripts/ 下的验收，不靠肉眼。 */
  ask: {
    kind: 'ask',
    label: '首屏·四块齐全',
    run: run({}),
    rail: { fam: RAIL_FAM, week: RAIL_WEEK, cand: RAIL_CAND, fridge: RAIL_FRIDGE },
  },
  'ask-no-cand': {
    kind: 'ask',
    label: '首屏·无候选(A)',
    run: run({}),
    rail: { fam: RAIL_FAM, week: RAIL_WEEK, cand: null, fridge: RAIL_FRIDGE },
  },
  'ask-empty-fridge': {
    kind: 'ask',
    label: '首屏·空冰箱(B)',
    run: run({}),
    rail: { fam: RAIL_FAM, week: RAIL_WEEK, cand: RAIL_CAND, fridge: null },
  },
  'ask-double-empty': {
    kind: 'ask',
    label: '首屏·双空(C)',
    run: run({}),
    rail: { fam: RAIL_FAM, week: RAIL_WEEK, cand: null, fridge: null },
  },
  'ask-week-empty': {
    kind: 'ask',
    label: '首屏·本周无记录(D)',
    run: run({}),
    rail: {
      fam: RAIL_FAM,
      week: { isEmpty: true, message: '本周还没有用餐记录' },
      cand: RAIL_CAND,
      fridge: RAIL_FRIDGE,
    },
  },
  'ask-single': {
    kind: 'ask',
    label: '首屏·单人无忌口(E)',
    run: run({}),
    rail: {
      fam: { members: [{ id: 'm_self', name: '我', isActive: true, digits: ['19岁', '176cm'], tags: ['增肌'] }], shared: [] },
      week: RAIL_WEEK,
      cand: null,
      fridge: RAIL_FRIDGE,
    },
  },
  'ask-none': {
    kind: 'ask',
    label: '首屏·四块全空',
    run: run({}),
    rail: { fam: null, week: null, cand: null, fridge: null },
  },
  typical: { kind: 'result', label: '常规（22/23）', run: run({ answer: TYPICAL }) },
  /* 动作条验收（★收藏/赞/踩）：可见性判据是 succeeded && !restored && origin，
     其余 result case 都不带 origin ⇒按契约整条隐藏，所以动作条在夹具台里
     此前根本不存在（2026-10-01 验收 A 方案时发现）。
     origin 指**真实存在**的记录（用户测试会话里那条已打星的消息），这样点★
     走的是真后端、验的是正向可用性——指向假 id 只能验收 404 错误路径。
     archive 不设 ⇒ 新轮初值确定（星/评分状态按「刚创建必然未收藏」给），
     这与真实 finish 后的本轮一致。 */
  actions: {
    kind: 'result',
    label: '结果·动作条(★/赞/踩)',
    run: run({
      answer: TYPICAL,
      origin: { sessionId: 'user_sse_direct_mt9nqtp0', recordId: 1 },
    }),
  },
  full: { kind: 'result', label: '满数据（含家人矩阵）', run: run({ answer: FULL, elapsed: 354700 }) },
  thin: { kind: 'result', label: '缺字段（最薄，无营养表）', run: run({ answer: THIN, elapsed: 52000 }) },
  candidates: { kind: 'result', label: '候选轮（无 answer，正文是清单）', run: CANDIDATES },
  partial: { kind: 'result', label: '营养表·缺口（已覆盖小计）', run: run({ answer: NUTRI_PARTIAL, elapsed: 96000 }) },
  long: { kind: 'result', label: '长文本溢出压力', run: run({ answer: LONG }) },
  prose: { kind: 'result', label: '纯散文（13/35）', run: run({ answer: PROSE, elapsed: 111000 }) },
  empty: { kind: 'result', label: '空结构（整理失败）', run: run({ answer: EMPTY, elapsed: 47000 }) },
  wait: {
    kind: 'wait',
    label: '等待中（有正文流）',
    run: run({
      status: 'running',
      answer: null,
      elapsed: 62000,
      events: EVENTS.slice(0, 4),
      heartbeats: [5000, 10000, 15000, 20000, 25000, 30000, 35000, 40000, 45000, 50000, 55000, 60000],
      currentStage: 'auditing',
      body: '你今晚想吃鱼、要清淡。先说结论：',
      firstTokenAt: 41000,
    }),
  },
  stale: {
    kind: 'wait',
    label: '等待中（心跳中断）',
    run: run({
      status: 'running',
      answer: null,
      elapsed: 96000,
      events: EVENTS.slice(0, 3),
      heartbeats: [5000, 10000, 15000, 20000],
      currentStage: 'searching',
      body: '',
      firstTokenAt: null,
    }),
  },
  failed: {
    kind: 'wait',
    label: '失败态',
    run: run({
      status: 'failed',
      answer: null,
      elapsed: 34000,
      error: '这一轮没有跑完：上游模型连接中断。',
      httpStatus: 502,
      body: '你今晚想吃鱼',
    }),
  },
  orphan: {
    kind: 'wait',
    label: '中断（用户取消）',
    run: run({
      status: 'running',
      answer: null,
      elapsed: 71000,
      orphaned: true,
      orphanCause: 'cancelled',
      events: EVENTS.slice(0, 3),
      heartbeats: [5000, 10000, 15000],
      currentStage: 'auditing',
      body: '你今晚想吃鱼、要清淡。',
    }),
  },
}

export const CASE_IDS = Object.keys(CASES)








