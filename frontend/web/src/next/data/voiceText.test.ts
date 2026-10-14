/**
 * 语音插入与「出去吃」标注的纯逻辑测试。`node --test src/next/data/voiceText.test.ts`。
 *
 * 这些断言对着验收里点名的行为写，不是对着实现写：
 * 「追加到光标处不覆盖」「空结果不动草稿」「权限被拒 ≠ 不支持」「没定位不许说已定位」。
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import {
  caretSpan,
  insertTranscript,
  voiceErrorMessage,
  EMPTY_TRANSCRIPT_NOTE,
} from './voiceText.ts'
import {
  nearbyKeyword,
  keywordNote,
  geoSelfNote,
  navUrlFor,
  GEO_UNAVAILABLE_NOTE,
} from './nearbyQuery.ts'

test('caretSpan：越界与缺省都收进合法区间，且 start ≤ end', () => {
  assert.deepEqual(caretSpan('abcdef', 3, 5), { start: 3, end: 5 })
  assert.deepEqual(caretSpan('abcdef', null, null), { start: 6, end: 6 })
  // 草稿被别处改短过：超界截回末尾，不抛错、不落到串外
  assert.deepEqual(caretSpan('ab', 9, 12), { start: 2, end: 2 })
  assert.deepEqual(caretSpan('abcdef', -4, 2), { start: 0, end: 2 })
  // 反向选区（右键拖选会产生）归一成空选区，不产生负长度
  assert.deepEqual(caretSpan('abcdef', 5, 1), { start: 5, end: 5 })
  assert.deepEqual(caretSpan('abcdef', 2.7, 4.9), { start: 2, end: 4 })
})

test('insertTranscript：空草稿 → 就是识别文本', () => {
  const r = insertTranscript('', { start: 0, end: 0 }, '今晚想吃鱼')
  assert.equal(r.next, '今晚想吃鱼')
  assert.equal(r.caret, 5)
})

test('insertTranscript：光标在末尾追加，且补一个分隔空格（不粘连）', () => {
  const text = '冰箱里只有鸡蛋和西兰花'
  const r = insertTranscript(text, { start: text.length, end: text.length }, '今晚想吃鱼')
  assert.equal(r.next, '冰箱里只有鸡蛋和西兰花 今晚想吃鱼')
  assert.equal(r.caret, r.next.length)
  // 原有内容一个字符都没被覆盖
  assert.ok(r.next.startsWith(text))
})

test('insertTranscript：光标在中间插入，前后原样保留', () => {
  const r = insertTranscript('吃饭', { start: 1, end: 1 }, '想')
  assert.equal(r.next, '吃 想饭')
  assert.equal(r.caret, 3)
})

test('insertTranscript：已有选区 → 替换选区（不追加两份）', () => {
  const r = insertTranscript('今晚想吃鱼', { start: 4, end: 6 }, '喝汤')
  assert.equal(r.next, '今晚想吃 喝汤')
})

test('insertTranscript：空白识别结果不动草稿', () => {
  const text = '已经写好的草稿'
  for (const empty of ['', '   ', '\n\t ']) {
    const r = insertTranscript(text, { start: 3, end: 3 }, empty)
    assert.equal(r.next, text, `识别结果 ${JSON.stringify(empty)} 不该改草稿`)
    assert.equal(r.caret, 3)
  }
})

test('voiceErrorMessage：权限被拒 ≠ 不支持，要写清原因与可重试', () => {
  const denied = voiceErrorMessage(new DOMException('Permission denied', 'NotAllowedError'), 'mic')
  assert.match(denied, /权限被拒绝/)
  assert.match(denied, /重试/)
  assert.doesNotMatch(denied, /不支持/)

  const notFound = voiceErrorMessage(new DOMException('x', 'NotFoundError'), 'mic')
  assert.match(notFound, /没有找到可用的麦克风/)

  // 后端 available:false / 转写失败走 transcribe 档：原话必须带出来，不吞
  const back = voiceErrorMessage(new Error('语音识别暂不可用'), 'transcribe')
  assert.match(back, /语音识别暂不可用/)
  assert.match(back, /没有成功/)
})

test('nearbyKeyword / keywordNote：空输入不编造关键词', () => {
  assert.equal(nearbyKeyword('  今晚想吃鱼  '), '今晚想吃鱼')
  assert.equal(nearbyKeyword('今晚   想吃鱼'), '今晚 想吃鱼')
  assert.equal(nearbyKeyword('   '), '')
  assert.match(keywordNote('今晚想吃鱼'), /关键词「今晚想吃鱼」/)
  assert.match(keywordNote(''), /还没有内容/)
  assert.match(keywordNote(''), /没有请求系统定位/)
})

test('geoSelfNote：只有真解析出地名才说话；解析失败该处不放任何原因文案', () => {
  // 成功态照旧（这一条不许被"取消失败文案"一起删掉）
  assert.equal(geoSelfNote(true, '杭州市西湖区', undefined), '已按你的位置查询：杭州市西湖区')

  // 解析失败：**空串**——页面不渲染任何句子，原因文案一律不上页面
  assert.equal(geoSelfNote(false, '', '逆地理未配置'), '')
  assert.equal(geoSelfNote(false, '', undefined), '')
  // 连成功的口吻也不许出现（不许把"没成功"画成"成功"）
  assert.doesNotMatch(geoSelfNote(false, '杭州市西湖区', undefined), /已按你的位置/)

  // 后端 warning 原话（含 info/infocode/控制台/.env 这类给人看的字）必须被丢掉，
  // 不许以任何形式漏进页面文案
  const backend =
    '高德逆地理编码被拒——请求 Key 与绑定的平台不符（info=USERKEY_PLAT_NOMATCH, infocode=10009）。' +
    '请在高德控制台新建一个「Web服务」类型的 Key，填进 .env 的 AMAP_KEY 并重启后端。'
  const shown = geoSelfNote(false, '', backend)
  assert.equal(shown, '')
  assert.doesNotMatch(shown, /USERKEY_PLAT_NOMATCH/)
  assert.doesNotMatch(shown, /infocode/)
  assert.doesNotMatch(shown, /10009/)
  assert.doesNotMatch(shown, /控制台/)
  assert.doesNotMatch(shown, /\.env/)
  assert.doesNotMatch(shown, /请手动选择城市/)
  // label 里带回来的原因（后端也可能把它塞进 label）同样不许上页面
  const labelLeak = geoSelfNote(false, backend, backend)
  assert.equal(labelLeak, '')
  assert.doesNotMatch(labelLeak, /USERKEY_PLAT_NOMATCH/)
})

test('坐标能力仍在：解析不出地名也照样按坐标查附近，且成功态只在 resolved 时出现', () => {
  // 源码级守卫：把"取消失败文案"误伤成"取消按坐标查询"时这条要变红。
  // （页面行为由真实浏览器验收另证；这里保的是"别顺手删掉那条链路"。）
  const src = readFileSync(new URL('./useNearby.ts', import.meta.url), 'utf8')
  // 拿到坐标后：既 setOrigin 又 load(keyword, gcj)——按坐标查
  assert.match(src, /setOrigin\(gcj\)/)
  assert.match(src, /void load\(keyword, gcj\)/)
  // 成功态文案只在 resolved 为真时才可能非空（geoSelfNote 的第一分支）
  assert.match(src, /setNote\(geoSelfNote\(r\.resolved, r\.label, r\.warning\)\)/)
})

test('GEO_UNAVAILABLE_NOTE：降级原话不许包含「已定位」', () => {
  assert.match(GEO_UNAVAILABLE_NOTE, /未获取到位置/)
  assert.doesNotMatch(GEO_UNAVAILABLE_NOTE, /已按你的位置/)
})

test('EMPTY_TRANSCRIPT_NOTE 说明草稿没动', () => {
  assert.match(EMPTY_TRANSCRIPT_NOTE, /草稿没有改动/)
})

test('navUrlFor：有坐标给高德导航链接，缺坐标给 null（不编链接）', () => {
  const withCoords = navUrlFor(
    { name: '老碗面', cuisine: '面食', avg_price: 28, lng: 120.155, lat: 30.274 },
    '120.15,30.27',
  )
  assert.ok(withCoords)
  assert.match(withCoords, /^https:\/\/uri\.amap\.com\/navigation\?/)
  assert.match(withCoords, /to=120\.155%2C30\.274%2C/)
  assert.equal(navUrlFor({ name: '无坐标店', cuisine: '', avg_price: null }, ''), null)
})
