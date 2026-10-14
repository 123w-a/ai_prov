/**
 * 食材注册表的测试。用 `node --test` 直接跑（本机 node 支持 type stripping）：
 *
 *     node --test src/next/data/ingredients.test.ts
 *
 * 最要紧的是两条，合起来就是「永不闪破图」的证明：
 *   ① slugOf 对未登记的写法返回 null —— 不猜、不模糊匹配、不做前缀命中；
 *   ② ingredientImage 对未登记的名字**不产出任何 URL** —— 于是页面上连 <img> 都没有，
 *      也就不存在"先发请求、再 404、再消失"的那一帧。
 * 末尾三条把注册表和磁盘钉在一起：登记了却没放图就是破图，
 * 「表 = 事实」这条不变量必须有门禁，否则它只是注释里的一句愿望。
 *
 * 2026-10-12 第二轮（8 → 40 项）补的一条**反向**不变量：
 *   上一版只证了「登记了→有图」，没证「有图→登记了」。
 *   后者才是"这张图会不会永远不显示"的那一半——多放一张没人登记的 png，
 *   文件在、测试绿、页面永远不出它。所以 ⑥ 里加了孤儿文件扫描（双向闭合）。
 *   同轮还加了 ⑦：白萝卜 / 胡萝卜 这对**名字互相包含**的登记项必须各自独立，
 *   它俩是把 slugOf 换成任何前缀/子串匹配时第一个被混掉的组合。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { INGREDIENTS, INGREDIENTS_WITH_IMAGE, ingredientImage, normalizeName, slugOf } from './ingredients.ts'

/** public/assets/food/ingredients/ —— 从 src/next/data/ 往上三级就是 web 根。 */
const IMAGE_DIR = fileURLToPath(new URL('../../../public/assets/food/ingredients/', import.meta.url))

/* ── ① 别名命中 ──────────────────────────────────────────────────────── */

test('slugOf：规范名自身命中', () => {
  assert.equal(slugOf('鸡蛋'), '鸡蛋')
  assert.equal(slugOf('西兰花'), '西兰花')
  assert.equal(slugOf('面条'), '面条')
})

test('slugOf：别名收敛到规范名（首批八项逐条）', () => {
  const cases: Array<[string, string]> = [
    ['土鸡蛋', '鸡蛋'],
    ['蛋', '鸡蛋'],
    ['笨鸡蛋', '鸡蛋'],
    ['绿花菜', '西兰花'],
    ['西蓝花', '西兰花'],
    ['挂面', '面条'],
    ['手擀面', '面条'],
    ['面', '面条'],
    ['蒜', '大蒜'],
    ['蒜瓣', '大蒜'],
    ['蒜头', '大蒜'],
    ['西红柿', '番茄'],
    ['马铃薯', '土豆'],
    ['洋芋', '土豆'],
    ['柿子椒', '青椒'],
    ['菜椒', '青椒'],
    ['北豆腐', '豆腐'],
    ['嫩豆腐', '豆腐'],
    ['老豆腐', '豆腐'],
  ]
  for (const [written, canonical] of cases) {
    assert.equal(slugOf(written), canonical, `${written} 应归到 ${canonical}`)
  }
})

/* ── ② 未登记一律 null（不猜） ───────────────────────────────────────── */

test('slugOf：真实冰箱里会有、但注册表里确实没有的食材返回 null', () => {
  // 第二轮补到 40 项之后，这一串只剩"仍然没登记"的写法（茄子/排骨/香菇/
  // 胡萝卜/生姜 都已出图，不能再放在这里当反例，否则测试自己会变红）。
  for (const name of ['秋葵', '小米椒', '丝瓜', '山药', '莲藕', '冬瓜', '紫甘蓝']) {
    assert.equal(slugOf(name), null, `${name} 没登记，就不许被认出来`)
  }
})

