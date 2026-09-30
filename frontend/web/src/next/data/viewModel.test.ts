/**
 * 视图模型的测试。用 `node --test` 直接跑（本机 node 支持 type stripping，无需额外依赖）：
 *
 *     node --test src/next/data/viewModel.test.ts
 *
 * ⚠️ 这些样本绝大多数是**真实观测**，不是编的——每一条背后都是页面上真的出过一次的错。
 * 编造样本只能测出我想到的情况，而这一层的 bug 全是"没想到的情况"：
 * 后端把 Python 的 None 序列化成字面量写进 image_note、用户输入被注入 GPS 坐标前缀、
 * opening 里粘进结构模型自己的 JSON 残骸。所以样本一律取自代码注释里保留的实测原文。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import { cleanOpening, echoRequest, guardText, sameSource, srcTitle, stripDishPrefix, usableNote } from './clean.ts'
import { buildMembersVM, buildProseVM, buildResultVM, classify, hide, isShown, show } from './viewModel.ts'
import type { ChefAnswer } from '../types.ts'

/* ── 契约本身 ─────────────────────────────────────────────────────────── */

test('classify：五态判定', () => {
  assert.equal(classify(undefined), 'absent')
  assert.equal(classify(null), 'absent')
  assert.equal(classify(''), 'empty')
  assert.equal(classify('   '), 'empty')
  assert.equal(classify([]), 'empty')
  assert.equal(classify('中'), 'present')
  assert.equal(classify([1]), 'present')
  assert.equal(classify(0), 'present', '数字 0 是有值，不是空')
  assert.equal(classify(NaN), 'invalid')
  // error 是请求级的，reducer 不判字段级 error
  assert.notEqual(classify({}), 'error')
})

test('三态对外只出 show/hide/placeholder，state 留在返回值上给测试看', () => {
  const s = show('x')
  const h = hide<string>('absent')
  assert.equal(s.display, 'show')
  assert.equal(s.state, 'present')
  assert.equal(h.display, 'hide')
  assert.equal(h.state, 'absent')
  assert.ok(isShown(s))
  assert.ok(!isShown(h))
})

/* ── 正文清洗：每一条都是真实踩过的坑 ──────────────────────────────────── */

test('cleanOpening：截断结构模型自己的 JSON 残骸（实测 1/23）', () => {
  // 形态取自实测：正文后面粘着上一段 JSON 的收尾 `}` 和下一个对象的开头
  const dirty = '今晚这一顿清淡好做。}{"opening":"今晚这一顿清淡好做。","recipes":[]}'
  assert.equal(cleanOpening(dirty), '今晚这一顿清淡好做。')
})

test('cleanOpening：截断转义过的 Markdown 残骸', () => {
  assert.equal(cleanOpening('正文到此为止。\\n\\n# 标题'), '正文到此为止。')
  assert.equal(cleanOpening('正文到此为止。\\n1. 第一步'), '正文到此为止。')
})

test('cleanOpening：截断尾部字段残骸', () => {
  // 形态取自实测：opening 后面粘了 `"}]  , "guardrails": [`
  const dirty = '正文。"}], "guardrails": ['
  assert.equal(cleanOpening(dirty), '正文。')
})

test('cleanOpening：正常正文一个字符都不动', () => {
  const ok = '今晚这一顿，按两个人的身体来定。三文鱼配西兰花，清淡、好做。'
  assert.equal(cleanOpening(ok), ok)
  assert.equal(cleanOpening(undefined), '')
})

test('echoRequest：剥掉机器注入的坐标前缀（实测 37 条里 17 条带）', () => {
  assert.equal(echoRequest('[当前位置：112.383184,28.542730]\r\n想去外面吃炸鸡。'), '想去外面吃炸鸡。')
  assert.equal(
    echoRequest('[实时状态：昨晚没睡好]\r\n[当前位置：112.383343,28.542442]\r\n早上了，想吃点便宜又管饱的东西'),
    '早上了，想吃点便宜又管饱的东西',
  )
})

