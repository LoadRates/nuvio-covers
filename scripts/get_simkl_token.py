#!/usr/bin/env python3
"""
One-time helper to obtain a Simkl OAuth access token via the PIN flow.

Run this locally, then paste the token into your GitHub repo as the
SIMKL_ACCESS_TOKEN secret (Settings → Secrets and variables → Actions).

Usage:
  SIMKL_CLIENT_ID=your_client_id python3 scripts/get_simkl_token.py
"""

import os
import sys
import time

import requests

SIMKL_API = "https://api.simkl.com"


def main():
    client_id = os.environ.get("SIMKL_CLIENT_ID", "").strip()
    if not client_id:
        print("Error: set SIMKL_CLIENT_ID before running.")
        print("  export SIMKL_CLIENT_ID=your_client_id")
        sys.exit(1)

    print("Requesting PIN from Simkl...")
    resp = requests.post(
        f"{SIMKL_API}/oauth/pin",
        json={"client_id": client_id, "redirect": "urn:ietf:wg:oauth:2.0:oob"},
        timeout=15,
    )
    resp.raise_for_status()
    pin_data = resp.json()

    user_code = pin_data["user_code"]
    interval = int(pin_data.get("interval", 5))
    expires_in = int(pin_data.get("expires_in", 600))

    print(f"\n  Open this URL in your browser and click Approve:")
    print(f"\n    https://simkl.com/pin/{user_code}\n")
    print(f"  (expires in {expires_in // 60} minutes)\n")
    print("Waiting", end="", flush=True)

    deadline = time.time() + expires_in
    while time.time() < deadline:
        time.sleep(interval)
        poll = requests.get(
            f"{SIMKL_API}/oauth/pin/{user_code}",
            params={"client_id": client_id},
            timeout=15,
        )
        if poll.status_code == 200:
            token_data = poll.json()
            if token_data.get("result") == "OK" and token_data.get("access_token"):
                print("\n\nAuthorized!\n")
                print("Your SIMKL_ACCESS_TOKEN:\n")
                print(f"  {token_data['access_token']}\n")
                print("Add it to GitHub: Settings → Secrets and variables → Actions → New repository secret")
                print("  Name:  SIMKL_ACCESS_TOKEN")
                print("  Value: (paste the token above)")
                return
            print(".", end="", flush=True)
        elif poll.status_code == 400:
            print(".", end="", flush=True)
        else:
            print(f"\nUnexpected status {poll.status_code}. Aborting.")
            sys.exit(1)

    print("\nPIN expired without authorization. Run this script again.")
    sys.exit(1)


if __name__ == "__main__":
    main()