test('slugOf：不做子串/前缀猜命中（这是"不猜"最容易被破坏的一处）', () => {
  // 别名表里有 '蛋'、'面'、'蒜' —— 这三个字最容易把长词错误吸进来。
  assert.equal(slugOf('蛋糕'), null, '蛋糕不是鸡蛋')
  assert.equal(slugOf('面包'), null, '面包不是面条')
  assert.equal(slugOf('蒜蓉'), null, '蒜蓉是调料形态，没登记就不配图')
  assert.equal(slugOf('蛋饺'), null)
  assert.equal(slugOf('面条机'), null)
})

test('slugOf：空串 / 纯标点 / 纯空白 都落 null', () => {
  for (const name of ['', '   ', '，。、', '\u3000', '（）']) {
    assert.equal(slugOf(name), null, JSON.stringify(name))
  }
})

test('slugOf：非字符串（后端可能给回 null/数字）不抛错，落 null', () => {
  for (const bad of [null, undefined, 42] as unknown[]) {
    assert.equal(slugOf(bad as string), null)
  }
})

/* ── ③ 归一化：空白 / 标点 / 全角 ────────────────────────────────────── */

test('normalizeName：去首尾与中间空白', () => {
  assert.equal(normalizeName(' 鸡蛋 '), '鸡蛋')
  assert.equal(normalizeName('鸡 蛋'), '鸡蛋')
  assert.equal(normalizeName('鸡蛋\u3000'), '鸡蛋', '全角空格也要去掉')
})

test('normalizeName：中英标点都去掉', () => {
  assert.equal(normalizeName('鸡蛋。'), '鸡蛋')
  assert.equal(normalizeName('鸡蛋，'), '鸡蛋')
  assert.equal(normalizeName('鸡蛋, '), '鸡蛋')
  assert.equal(normalizeName('（鸡蛋）'), '鸡蛋')
  assert.equal(normalizeName('西\u00b7红\u00b7柿'), '西红柿', '间隔号也是噪声')
})

test('normalizeName：全角折半角（含全角标点/数字/字母）', () => {
  assert.equal(normalizeName('鸡蛋１'), '鸡蛋1')
  assert.equal(normalizeName('ＡＢ'), 'AB')
  assert.equal(normalizeName('鸡蛋，西兰花'), '鸡蛋西兰花', '全角逗号折成半角后落进同一张标点表')
})

test('标点/空白归一化之后仍能命中：同一食材的多种写法只出一个规范名', () => {
  for (const written of [' 土鸡蛋 ', '土鸡蛋。', '土,鸡 蛋', '土鸡蛋，']) {
    assert.equal(slugOf(written), '鸡蛋', JSON.stringify(written))
  }
})

test('normalizeName 之后仍不命中：归一化只合并写法，不制造新词', () => {
  assert.equal(slugOf('鸡，蛋。'), '鸡蛋', '这是同一写法的标点噪声，应当合并')
  assert.equal(slugOf('鸡蛋西红柿'), null, '把两样拼在一起不是任何一项登记过的写法')
})

/* ── ④ 未登记者不产出 URL（关键证明） ───────────────────────────────── */

test('ingredientImage：未登记的食材**不产出任何 URL**', () => {
  const unregistered = [
    '秋葵', '丝瓜', '山药', // 确实没登记
    '蛋糕', '面包', '蛋饺', '面条机', // 含登记项的字符，但不是同一个东西
    '火腿肠', '花生酱', '姜糖', // 下位/制品：图只画了一种形态，不认形态不同的写法
    '', '   ',
  ]
  for (const name of unregistered) {
    assert.equal(ingredientImage(name), null, `${JSON.stringify(name)} 不该产出图片 URL`)
  }
})

test('ingredientImage 的值域只有两种：null，或已出图规范名的路径', () => {
  // 遍历全部别名 + 一批未登记写法：**凡产出 URL 的，其规范名必在已出图集合内**。
  // 这条挡的是"命中别名表就直接给 URL"那种写法（它会漏掉集合判定这一层）。
  const probes = [
    ...Object.keys(INGREDIENTS).flatMap((canonical) => [...INGREDIENTS[canonical]]),
    '秋葵',
    '蛋糕',
    '',
    ' 土鸡蛋 ',
  ]
  for (const written of probes) {
    const url = ingredientImage(written)
    const canonical = slugOf(written)
    if (url === null) {
      assert.ok(canonical === null || !INGREDIENTS_WITH_IMAGE.includes(canonical), `${written} 落了 null 却有图`)
      continue
    }
    if (canonical === null) assert.fail(`${written} 产出了 URL 却认不出规范名`)
    assert.ok(INGREDIENTS_WITH_IMAGE.includes(canonical), `${written} 产出了 URL，但 ${canonical} 不在已出图集合里`)
    assert.equal(url, `/assets/food/ingredients/${encodeURIComponent(canonical)}.png`)
  }
})

