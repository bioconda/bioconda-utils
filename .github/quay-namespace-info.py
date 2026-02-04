#!/usr/bin/env python
"""
A simple script to query API to get public/private status of all repos under a namespace.

Caveat being, without an API key, you will only ever see the public repos.
"""

PROGRAM = "quay-namespace-info"
VERSION = "1.0.0"
QUAY_API_URL = "https://quay.io/api/v1/repository"

if __name__ == "__main__":
    import argparse as ap
    import requests
    import os
    import sys
    import time

    parser = ap.ArgumentParser(
        prog=PROGRAM,
        conflict_handler="resolve",
        description=(
            f"{PROGRAM} (v{VERSION}) - Check visibility of containers and optionally set to public\n"
        ),
        formatter_class=ap.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--namespace",
        metavar="STR",
        type=str,
        default="biocontainers",
        help="Namespace to query (default: biocontainers)",
    )
    parser.add_argument(
        "--changevisibility",
        action="store_true",
        help="Any private repos will be set to public, requires QUAY_OAUTH_TOKEN to be set",
    )
    parser.add_argument("--version", action="version", version=f"{PROGRAM} {VERSION}")

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    args = parser.parse_args()

    # Set headers, include OAuth token if available
    HEADERS = {"Content-Type": "application/json"}
    QUAY_OAUTH_TOKEN = os.getenv("QUAY_OAUTH_TOKEN")
    if QUAY_OAUTH_TOKEN:
        print(f"Quay Token found, using it for {args.namespace}")
        HEADERS["Authorization"] = f"Bearer {QUAY_OAUTH_TOKEN}"

    # Starting querying Quay
    next_page = ""
    has_next_page = True
    repo_status = {}
    change_visibility = []
    print(f"Starting query against {args.namespace}")
    while has_next_page:
        r = requests.get(
            QUAY_API_URL,
            headers=HEADERS,
            params={"namespace": args.namespace, "next_page": next_page}
            if QUAY_OAUTH_TOKEN
            else {
                "public": "true",
                "namespace": args.namespace,
                "next_page": next_page,
            },
            timeout=10,
        )
        json_data = r.json()
        """
        Example response from query
        {
            "repositories": [{
                "namespace": "biocontainers",
                "name": "coreutils",
                "description": "# Coreutils\n\n> The gnu core utilities are  which ...TRUNCATED",
                "is_public": true,
                "kind": "image",
                "state": "NORMAL"
            }, {
                "namespace":
                "biocontainers",
                "name": "jq",
                "description": "# Jq\n\n> Jq is a lightweight and flexible ...TRUNCATED", 
                "is_public": true, 
                "kind": "image", 
                "state": "NORMAL"
            }], 
            "next_page": "gAAAAABj0XHI4znSYv5GZ04J1cza9Q9HtUEtaksMHmrDjg98ACHvXRt56m8TnTS-mUXS09F_Px9ytXiwOMouECjX6kRE1C-jSBBZcHk8IPhPyhyPkafZMdp-euR66-SZcmH66vSKJFmbarKVWCjWbwYuhVSD6QzrZA=="
        }
        """

        # next_page is only available if there are more pages, use it to break out of while loop
        if "next_page" in json_data:
            next_page = json_data["next_page"]
        else:
            has_next_page = False

        # Capture public/private status for each repo
        for repo in json_data["repositories"]:
            repo_status[repo["name"]] = {
                "namespace": repo["namespace"],
                "name": repo["name"],
                "is_public": repo["is_public"],
            }

            # Collect repos that are private
            if repo["is_public"] is False:
                change_visibility.append(repo["name"])

        # Couldn't find a specific rate limit in the docs, so limit to max 3 per second
        time.sleep(0.3)

    # Optionally change visibility
    print(
        f"Found {len(repo_status)} repos under namespace {args.namespace} ({len(change_visibility)} private)"
    )
    if args.changevisibility and QUAY_OAUTH_TOKEN:
        print(f"Changing visibility of {len(change_visibility)} repos to public")
        with open(f"{args.namespace}-changevisibility.txt", "w") as fh:
            for repo in change_visibility:
                r = requests.post(
                    f"{QUAY_API_URL}/{args.namespace}/{repo}/changevisibility",
                    headers=HEADERS,
                    json={"visibility": "public"},
                    timeout=10,
                )
                repo_status[repo]["is_public"] = "True (changed by script)"
                # Again, be nice to Quay
                time.sleep(0.3)
                fh.write(f"Changed visibility of {args.namespace}/{repo} to public\n")

    # Print status
    with open(f"{args.namespace}-status.txt", "w") as fh:
        fh.write("namespace\tname\tis_public\n")
        for k, v in sorted(repo_status.items()):
            fh.write(f"{v['namespace']}\t{v['name']}\t{v['is_public']}\n")