test('echoRequest：用户自己写的方括号内容不许吃（只剥已知机器键）', () => {
  assert.equal(echoRequest('[减脂期] 想吃点清淡的'), '[减脂期] 想吃点清淡的')
})

test('echoRequest：过长截断，空白压缩，空输入给空串', () => {
  const long = '想吃'.repeat(40)
  assert.ok(echoRequest(long).endsWith('…'))
  assert.equal(echoRequest('  想吃   鱼  '), '想吃 鱼')
  assert.equal(echoRequest(''), '')
  assert.equal(echoRequest('　'), '')
})

test('usableNote：字面量 "None" 必须当成没有（实测菜品级 20/23 是空或 None）', () => {
  assert.equal(usableNote(undefined), '')
  assert.equal(usableNote(null), '')
  assert.equal(usableNote('None'), '')
  assert.equal(usableNote('none'), '')
  assert.equal(usableNote(' NULL '), '')
  assert.equal(usableNote('这是 AI 补的示意图'), '这是 AI 补的示意图')
})

test('guardText：pass 返回空串是刻意的（它的理由本身就自述了结论）', () => {
  assert.equal(guardText('pass', '已符合高血压膳食原则'), '已符合高血压膳食原则')
  assert.equal(guardText('warn', '钠含量偏高'), '需注意 · 钠含量偏高')
  assert.equal(guardText('blocked', '已避开芹菜'), '已避开 · 已避开芹菜')
})

test('guardText：未知取值原样透出，绝不猜成某个已知档位', () => {
  assert.equal(guardText('whatever', '原因'), 'whatever · 原因')
  assert.equal(guardText('', ''), '')
})

test('stripDishPrefix：剥掉调整文案开头的菜名（实测两处讲同一件事会印两遍）', () => {
  const body = '「糖色香菇红烧肉（全家可吃版）」主食减半、蔬菜加量、不额外淋油'
  assert.equal(stripDishPrefix(body, ['糖色香菇红烧肉']), '主食减半、蔬菜加量、不额外淋油')
  assert.equal(stripDishPrefix('糖色香菇红烧肉：主食减半', ['糖色香菇红烧肉']), '：主食减半')
  assert.equal(stripDishPrefix('主食减半', ['糖色香菇红烧肉']), '主食减半')
})

test('srcTitle：去前导序号、去扩展名、下划线换间隔号，且不往下截', () => {
  assert.equal(srcTitle('4_备孕孕期妇女膳食指南解读_杨年红2022.pdf'), '备孕孕期妇女膳食指南解读·杨年红2022')
  assert.equal(srcTitle('1. 中国居民膳食指南.docx'), '中国居民膳食指南')
  assert.equal(srcTitle('readme'), 'readme')
  // 纯符号也不许变成空串（否则右栏会印出一个空白）。下划线会被换成间隔号。
  assert.equal(srcTitle('___'), '···')
})

test('sameSource：能认出同源，也不把两篇短文本误判成同源', () => {
  const a = '今晚做三文鱼配西兰花，清淡好做，适合两个人一起吃，钠含量也控制得住。'
  const b = '## 今晚做三文鱼配西兰花\n\n清淡好做，适合两个人一起吃，钠含量也控制得住。'
  assert.ok(sameSource(b, a))
  assert.ok(!sameSource('短', '另一篇很短的'))
})

/* ── 结果页视图模型 ───────────────────────────────────────────────────── */

const TYPICAL: ChefAnswer = {
  opening: '今晚这一顿清淡好做。',
  recipes: [
    {
      name: '香煎三文鱼配蒜香西兰花',
      intro: '清淡、好做，适合两个人。',
      difficulty: 3,
      nutrition: 5,
      seasonings: [{ name: '盐', amount: '少许' }],
      steps: ['第一步', '第二步'],
      image_url: null,
      image_ai_generated: true,
    },
  ],
  health_lights: [{ label: '钠', level: 'yellow', reason: '酱油别多放' }],
}

