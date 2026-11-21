"""Submit a two-date analysis against a running integration stack."""

import argparse
import json
import math
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def call(base_url, path, payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    request = Request(base_url + path, data=body, headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=15) as response:
            return json.load(response)
    except HTTPError as exc:
        raise SystemExit(f"{path}: HTTP {exc.code} {exc.read().decode(errors='replace')}") from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--dates", nargs=2, default=["2022-08-31", "2022-09-30"])
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    url = args.url.rstrip("/")
    health = call(url, "/api/v1/health")
    if health["status"] != "ok":
        raise SystemExit(f"Services are not ready: {health['services']}")
    started = call(url, "/api/v1/runs", {"dates": args.dates, "prepayment": False})
    path = f"/api/v1/runs/{started['id']}"
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        run = call(url, path)
        if run["status"] == "failed":
            raise SystemExit(f"Analysis failed: {run['error']}")
        if run["status"] == "done":
            break
        time.sleep(1)
    else:
        raise SystemExit(f"Analysis exceeded {args.timeout:g} s: {started['id']}")

    result = run["result"]
    periods = result["periods"]
    if [period["date"] for period in periods] != sorted(args.dates):
        raise SystemExit("Unexpected report dates")
    for period in periods:
        if len(period["dnpv"]) != 6 or not math.isclose(period["ec"], min(period["dnpv"].values())):
            raise SystemExit(f"Invalid stress valuation on {period['date']}")
        print(f"{period['date']} · curve {period['curve_date']} · BASE {period['npv_base']:.2f} · ΔNPV min {period['ec']:.2f}")
    for name in ("ec", "npv_base"):
        metric = result["decomposition"]["metrics"][name]
        contributions = metric["contributions"]
        if len(contributions) != 4 or not math.isclose(
            sum(row["shapley"] for row in contributions), metric["change"], rel_tol=1e-9, abs_tol=0.01
        ):
            raise SystemExit(f"Invalid Shapley decomposition for {name}")
    print(f"OK · run {started['id']} · two dates, seven scenarios and four factor groups")


if __name__ == "__main__":
    main()
