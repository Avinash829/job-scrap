"""Verify every ATS endpoint pattern and dump its real response shape."""
import asyncio, httpx, json

TESTS = [
 ("greenhouse",      "GET",  "https://boards-api.greenhouse.io/v1/boards/postman/jobs?content=true"),
 ("lever",           "GET",  "https://api.lever.co/v0/postings/meesho?mode=json"),
 ("ashby",           "GET",  "https://api.ashbyhq.com/posting-api/job-board/atlan?includeCompensation=true"),
 ("smartrecruiters", "GET",  "https://api.smartrecruiters.com/v1/companies/swiggy/postings?limit=100"),
 ("workable_widget", "GET",  "https://apply.workable.com/api/v1/widget/accounts/zapier"),
 ("recruitee",       "GET",  "https://vinted.recruitee.com/api/offers/"),
 ("personio_xml",    "GET",  "https://hellofresh.jobs.personio.de/xml"),
 ("bamboohr",        "GET",  "https://sproutsocial.bamboohr.com/careers/list"),
]

async def main():
    async with httpx.AsyncClient(headers={"User-Agent":"jobscrap/0.1"},
                                 follow_redirects=True, timeout=25) as c:
        for name, method, url in TESTS:
            try:
                r = await c.get(url)
            except Exception as e:
                print(f"{name:<18} ERROR {type(e).__name__}"); continue
            print(f"\n=== {name}  HTTP {r.status_code}  ({len(r.content)//1024}KB) ===")
            if r.status_code != 200: continue
            if name.endswith("xml"):
                print("  xml head:", r.text[:220].replace("\n"," ")); continue
            try: d = r.json()
            except Exception: print("  not json:", r.text[:120]); continue
            if isinstance(d, list):
                print(f"  ENVELOPE: bare array, n={len(d)}")
                if d: print("  keys:", sorted(d[0].keys())[:18])
            else:
                print(f"  ENVELOPE: object, top keys={sorted(d.keys())[:10]}")
                for k in ("jobs","content","results","offers","postings","data"):
                    if isinstance(d.get(k), list) and d[k]:
                        print(f"  list under '{k}', n={len(d[k])}")
                        print("  keys:", sorted(d[k][0].keys())[:20])
                        break

    # Workday needs POST
    body = {"appliedFacets":{}, "limit":20, "offset":0, "searchText":""}
    for tenant, host, site in [("nvidia","nvidia.wd5.myworkdayjobs.com","NVIDIAExternalCareerSite"),
                               ("salesforce","salesforce.wd12.myworkdayjobs.com","External_Career_Site")]:
        url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
        try:
            async with httpx.AsyncClient(timeout=25, headers={"User-Agent":"jobscrap/0.1","Content-Type":"application/json"}) as c:
                r = await c.post(url, json=body)
            print(f"\n=== workday:{tenant}  HTTP {r.status_code} ===")
            if r.status_code == 200:
                d = r.json()
                print("  top keys:", sorted(d.keys()))
                print("  total:", d.get("total"), "| returned:", len(d.get("jobPostings") or []))
                if d.get("jobPostings"): print("  keys:", sorted(d["jobPostings"][0].keys()))
        except Exception as e:
            print(f"\n=== workday:{tenant} ERROR {type(e).__name__} ===")

asyncio.run(main())
