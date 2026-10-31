"""Live discovery test across professions. Usage: python tests/discovery_any_profession.py <user_id> "<location>" <days>"""
import json, sys, time
sys.path.insert(0, ".")
from app.discovery import search
uid, loc, days = int(sys.argv[1]), sys.argv[2], int(sys.argv[3])
t = time.time()
jobs, errors, meta = search(uid, location=loc, days=days)
print(json.dumps({"user": uid, "roles": meta["roles"], "method": meta["profile"]["method"], "where": meta["location"],
                  "raw": meta["raw_counts"], "kept": meta["kept_counts"], "total": len(jobs), "secs": round(time.time() - t),
                  "errors": errors[:5],
                  "top": [{"score": j["match_score"], "title": j["title"], "company": j["company"], "loc": j["location"],
                           "src": j["source"], "url": j["url"]} for j in jobs[:8]]}, indent=1, default=str))
