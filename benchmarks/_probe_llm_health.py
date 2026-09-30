import io, sys, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\develop\ai_prov")
from model_name import get_langchain_llm
t0 = time.time()
try:
    llm = get_langchain_llm("deepseek", temperature=0, max_tokens=5)
    r = llm.invoke([("human", "hi")])
    print("OK", round(time.time() - t0, 1), "s:", str(r.content)[:40])
except Exception as e:
    print("FAIL", round(time.time() - t0, 1), "s:", type(e).__name__, str(e)[:150])
