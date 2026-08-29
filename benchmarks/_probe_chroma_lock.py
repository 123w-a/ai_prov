import sys, io, traceback
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\develop\ai_prov")
try:
    import chromadb
    cl = chromadb.PersistentClient(path=r"D:\develop\ai_prov\kb_corpus_chroma")
    col = cl.get_or_create_collection("kb")
    print("CHROMA-OK", col.count())
except Exception:
    traceback.print_exc()
