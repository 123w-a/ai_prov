import { readFile, writeFile } from "node:fs/promises"
import { bootstrapMetricLoop } from "file:///D:/develop/Deepseek%20Harness/plugins/dsh-eval-runner/lib/index.js"

const base = JSON.parse(await readFile(new URL("./sweep_baseline.json", import.meta.url), "utf8"))
const cand = JSON.parse(await readFile(new URL("./sweep_candidate.json", import.meta.url), "utf8"))

const report = await bootstrapMetricLoop({
  cases: base.cases,
  metric: async (example, prediction) => {
    const texts = Array.isArray(prediction) ? prediction : []
    return example.expect_keywords.every((kw) => texts.some((t) => t.includes(kw)))
  },
  baselineRun: async (input) => base.runs[input] ?? [],
  candidateRun: async (input) => cand.runs[input] ?? [],
})

await writeFile(new URL("./compile_report.json", import.meta.url), JSON.stringify(report, null, 1))
console.log("VERDICT:", report.verdict, `before ${report.before.passed}/${report.before.total} -> after ${report.after.passed}/${report.after.total}`, `green=${report.green.length} red=${report.red.length}`)
if (report.verdict !== "promote") process.exit(1)