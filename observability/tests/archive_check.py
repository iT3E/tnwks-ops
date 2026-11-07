"""Loopback-only S3 compatibility checks using disposable fixture credentials."""

import argparse
import datetime as dt
import hashlib
import hmac
import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


class S3:
    def __init__(self, directory: Path):
        self.access = (directory / "secrets/access-key").read_text().strip()
        self.secret = (directory / "secrets/secret-key").read_text().strip()
        self.endpoint = "http://127.0.0.1:13103"

    def request(self, method, key="", data=b"", query="", headers=None):
        path = "/homelab-metrics/" + urllib.parse.quote(key, safe="/")
        now = dt.datetime.now(dt.timezone.utc)
        date, stamp = now.strftime("%Y%m%d"), now.strftime("%Y%m%dT%H%M%SZ")
        payload_hash = hashlib.sha256(data).hexdigest()
        signed = {"host": "127.0.0.1:13103", "x-amz-content-sha256": payload_hash,
                  "x-amz-date": stamp}
        signed.update(headers or {})
        names = ";".join(sorted(signed))
        canonical_headers = "".join(f"{k}:{v}\n" for k, v in sorted(signed.items()))
        canonical = "\n".join([method, path, query, canonical_headers, names, payload_hash])
        scope = f"{date}/garage/s3/aws4_request"
        def sign(k, v):
            return hmac.new(k, v.encode(), hashlib.sha256).digest()
        key_bytes = ("AWS4" + self.secret).encode()
        for part in [date, "garage", "s3", "aws4_request"]:
            key_bytes = sign(key_bytes, part)
        string = "\n".join(["AWS4-HMAC-SHA256", stamp, scope,
                            hashlib.sha256(canonical.encode()).hexdigest()])
        signature = sign(key_bytes, string).hex()
        signed["Authorization"] = (f"AWS4-HMAC-SHA256 Credential={self.access}/{scope}, "
                                   f"SignedHeaders={names}, Signature={signature}")
        url = self.endpoint + path + ("?" + query if query else "")
        request = urllib.request.Request(url, method=method, data=data if method in ("PUT", "POST") else None,
                                         headers=signed)
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.read()

    def multipart_check(self):
        key = "acceptance/multipart.bin"
        raw = self.request("POST", key, query="uploads=")
        upload = ET.fromstring(raw).find("{*}UploadId").text
        parts = [b"a" * (5 * 1024 * 1024), b"b" * (5 * 1024 * 1024), b"c" * (1024 * 1024)]
        completed = ET.Element("CompleteMultipartUpload")
        for number, data in enumerate(parts, 1):
            query = urllib.parse.urlencode({"partNumber": number, "uploadId": upload})
            self.request("PUT", key, data, query=query)
            part = ET.SubElement(completed, "Part")
            ET.SubElement(part, "PartNumber").text = str(number)
            ET.SubElement(part, "ETag").text = '"' + hashlib.md5(data, usedforsecurity=False).hexdigest() + '"'
        self.request("POST", key, ET.tostring(completed), query=urllib.parse.urlencode({"uploadId": upload}))
        assert self.request("GET", key) == b"".join(parts)
        self.request("DELETE", key)
        upload = ET.fromstring(self.request("POST", key, query="uploads=")).find("{*}UploadId").text
        query = urllib.parse.urlencode({"uploadId": upload})
        self.request("DELETE", key, query=query)
        try:
            self.request("GET", key, query=query)
        except urllib.error.HTTPError as error:
            assert error.code == 404
        else:
            raise AssertionError("aborted multipart upload still exists")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("/tmp/obs-garage-rehearsal"))
    parser.add_argument("--upload-blocks", type=Path)
    parser.add_argument("--verify-restart", action="store_true")
    args = parser.parse_args()
    s3 = S3(args.directory)
    key = "acceptance/roundtrip.bin"
    body = bytes(range(256)) * 8192
    if not args.verify_restart:
        s3.request("PUT", key, body)
    assert s3.request("GET", key) == body
    assert s3.request("GET", key, headers={"range": "bytes=10-31"}) == body[10:32]
    assert key.encode() in s3.request("GET", query="list-type=2&prefix=acceptance%2F")
    if args.verify_restart:
        s3.request("DELETE", key)
        assert key.encode() not in s3.request("GET", query="list-type=2&prefix=acceptance%2F")
    if args.upload_blocks:
        blocks = [p for p in args.upload_blocks.iterdir() if (p / "meta.json").exists()]
        assert blocks, "no fixture blocks"
        for block in blocks:
            meta_path = block / "meta.json"
            meta = json.loads(meta_path.read_text())
            meta["thanos"] = {"labels": {"k8s_cluster": "archive-fixture"},
                              "downsample": {"resolution": 0}, "source": "sidecar"}
            meta_path.write_text(json.dumps(meta))
            # Publish meta last, matching Thanos' visibility contract.
            for file in sorted(block.rglob("*"), key=lambda p: p.name == "meta.json"):
                if file.is_file():
                    s3.request("PUT", str(file.relative_to(args.upload_blocks)), file.read_bytes())
        print(f"Published {len(blocks)} fixture blocks")
    print("S3 object roundtrip, range reads and listing passed" +
          ("; persisted through restart and deleted" if args.verify_restart else ""))


if __name__ == "__main__":
    main()
