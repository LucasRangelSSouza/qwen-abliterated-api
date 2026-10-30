import asyncio, json, os, sys, time
import httpx
sys.path.insert(0, "/tmp")
import k4k5 as R

async def main():
    s = R.Settings.from_env(1200.0)
    body = R.build_body(s, tenant_id="qa-k4-k5-0", index=0)
    t0 = time.time(); n = 0
    async with httpx.AsyncClient(timeout=httpx.Timeout(1500.0)) as c:
        async with c.stream("POST", f"{s.base_url}/v1/runs", json=body, headers={"Authorization": f"Bearer {s.service_token}"}) as r:
            print("HTTP", r.status_code, flush=True)
            ev = ""; data = []
            async for line in r.aiter_lines():
                if line.startswith("event:"): ev = line[6:].strip()
                elif line.startswith("data:"): data.append(line[5:].strip())
                elif not line and ev:
                    n += 1
                    print(f"{time.time()-t0:6.1f}s {ev}: {' '.join(data)[:260]}", flush=True)
                    if ev in R.TERMINAL_EVENTS: break
                    ev, data = "", []
        st = await c.get(f"{s.base_url}/v1/runs/{body['run_id']}", params={"tenant_id": "qa-k4-k5-0"}, headers={"Authorization": f"Bearer {s.service_token}"})
        print("STATUS", st.status_code, st.text[:400])
    print(f"FIM {time.time()-t0:.1f}s eventos={n}", flush=True)
asyncio.run(main())
