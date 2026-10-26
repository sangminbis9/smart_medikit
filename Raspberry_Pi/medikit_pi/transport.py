"""합의된 배포 설정이 있을 때만 S3와 상태 조회 API에 연결한다."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class _NoRedirect(HTTPRedirectHandler):
    # 인증 헤더가 다른 주소로 전달되지 않도록 리다이렉트를 거부한다.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("상태 조회 API의 리다이렉트는 허용하지 않습니다.")


class S3Transport:
    # 서버 계약과 명시적 경로가 확인된 경우에만 실제 전송을 구성한다.
    def __init__(self, config: dict, *, session=None, opener=None):
        if config.get("contract_confirmed") is not True:
            raise ValueError("백엔드 담당자와 S3 완료 객체·상태 조회 계약을 먼저 확정해야 합니다.")
        required = (
            "bucket",
            "region",
            "prefix",
            "manifest_name",
            "status_url_template",
            "auth_mode",
        )
        if any(not isinstance(config.get(key), str) or not config[key].strip() for key in required):
            raise ValueError("AWS 버킷·리전·키 경로·상태 URL·인증 설정이 필요합니다.")
        self.config = dict(config)
        self.prefix = config["prefix"].strip("/")
        self.manifest_name = config["manifest_name"]
        if not re.fullmatch(r"[A-Za-z0-9_./-]+", self.prefix) or ".." in self.prefix.split("/"):
            raise ValueError("S3 prefix는 명시적인 상대 키 경로여야 합니다.")
        if not re.fullmatch(r"[A-Za-z0-9_-]+\.json", self.manifest_name):
            raise ValueError("완료 객체 이름은 경로가 없는 JSON 파일명이어야 합니다.")
        template = config["status_url_template"]
        if (
            template.count("{event_id}") != 1
            or "{" in template.replace("{event_id}", "")
            or "}" in template.replace("{event_id}", "")
        ):
            raise ValueError("상태 URL에는 {event_id} 치환자 하나가 필요합니다.")
        parsed = urlsplit(template.replace("{event_id}", "event"))
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise ValueError("인증정보가 포함되지 않은 HTTPS 상태 URL이 필요합니다.")
        if config["auth_mode"] not in {"sigv4", "bearer"}:
            raise ValueError("합의한 API 인증 방식 sigv4 또는 bearer를 지정해야 합니다.")
        if config["auth_mode"] == "bearer" and not config.get("bearer_token_file"):
            raise ValueError("bearer 인증에는 외부 토큰 파일이 필요합니다.")
        if session is None:
            import boto3

            session = boto3.Session(region_name=config["region"])
        self.session = session
        self.opener = opener or build_opener(_NoRedirect())
        self._s3 = None

    # 매 요청 전에 갱신 가능한 공급자의 단기 세션 자격증명을 확인한다.
    def _credentials(self):
        credentials = self.session.get_credentials()
        if credentials is None:
            raise RuntimeError("외부 AWS 단기 자격증명 공급자를 설정해야 합니다.")
        frozen = credentials.get_frozen_credentials()
        if not frozen.token:
            raise RuntimeError(
                "Pi에는 장기 AWS 키를 사용할 수 없습니다. 단기 세션 토큰이 필요합니다."
            )
        return frozen

    # 촬영 사진을 먼저 전송하고 마지막 JSON 객체로 세션 완결성을 알린다.
    def upload(self, event: dict, directory: Path) -> None:
        self._credentials()
        if self._s3 is None:
            from botocore.config import Config

            self._s3 = self.session.client(
                "s3",
                region_name=self.config["region"],
                config=Config(
                    connect_timeout=5, read_timeout=15, retries={"total_max_attempts": 2}
                ),
            )
        session_id = event["session_id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
            raise ValueError("올바른 session_id가 필요합니다.")
        metadata = dict(event)
        for kind in ("package", "interior"):
            key = f"{self.prefix}/{session_id}/{kind}.jpg"
            captured = event[f"{kind}_capture_ok"]
            metadata[f"{kind}_image_key"] = key if captured else None
            if captured:
                with (Path(directory) / f"{kind}.jpg").open("rb") as image_file:
                    self._s3.put_object(
                        Bucket=self.config["bucket"],
                        Key=key,
                        Body=image_file,
                        ContentType="image/jpeg",
                    )
        # 이미지별 트리거를 사용하지 않는 합의된 서버만 완료 객체를 처리한다.
        self._s3.put_object(
            Bucket=self.config["bucket"],
            Key=f"{self.prefix}/{session_id}/{self.manifest_name}",
            Body=json.dumps(metadata, ensure_ascii=False).encode("utf-8"),
            ContentType="application/json",
        )

    # 설정된 상태 URL을 인증하여 조회하고 계약의 처리 상태만 반환한다.
    def status(self, event_id: str) -> str:
        url = self.config["status_url_template"].replace("{event_id}", quote(event_id, safe=""))
        headers = {"Accept": "application/json"}
        if self.config["auth_mode"] == "sigv4":
            from botocore.auth import SigV4Auth
            from botocore.awsrequest import AWSRequest

            request = AWSRequest(method="GET", url=url, headers=headers)
            SigV4Auth(self._credentials(), "execute-api", self.config["region"]).add_auth(request)
            headers = dict(request.headers.items())
        else:
            token = Path(self.config["bearer_token_file"]).read_text(encoding="utf-8").strip()
            if not token or any(character.isspace() for character in token):
                raise ValueError("API 토큰 파일이 비어 있거나 잘못되었습니다.")
            headers["Authorization"] = f"Bearer {token}"
        with self.opener.open(Request(url, headers=headers), timeout=15) as response:
            body = response.read(65537)
            if len(body) > 65536:
                raise ValueError("상태 조회 응답이 허용 크기를 초과했습니다.")
            payload = json.loads(body)
        if not isinstance(payload, dict) or payload.get("processing_status") not in {
            "PENDING",
            "PROCESSING",
            "COMPLETED",
            "REVIEW_REQUIRED",
            "FAILED",
        }:
            raise ValueError("서버 응답에 계약의 processing_status가 없습니다.")
        return payload["processing_status"]