test('没有灯、没有护栏、没有依据时，整块右栏不渲染（实测 thin 形态会渲染成空圆角框）', () => {
  const vm = buildResultVM({ recipes: [] }, '', '想吃点清淡的', 0, guardText)
  assert.equal(vm.rail.display, 'hide')
  assert.equal(vm.notice.display, 'hide')
  assert.ok(!isShown(vm.rail))
})

test('只有依据时右栏渲染，但「这顿要注意的」那块不渲染', () => {
  const vm = buildResultVM(
    { recipes: [], sources: [{ source: '1_指南.pdf' }] },
    '',
    '想吃点清淡的',
    0,
    guardText,
  )
  assert.equal(vm.rail.display, 'show')
  assert.equal(vm.notice.display, 'hide')
  assert.equal(vm.process.sources, 1)
})

test('灯级 → 语义色的映射只在这里判，且未知灯级不炸', () => {
  const vm = buildResultVM(
    {
      recipes: [],
      health_lights: [
        { label: '钠', level: 'red', reason: '偏高' },
        { label: '糖', level: 'yellow' },
        { label: '蛋白质', level: 'green', reason: '充足' },
      ],
    },
    '',
    'x',
    0,
    guardText,
  )
  assert.deepEqual(vm.ranked.map((l) => l.tone), ['risk', 'warn', 'ok'])
  assert.deepEqual(vm.ranked.map((l) => l.label), ['钠', '糖', '蛋白质'], '排序按严重度')
  assert.equal(vm.yellows[0].reason.display, 'hide', '没给理由就是 hide，不是空串')
})

test('字段分级：absent / empty / invalid 都要区分开', () => {
  const vm = buildResultVM(
    { recipes: [{ name: 'A', difficulty: NaN, steps: [] } as never] } as never,
    '', '', 0, guardText,
  )
  assert.equal(vm.difficulty.state, 'invalid', 'NaN 也是 number，但它是坏值不是有值')
  assert.equal(vm.difficulty.display, 'hide')
  assert.equal(vm.steps.state, 'empty', '后端给了空数组')
  assert.equal(vm.seasonings.state, 'absent', '后端压根没给这个字段')
  assert.equal(vm.rest.state, 'empty')
  assert.equal(vm.chefTip.state, 'absent', '后端压根没给这个字段')
})

test('每一项的 state 与 reason 都留得下来（否则测试分不清"为空"和"坏了"）', () => {
  const vm = buildResultVM({ recipes: [] }, '', '', 0, guardText)
  assert.equal(vm.chefTip.display, 'hide')
  assert.equal(vm.chefTip.state, 'absent')
  assert.ok(vm.chefTip.reason && vm.chefTip.reason.length > 0)
})

test('chef_tip 与 intro 走的是"非空才显示"，全空白等于没有', () => {
  const vm = buildResultVM(
    { recipes: [{ name: 'A', intro: '   ' } as never], chef_tip: '  ' },
    '',
    '',
    0,
    guardText,
  )
  assert.equal(vm.chefTip.display, 'hide')
  assert.equal(vm.intro.display, 'hide')
})

test('真实满数据形态：三个指标、护栏翻译、来源条数都对得上', () => {
  const vm = buildResultVM(
    {
      ...TYPICAL,
      guardrails: [{ condition: '高血压', status: 'pass', reason: '已符合高血压膳食原则' }],
      sources: [{ source: '1_指南.pdf' }, { source: '2_文献.pdf' }],
    },
    '',
    '[当前位置：112.383184,28.542730]\r\n想吃鱼',
    354700,
    guardText,
  )
  assert.equal(vm.echo.display, 'show')
  assert.equal(isShown(vm.echo) && vm.echo.data, '想吃鱼')
  assert.equal(vm.difficulty.display, 'show')
  assert.equal(vm.nutrition.display, 'show')
  assert.equal(vm.notice.display, 'show', '有灯也有护栏')
  assert.equal(vm.process.sources, 2)
  assert.equal(vm.guardrails[0].condition, '高血压')
  assert.equal(isShown(vm.guardrails[0].text) && vm.guardrails[0].text.data, '已符合高血压膳食原则')
})