test('ingredientImage：已出图集合每一项都能产出 URL，且路径就是 <规范名>.png', () => {
  for (const canonical of INGREDIENTS_WITH_IMAGE) {
    assert.equal(ingredientImage(canonical), `/assets/food/ingredients/${encodeURIComponent(canonical)}.png`)
  }
})

test('ingredientImage：别名与规范名产出的 URL 完全相同（同一张图）', () => {
  assert.equal(ingredientImage('土鸡蛋'), ingredientImage('鸡蛋'))
  assert.equal(ingredientImage('西红柿'), ingredientImage('番茄'))
  assert.equal(ingredientImage('马铃薯'), ingredientImage('土豆'))
  assert.equal(ingredientImage('挂面'), ingredientImage('面条'))
})

/* ── ⑤ 注册表自身的一致性 ────────────────────────────────────────────── */

test('每个规范名的别名表都包含它自己（否则 slugOf(规范名) 会落 null）', () => {
  for (const [canonical, aliases] of Object.entries(INGREDIENTS)) {
    assert.ok(aliases.includes(canonical), `${canonical} 的别名表里少了它自己`)
  }
})

test('别名归一化之后不许撞车（同一写法不能指向两个规范名）', () => {
  const seen = new Map<string, string>()
  for (const [canonical, aliases] of Object.entries(INGREDIENTS)) {
    for (const alias of aliases) {
      const key = normalizeName(alias)
      const owner = seen.get(key)
      assert.equal(owner ?? canonical, canonical, `写法 ${alias} 同时属于 ${owner} 与 ${canonical}`)
      seen.set(key, canonical)
    }
  }
})

test('已出图集合里的规范名必须先登记过别名', () => {
  for (const canonical of INGREDIENTS_WITH_IMAGE) {
    assert.ok(canonical in INGREDIENTS, `${canonical} 出了图却没登记别名`)
  }
})

test('2026-10-12 第二轮：已出图集合恰好 40 项，且与注册表一一对应', () => {
  assert.equal(INGREDIENTS_WITH_IMAGE.length, 40, '第二轮验收口径就是 40 样')
  assert.equal(new Set(INGREDIENTS_WITH_IMAGE).size, 40, '集合里不许有重复项')
  for (const canonical of INGREDIENTS_WITH_IMAGE) {
    assert.ok(canonical in INGREDIENTS, `${canonical} 在已出图集合里却不在注册表`)
  }
})

test('第二轮新收录的写法逐条收敛到规范名', () => {
  // 只列"用户真会写、且与图里画的那一种形态一致"的写法；
  // 形态不同的下位词（火腿肠/花生酱）在 ④ 里断言仍然落 null。
  const cases: Array<[string, string]> = [
    ['大白菜', '白菜'],
    ['结球生菜', '生菜'],
    ['莜麦菜', '油麦菜'],
    ['青瓜', '黄瓜'],
    ['矮瓜', '茄子'],
    ['红萝卜', '胡萝卜'],
    ['萝卜', '白萝卜'],
    ['葱头', '洋葱'],
    ['苞米', '玉米'],
    ['倭瓜', '南瓜'],
    ['冬菇', '香菇'],
    ['金菇', '金针菇'],
    ['黄豆芽', '豆芽'],
    ['鸡胸', '鸡胸肉'],
    ['琵琶腿', '鸡腿'],
    ['里脊', '猪肉'],
    ['肋排', '排骨'],
    ['牛腱', '牛肉'],
    ['对虾', '虾'],
    ['鲈鱼', '鱼'],
    ['餐肉', '午餐肉'],
    ['金华火腿', '火腿'],
    ['白米', '大米'],
    ['馍', '馒头'],
    ['老姜', '生姜'],
    ['小葱', '葱'],
    ['大葱', '葱'],
    ['芫荽', '香菜'],
    ['落花生', '花生'],
    ['植物油', '食用油'],
    ['生抽', '酱油'],
    ['精盐', '食盐'],
  ]
  for (const [written, canonical] of cases) {
    assert.equal(slugOf(written), canonical, `${written} 应归到 ${canonical}`)
  }
})

