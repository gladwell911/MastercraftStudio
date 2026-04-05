import argparse
import csv
import datetime as dt
import hashlib
import hmac
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Tuple


HOST = "open.volcengineapi.com"
ENDPOINT = f"https://{HOST}/"
SERVICE = "speech_saas_prod"
REGION = "cn-beijing"
ACTION = "ListSpeakers"
VERSION = "2025-05-20"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def build_authorization(
    ak: str,
    sk: str,
    amz_date: str,
    date_stamp: str,
    canonical_query: str,
    payload_hash: str,
) -> str:
    canonical_uri = "/"
    canonical_headers = f"host:{HOST}\n" f"x-content-sha256:{payload_hash}\n" f"x-date:{amz_date}\n"
    signed_headers = "host;x-content-sha256;x-date"
    canonical_request = "\n".join(
        [
            "POST",
            canonical_uri,
            canonical_query,
            canonical_headers,
            signed_headers,
            payload_hash,
        ]
    )
    scope = f"{date_stamp}/{REGION}/{SERVICE}/request"
    string_to_sign = "\n".join(
        [
            "HMAC-SHA256",
            amz_date,
            scope,
            sha256_hex(canonical_request.encode("utf-8")),
        ]
    )

    k_date = hmac_sha256(("VOLC" + sk).encode("utf-8"), date_stamp)
    k_region = hmac.new(k_date, REGION.encode("utf-8"), hashlib.sha256).digest()
    k_service = hmac.new(k_region, SERVICE.encode("utf-8"), hashlib.sha256).digest()
    k_signing = hmac.new(k_service, b"request", hashlib.sha256).digest()
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return (
        "HMAC-SHA256 "
        f"Credential={ak}/{scope}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )


def call_list_speakers(
    ak: str,
    sk: str,
    resource_ids: List[str],
    page: int,
    limit: int,
) -> Dict:
    now = dt.datetime.utcnow()
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")

    query_pairs = [("Action", ACTION), ("Version", VERSION)]
    canonical_query = urllib.parse.urlencode(sorted(query_pairs))

    body_obj = {
        "ResourceIDs": resource_ids,
        "Page": page,
        "Limit": limit,
    }
    body = json.dumps(body_obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    payload_hash = sha256_hex(body)

    auth = build_authorization(
        ak=ak,
        sk=sk,
        amz_date=amz_date,
        date_stamp=date_stamp,
        canonical_query=canonical_query,
        payload_hash=payload_hash,
    )
    url = f"{ENDPOINT}?{canonical_query}"
    req = urllib.request.Request(
        url=url,
        data=body,
        method="POST",
        headers={
            "Host": HOST,
            "Content-Type": "application/json; charset=UTF-8",
            "X-Date": amz_date,
            "X-Content-Sha256": payload_hash,
            "Authorization": auth,
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8"))


def extract_rows(speakers: List[Dict]) -> List[Dict]:
    rows = []
    for s in speakers:
        voice_type = s.get("VoiceType", "")
        rows.append(
            {
                "VoiceType": voice_type,
                "Name": s.get("Name", ""),
                "ResourceID": s.get("ResourceID", ""),
                "Language": ",".join([x.get("Language", "") for x in s.get("Languages", []) if isinstance(x, dict)]),
                "Categories": json.dumps(s.get("Categories", []), ensure_ascii=False),
                "NormalLabels": ",".join(s.get("NormalLabels", []) or []),
                "SpecialLabels": ",".join(s.get("SpecialLabels", []) or []),
                "Description": s.get("Description", ""),
                "PrefixGroup": classify_voice(voice_type),
            }
        )
    return rows


def classify_voice(voice_type: str) -> str:
    if voice_type.startswith("ICL_"):
        return "official_clone_icl"
    if voice_type.startswith("saturn_"):
        return "official_clone_saturn"
    if voice_type.startswith("S_"):
        return "custom_clone_s"
    return "other"


def write_csv(path: str, rows: List[Dict]) -> None:
    fields = [
        "VoiceType",
        "Name",
        "ResourceID",
        "Language",
        "Categories",
        "NormalLabels",
        "SpecialLabels",
        "Description",
        "PrefixGroup",
    ]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export speaker list from Volcengine ListSpeakers API."
    )
    parser.add_argument("--ak", default=os.getenv("VOLC_AK", ""), help="Volcengine AccessKey ID")
    parser.add_argument("--sk", default=os.getenv("VOLC_SK", ""), help="Volcengine SecretAccessKey")
    parser.add_argument(
        "--resources",
        default="seed-tts-2.0,seed-tts-1.0",
        help="Comma-separated ResourceIDs, e.g. seed-tts-2.0",
    )
    parser.add_argument("--limit", type=int, default=100, help="Page size")
    parser.add_argument("--out-csv", default="voices_export.csv", help="Output CSV path")
    parser.add_argument("--out-json", default="voices_export.json", help="Output JSON path")
    args = parser.parse_args()

    if not args.ak or not args.sk:
        print("Missing credentials: pass --ak --sk or set VOLC_AK/VOLC_SK.", file=sys.stderr)
        return 2

    resource_ids = [x.strip() for x in args.resources.split(",") if x.strip()]
    all_speakers: List[Dict] = []
    page = 1
    total = None

    try:
        while True:
            data = call_list_speakers(
                ak=args.ak,
                sk=args.sk,
                resource_ids=resource_ids,
                page=page,
                limit=args.limit,
            )
            if data.get("ResponseMetadata", {}).get("Error"):
                print(json.dumps(data, ensure_ascii=False, indent=2), file=sys.stderr)
                return 3

            result = data.get("Result", {})
            speakers = result.get("Speakers", []) or []
            if total is None:
                total = int(result.get("Total", 0) or 0)
            all_speakers.extend(speakers)
            if not speakers or len(all_speakers) >= total:
                break
            page += 1
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        print(f"HTTPError {e.code}: {body}", file=sys.stderr)
        return 4
    except Exception as e:
        print(f"Request failed: {e}", file=sys.stderr)
        return 5

    rows = extract_rows(all_speakers)
    write_csv(args.out_csv, rows)
    with open(args.out_json, "w", encoding="utf-8") as f:
        json.dump(all_speakers, f, ensure_ascii=False, indent=2)

    grouped = {
        "official_clone_icl": 0,
        "official_clone_saturn": 0,
        "custom_clone_s": 0,
        "other": 0,
    }
    for r in rows:
        grouped[r["PrefixGroup"]] += 1

    print(f"Exported {len(rows)} voices -> {args.out_csv} / {args.out_json}")
    print("Grouped counts:", grouped)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