/* ── 家人两列 ─────────────────────────────────────────────────────────── */

test('members：实测那条答案里调整文案与矩阵是同一件事，合成后不重复印', () => {
  const vm = buildMembersVM(
    [{ dish: '糖色香菇红烧肉', member: '我', verdict: '需调整', reason: '主食减半、蔬菜加量、不额外淋油；保留优质蛋白质' }],
    ['我：「糖色香菇红烧肉（全家可吃版）」主食减半、蔬菜加量、不额外淋油；保留优质蛋白质'],
  )
  assert.ok(vm)
  assert.deepEqual(vm!.order, ['我'])
  assert.equal(vm!.lines['我'].length, 1, '一人一行')
  assert.equal(vm!.lines['我'][0].verdict, '需调整')
  assert.equal(vm!.lines['我'][0].body, '主食减半、蔬菜加量、不额外淋油；保留优质蛋白质')
  assert.equal(vm!.showDish, false, '单菜答案里不重复印菜名')
})

test('members：矩阵里没有的人不凭空开一列，归到"其他"', () => {
  // 这是**原有行为**，不是缺陷：调整文案里会出现矩阵里没有的人（实测有"客厅那位"这种）。
  // 给矩阵里不存在的人开一列，等于替后端编结构。
  const vm = buildMembersVM([], ['小美：少放盐'])
  assert.ok(vm)
  assert.deepEqual(vm!.order, [])
  assert.deepEqual(vm!.orphans, ['小美：少放盐'])
})

test('members：完全没数据时返回 null（上层才知道该不该渲染整块）', () => {
  assert.equal(buildMembersVM([], []), null)
  assert.equal(buildMembersVM([], ['   ']), null)
})

test('members：归不到人的调整文案进"其他"，不丢', () => {
  const vm = buildMembersVM([{ dish: 'A', member: '我', verdict: '可吃' }], ['客厅那位：少放辣'])
  assert.ok(vm)
  assert.deepEqual(vm!.orphans, ['客厅那位：少放辣'])
})

test('members：多个菜名时才在每行印菜名', () => {
  const vm = buildMembersVM(
    [
      { dish: 'A', member: '我', verdict: '可吃', reason: 'r1' },
      { dish: 'B', member: '我', verdict: '需调整', reason: 'r2' },
    ],
    [],
  )
  assert.ok(vm)
  assert.equal(vm!.showDish, true)
  assert.equal(vm!.lines['我'].length, 2)
  assert.deepEqual(vm!.lines['我'].map((l) => l.dish), ['A', 'B'])
})

/* ── 散文结局 ─────────────────────────────────────────────────────────── */

test('散文：首行是 Markdown 标题时提到标题位，正文里不再重复', () => {
  const vm = buildProseVM('## 今晚吃清蒸鱼\n\n鱼洗净上锅，八分钟即可。', '想吃鱼')
  assert.equal(isShown(vm.heading) && vm.heading.data, '今晚吃清蒸鱼')
  assert.ok(!vm.text.includes('## 今晚吃清蒸鱼'))
  assert.ok(vm.text.includes('八分钟'))
})

test('散文：首行不是标题时全文按正文渲染', () => {
  const vm = buildProseVM('鱼洗净上锅，八分钟即可。', '想吃鱼')
  assert.equal(vm.heading.display, 'hide')
  assert.equal(vm.text, '鱼洗净上锅，八分钟即可。')
})

test('散文：正文全空时给的是明确的空结果话术，不是空白页', () => {
  const vm = buildProseVM('   ', '想吃鱼')
  assert.equal(vm.empty.display, 'show')
  assert.equal(vm.text, '')
  assert.equal(vm.echo.display, 'show')
})

test('散文：空白行开头的标题也能被认出来', () => {
  const vm = buildProseVM('\n\n# 标题\n正文', 'x')
  assert.equal(isShown(vm.heading) && vm.heading.data, '标题')
  assert.equal(vm.text, '正文')
})