/* ── ⑥ 表与磁盘钉在一起 ──────────────────────────────────────────────── */

test('登记了就必须放图：每个已出图规范名都有一张同名 png', () => {
  for (const canonical of INGREDIENTS_WITH_IMAGE) {
    const file = join(IMAGE_DIR, `${canonical}.png`)
    assert.ok(existsSync(file), `登记了却没放图（页面上会变成破图）：${file}`)
  }
})

test('放了图就必须登记（反向闭合）：磁盘上不许有孤儿 png', () => {
  // 上一版只证了一个方向。孤儿文件比"缺图"更隐蔽：
  // 文件在、门禁绿、页面永远不出它，谁也不会发现。
  const registered = new Set(INGREDIENTS_WITH_IMAGE.map((n) => `${n}.png`))
  const onDisk = readdirSync(IMAGE_DIR).filter((f) => f.toLowerCase().endsWith('.png'))
  assert.equal(onDisk.length, 40, `磁盘上应有 40 张，实际 ${onDisk.length} 张`)
  for (const file of onDisk) {
    assert.ok(registered.has(file), `孤儿图（放了却没登记，永远不会显示）：${file}`)
  }
})

test('图必须是 256×256：读 PNG 头 IHDR 的宽高，不靠猜', () => {
  for (const canonical of INGREDIENTS_WITH_IMAGE) {
    const buf = readFileSync(join(IMAGE_DIR, `${canonical}.png`))
    assert.ok(buf.subarray(1, 4).toString('latin1') === 'PNG', `${canonical}.png 不是 PNG`)
    assert.equal(buf.readUInt32BE(16), 256, `${canonical}.png 宽不是 256`)
    assert.equal(buf.readUInt32BE(20), 256, `${canonical}.png 高不是 256`)
  }
})

test('没有 .tmp 半写残留（压图脚本用临时文件 + os.replace，失败会留下 .tmp）', () => {
  const leftovers = readdirSync(IMAGE_DIR).filter((f) => f.endsWith('.tmp'))
  assert.deepEqual(leftovers, [], `存在半写残留：${leftovers.join('、')}`)
})

/* ── ⑦ 名字互相包含的一对：白萝卜 / 胡萝卜 ───────────────────────────── */

test('白萝卜与胡萝卜各自独立：互不收录，且都能各自命中', () => {
  // 这是全表唯一一对"一个规范名是另一个的子串"的登记项。
  // 任何把 slugOf 改成前缀/子串匹配的改动都会在这里当场变红——
  // 那正是它必须存在的原因，而不是"看着像就顺手写一条"。
  assert.equal(slugOf('白萝卜'), '白萝卜')
  assert.equal(slugOf('胡萝卜'), '胡萝卜')
  assert.equal(slugOf('萝卜'), '白萝卜', '口语里"萝卜"单说就是白萝卜')
  assert.equal(slugOf('红萝卜'), '胡萝卜')

  assert.ok(!INGREDIENTS['白萝卜'].includes('胡萝卜'), '白萝卜的别名表里不许出现胡萝卜')
  assert.ok(!INGREDIENTS['胡萝卜'].includes('白萝卜'), '胡萝卜的别名表里不许出现白萝卜')

  const white = new Set(INGREDIENTS['白萝卜'].map(normalizeName))
  const red = new Set(INGREDIENTS['胡萝卜'].map(normalizeName))
  for (const key of white) assert.ok(!red.has(key), `写法 ${key} 同时属于两个萝卜`)
})

test('两个萝卜是不同的图，不是同一张', () => {
  assert.notEqual(ingredientImage('白萝卜'), ingredientImage('胡萝卜'))
})
