# app/routers/outbox_scheduler.py
import os, time, traceback, requests

BASE = os.environ.get("APP_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
INTERVAL = int(os.environ.get("SCHED_INTERVAL_SECONDS", "60"))

# Add more enqueue endpoints here as you grow (e.g., chasing)
URLS = [
    "/api/statement_reminders/statements/enqueue-due",
    "/api/chasing_reminders/enqueue-due",  
]

def tick():
    for path in URLS:
        url = BASE + path
        try:
            r = requests.post(url, timeout=10)
            print(f"[scheduler] POST {url} -> {r.status_code}", flush=True)
            if r.status_code >= 400:
                print(f"[scheduler] body: {r.text[:500]}", flush=True)
        except Exception:
            traceback.print_exc()

def main():
    print(f"[scheduler] starting base={BASE} interval={INTERVAL}s", flush=True)
    while True:
        t0 = time.time()
        tick()
        time.sleep(max(1, INTERVAL - int(time.time() - t0)))

if __name__ == "__main__":
    main()
