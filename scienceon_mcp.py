#!/usr/bin/env python3
"""
ScienceON-MCP Server
KISTI ScienceON OpenAPI를 MCP 서버로 래핑합니다.
"""
import base64
import html
import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import httpx
from Crypto.Cipher import AES
from fastmcp import FastMCP

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 환경변수
# ─────────────────────────────────────────────

_env_cache: dict | None = None

def _load_env() -> dict:
    global _env_cache
    if _env_cache is not None:
        return _env_cache
    _env_cache = {}
    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                _env_cache[k.strip()] = v.strip()
    return _env_cache

def _env(key: str) -> str:
    return os.getenv(key) or _load_env().get(key, "")

API_KEY    = lambda: _env("SCIENCEON_API_KEY")
CLIENT_ID  = lambda: _env("SCIENCEON_CLIENT_ID")
MAC_ADDR   = lambda: _env("SCIENCEON_MAC_ADDRESS")

BASE_URL = "https://apigateway.kisti.re.kr"

# ─────────────────────────────────────────────
# 인증 / 토큰 (캐싱)
# ─────────────────────────────────────────────

_token_cache: str | None = None
_token_issued_at: datetime | None = None
_TOKEN_TTL_SECONDS = 3600  # 1시간

def _make_accounts() -> str:
    """datetime + mac_address를 AES-CBC로 암호화해 accounts 파라미터 생성"""
    iv = "jvHJ1EFA0IXBrxxz"
    bs = 16
    time_str = "".join(re.findall(r"\d", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    plain = json.dumps({"datetime": time_str, "mac_address": MAC_ADDR()}, separators=(",", ":"))
    pad_len = bs - len(plain) % bs
    padded = plain + chr(pad_len) * pad_len
    cipher = AES.new(API_KEY().encode(), AES.MODE_CBC, iv.encode())
    encrypted = base64.urlsafe_b64encode(cipher.encrypt(padded.encode())).decode()
    return quote(encrypted)

async def _get_token() -> str:
    global _token_cache, _token_issued_at
    now = datetime.now()
    if (
        _token_cache
        and _token_issued_at
        and (now - _token_issued_at).total_seconds() < _TOKEN_TTL_SECONDS
    ):
        return _token_cache

    url = f"{BASE_URL}/tokenrequest.do?client_id={CLIENT_ID()}&accounts={_make_accounts()}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url)
        data = r.json()
        token = data.get("access_token", "")
        if token:
            _token_cache = token
            _token_issued_at = now
        return token

def _check_credentials() -> str | None:
    """인증 정보 미설정 시 오류 메시지 반환"""
    missing = [k for k, v in {
        "SCIENCEON_API_KEY": API_KEY(),
        "SCIENCEON_CLIENT_ID": CLIENT_ID(),
        "SCIENCEON_MAC_ADDRESS": MAC_ADDR(),
    }.items() if not v]
    if missing:
        return (
            f"API 인증 정보가 설정되지 않았습니다: {', '.join(missing)}\n"
            "MCP 클라이언트 설정의 env 항목에 환경변수를 추가해주세요."
        )
    return None

# ─────────────────────────────────────────────
# 공통 API 호출
# ─────────────────────────────────────────────

async def _search(target: str, search_query: dict, cur_page: int = 1, row_count: int = 10) -> dict:
    """검색 API 호출 (action=search)"""
    token = await _get_token()
    sq_enc = quote(json.dumps(search_query, ensure_ascii=False))
    url = (
        f"{BASE_URL}/openapicall.do"
        f"?client_id={CLIENT_ID()}&token={token}&version=1.0"
        f"&action=search&target={target}"
        f"&searchQuery={sq_enc}&curPage={cur_page}&rowCount={row_count}"
    )
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url)
        return _parse_xml(r.content.decode("utf-8", errors="replace"))

async def _browse(target: str, cn: str) -> dict:
    """상세보기 API 호출 (action=browse)"""
    token = await _get_token()
    url = (
        f"{BASE_URL}/openapicall.do"
        f"?client_id={CLIENT_ID()}&token={token}&version=1.0"
        f"&action=browse&target={target}&cn={cn}"
    )
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url)
        return _parse_xml(r.content.decode("utf-8", errors="replace"))

async def _citation(target: str, cn: str) -> dict:
    """인용/피인용 API 호출 (action=citation)"""
    token = await _get_token()
    url = (
        f"{BASE_URL}/openapicall.do"
        f"?client_id={CLIENT_ID()}&token={token}&version=1.0"
        f"&action=citation&target={target}&cn={cn}"
    )
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url)
        return _parse_xml(r.content.decode("utf-8", errors="replace"))

# ─────────────────────────────────────────────
# XML 파싱
# ─────────────────────────────────────────────

def _parse_xml(xml_str: str) -> dict:
    """ScienceON XML 응답을 파싱해 dict로 반환"""
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError as e:
        return {"error": True, "message": f"XML 파싱 오류: {e}"}

    # 상태 코드 확인
    status_code = root.findtext(".//statusCode", "")
    if status_code and status_code != "200":
        return {
            "error": True,
            "status_code": status_code,
            "message": root.findtext(".//errorMessage", "알 수 없는 오류"),
        }

    total_count = root.findtext(".//TotalCount")
    records = []
    for record in root.findall(".//record"):
        item_dict: dict = {}
        for item in record.findall("item"):
            meta_code = item.get("metaCode", "")
            # CallAPIInfo 같은 중첩 item은 건너뜀
            if meta_code and item.text is not None:
                # 같은 metaCode가 여러 번 나올 경우 리스트로
                if meta_code in item_dict:
                    existing = item_dict[meta_code]
                    if isinstance(existing, list):
                        existing.append(item.text)
                    else:
                        item_dict[meta_code] = [existing, item.text]
                else:
                    item_dict[meta_code] = item.text
        records.append(item_dict)

    return {
        "error": False,
        "total_count": int(total_count) if total_count else len(records),
        "records": records,
    }

# ─────────────────────────────────────────────
# 포맷터 헬퍼
# ─────────────────────────────────────────────

def _clean_html(text: str) -> str:
    """HTML 태그 및 엔티티 제거 (XML 이스케이프 → 태그 제거 순서)"""
    # 1단계: XML 이스케이프 디코딩 (&lt; → <, &gt; → > 등)
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    # 2단계: HTML 태그 제거
    text = re.sub(r"<[^>]+>", "", text)
    # 3단계: 나머지 HTML 엔티티 전체 디코딩 (&nbsp; &lsquo; &rsquo; 등)
    text = html.unescape(text)
    # 4단계: 연속 공백 정리
    text = re.sub(r"\s+", " ", text).strip()
    return text

def _truncate(text: str, max_len: int = 300) -> str:
    text = text.strip()
    return text[:max_len] + "..." if len(text) > max_len else text

def _fmt_date(d: str) -> str:
    """20231101 → 2023-11-01"""
    if d and len(d) == 8 and d.isdigit():
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"
    return d

# ─────────────────────────────────────────────
# MCP 서버
# ─────────────────────────────────────────────

mcp = FastMCP("ScienceON-MCP")

# ── 논문 ──────────────────────────────────────

@mcp.tool(annotations={"readOnlyHint": True})
async def scienceon_papers(
    query: str,
    max_results: int = 10,
    page: int = 1,
) -> str:
    """
    KISTI ScienceON에서 논문을 검색합니다.

    Args:
        query: 검색 키워드
        max_results: 최대 결과 수 (기본 10, 최대 100)
        page: 페이지 번호 (기본 1)

    Returns:
        논문 목록 (제목, 저자, 발행년, 저널명, 초록, CN번호)
    """
    if err := _check_credentials():
        return err
    try:
        result = await _search("ARTI", {"BI": query}, page, min(max_results, 100))
        if result["error"]:
            return f"오류: {result['message']}"
        records = result["records"]
        if not records:
            return f"'{query}'에 대한 논문 검색 결과가 없습니다."

        lines = [f"**논문 검색 결과** | 검색어: '{query}' | 총 {result['total_count']:,}건 중 {len(records)}건\n"]
        for i, r in enumerate(records, 1):
            title   = r.get("Title", "제목 없음")
            author  = r.get("Author", "")
            year    = r.get("Pubyear", "")
            journal = r.get("JournalName", "")
            cn      = r.get("CN", "")
            abstract = _truncate(_clean_html(r.get("Abstract", "")), 200)

            lines.append(f"**[{i}] {title}**")
            if author: lines.append(f"  - 저자: {author}")
            if year:   lines.append(f"  - 발행년: {year}")
            if journal: lines.append(f"  - 저널: {journal}")
            if cn:     lines.append(f"  - CN: `{cn}`")
            if abstract: lines.append(f"  - 초록: {abstract}")
            lines.append("")

        lines.append("💡 상세정보는 CN번호로 `scienceon_paper_details`를 호출하세요.")
        return "\n".join(lines)
    except Exception as e:
        return f"논문 검색 중 오류: {e}"


@mcp.tool(annotations={"readOnlyHint": True})
async def scienceon_paper_details(cn: str) -> str:
    """
    CN번호로 논문 상세정보를 조회합니다. (인용/참고문헌, 유사논문 포함)

    Args:
        cn: 논문 고유 식별번호 (논문 검색 결과의 CN 값)

    Returns:
        논문 상세정보
    """
    if err := _check_credentials():
        return err
    try:
        result = await _browse("ARTI", cn)
        if result["error"]:
            return f"오류: {result['message']}"
        if not result["records"]:
            return f"CN '{cn}'에 해당하는 논문을 찾을 수 없습니다."

        r = result["records"][0]
        lines = [f"**논문 상세정보** | CN: `{cn}`\n"]
        lines.append(f"**제목**: {r.get('Title', '')}")
        if r.get("Author"):     lines.append(f"**저자**: {r['Author']}")
        if r.get("Pubyear"):    lines.append(f"**발행년**: {r['Pubyear']}")
        if r.get("JournalName"): lines.append(f"**저널**: {r['JournalName']}")
        if r.get("Publisher"):  lines.append(f"**출판사**: {r['Publisher']}")
        if r.get("DOI"):        lines.append(f"**DOI**: {r['DOI']}")
        if r.get("Keyword"):    lines.append(f"**키워드**: {r['Keyword']}")
        lines.append(f"**ScienceON**: https://scienceon.kisti.re.kr/srch/selectPORSrchArticle.do?cn={cn}")

        abstract = _clean_html(r.get("Abstract", ""))
        if abstract:
            lines.append(f"\n**초록**:\n{abstract}")

        if r.get("FulltextURL"):
            lines.append(f"\n**원문**: {r['FulltextURL']}")
        if r.get("SimilarTitle"):
            lines.append(f"\n**유사논문**: {_truncate(r['SimilarTitle'], 200)}")
        if r.get("CitingTitle"):
            lines.append(f"\n**인용논문**: {_truncate(r['CitingTitle'], 200)}")
        if r.get("CitedTitle"):
            lines.append(f"\n**참고논문**: {_truncate(r['CitedTitle'], 200)}")

        return "\n".join(lines)
    except Exception as e:
        return f"논문 상세조회 중 오류: {e}"


# ── 특허 ──────────────────────────────────────

@mcp.tool(annotations={"readOnlyHint": True})
async def scienceon_patents(
    query: str,
    max_results: int = 10,
    page: int = 1,
) -> str:
    """
    KISTI ScienceON에서 특허를 검색합니다.

    Args:
        query: 검색 키워드
        max_results: 최대 결과 수 (기본 10, 최대 100)
        page: 페이지 번호 (기본 1)

    Returns:
        특허 목록 (특허제목, 출원인, 출원일, 공개일, 특허상태, CN번호)
    """
    if err := _check_credentials():
        return err
    try:
        result = await _search("PATENT", {"BI": query}, page, min(max_results, 100))
        if result["error"]:
            return f"오류: {result['message']}"
        records = result["records"]
        if not records:
            return f"'{query}'에 대한 특허 검색 결과가 없습니다."

        lines = [f"**특허 검색 결과** | 검색어: '{query}' | 총 {result['total_count']:,}건 중 {len(records)}건\n"]
        for i, r in enumerate(records, 1):
            title      = r.get("Title", "제목 없음")
            applicants = r.get("Applicants", "")
            appl_date  = _fmt_date(r.get("ApplDate", ""))
            publ_date  = _fmt_date(r.get("PublDate", ""))
            status     = r.get("PatentStatus", "")
            ipc        = r.get("IPC", "")
            cn         = r.get("CN", "")
            abstract   = _truncate(_clean_html(r.get("Abstract", "")), 200)

            lines.append(f"**[{i}] {title}**")
            if applicants: lines.append(f"  - 출원인: {applicants}")
            if appl_date:  lines.append(f"  - 출원일: {appl_date}")
            if publ_date:  lines.append(f"  - 공개일: {publ_date}")
            if status:     lines.append(f"  - 상태: {status}")
            if ipc:        lines.append(f"  - IPC: {ipc}")
            if cn:         lines.append(f"  - CN: `{cn}`")
            if abstract:   lines.append(f"  - 초록: {abstract}")
            lines.append("")

        lines.append("💡 상세정보는 CN번호로 `scienceon_patent_details`를, 인용정보는 `scienceon_patent_citations`를 호출하세요.")
        return "\n".join(lines)
    except Exception as e:
        return f"특허 검색 중 오류: {e}"


@mcp.tool(annotations={"readOnlyHint": True})
async def scienceon_patent_details(cn: str) -> str:
    """
    CN번호로 특허 상세정보를 조회합니다.

    Args:
        cn: 특허 고유 식별번호 (특허 검색 결과의 CN 값)

    Returns:
        특허 상세정보 (유사특허, 인용특허 포함)
    """
    if err := _check_credentials():
        return err
    try:
        result = await _browse("PATENT", cn)
        if result["error"]:
            return f"오류: {result['message']}"
        if not result["records"]:
            return f"CN '{cn}'에 해당하는 특허를 찾을 수 없습니다."

        r = result["records"][0]
        lines = [f"**특허 상세정보** | CN: `{cn}`\n"]
        lines.append(f"**제목**: {r.get('Title', '')}")
        if r.get("Applicants"):   lines.append(f"**출원인**: {r['Applicants']}")
        if r.get("Inventor"):     lines.append(f"**발명자**: {r['Inventor']}")
        if r.get("ApplDate"):     lines.append(f"**출원일**: {_fmt_date(r['ApplDate'])}")
        if r.get("PublDate"):     lines.append(f"**공개일**: {_fmt_date(r['PublDate'])}")
        if r.get("GrantDate"):    lines.append(f"**등록일**: {_fmt_date(r['GrantDate'])}")
        if r.get("GrantNum"):     lines.append(f"**등록번호**: {r['GrantNum']}")
        if r.get("PatentStatus"): lines.append(f"**상태**: {r['PatentStatus']}")
        if r.get("IPC"):          lines.append(f"**IPC**: {r['IPC']}")
        if r.get("Nation"):       lines.append(f"**국가**: {r['Nation']}")
        lines.append(f"**ScienceON**: https://scienceon.kisti.re.kr/srch/selectPORSrchPatent.do?cn={cn}")

        abstract = _clean_html(r.get("Abstract", ""))
        if abstract:
            lines.append(f"\n**초록**:\n{abstract}")

        if r.get("SimilarTitle"):
            lines.append(f"\n**유사특허**: {_truncate(r['SimilarTitle'], 200)}")
        if r.get("CitingTitle"):
            lines.append(f"\n**인용특허**: {_truncate(r['CitingTitle'], 200)}")

        return "\n".join(lines)
    except Exception as e:
        return f"특허 상세조회 중 오류: {e}"


@mcp.tool(annotations={"readOnlyHint": True})
async def scienceon_patent_citations(cn: str) -> str:
    """
    CN번호로 특허 인용/피인용 정보를 조회합니다.

    Args:
        cn: 특허 고유 식별번호 (특허 검색 결과의 CN 값)

    Returns:
        인용/피인용 특허 목록
    """
    if err := _check_credentials():
        return err
    try:
        result = await _citation("PATENT", cn)
        if result["error"]:
            return f"오류: {result['message']}"
        records = result["records"]
        if not records:
            return f"CN '{cn}'에 대한 인용/피인용 정보가 없습니다."

        lines = [f"**특허 인용/피인용 정보** | CN: `{cn}` | 총 {len(records)}건\n"]
        for i, r in enumerate(records, 1):
            citation_type = r.get("Citation", "")
            title         = r.get("Title", "제목 없음")
            applicants    = r.get("Applicants", "")
            appl_date     = _fmt_date(r.get("ApplDate", ""))
            status        = r.get("PatentStatus", "")
            ref_cn        = r.get("CN", "")

            lines.append(f"**[{i}] [{citation_type}] {title}**")
            if applicants: lines.append(f"  - 출원인: {applicants}")
            if appl_date:  lines.append(f"  - 출원일: {appl_date}")
            if status:     lines.append(f"  - 상태: {status}")
            if ref_cn:     lines.append(f"  - CN: `{ref_cn}`")
            lines.append("")

        return "\n".join(lines)
    except Exception as e:
        return f"특허 인용정보 조회 중 오류: {e}"


# ── 보고서 ────────────────────────────────────

@mcp.tool(annotations={"readOnlyHint": True})
async def scienceon_reports(
    query: str,
    max_results: int = 10,
    page: int = 1,
) -> str:
    """
    KISTI ScienceON에서 R&D 보고서를 검색합니다.

    Args:
        query: 검색 키워드
        max_results: 최대 결과 수 (기본 10, 최대 100)
        page: 페이지 번호 (기본 1)

    Returns:
        보고서 목록 (제목, 저자, 발행기관, 발행년, 초록, CN번호)
    """
    if err := _check_credentials():
        return err
    try:
        result = await _search("REPORT", {"BI": query}, page, min(max_results, 100))
        if result["error"]:
            return f"오류: {result['message']}"
        records = result["records"]
        if not records:
            return f"'{query}'에 대한 보고서 검색 결과가 없습니다."

        lines = [f"**보고서 검색 결과** | 검색어: '{query}' | 총 {result['total_count']:,}건 중 {len(records)}건\n"]
        for i, r in enumerate(records, 1):
            title     = r.get("Title", "제목 없음")
            author    = r.get("Author", "")
            publisher = r.get("Publisher", "")
            year      = r.get("Pubyear", "")
            cn        = r.get("CN", "")
            abstract  = _truncate(_clean_html(r.get("Abstract", "")), 200)

            lines.append(f"**[{i}] {title}**")
            if author:    lines.append(f"  - 저자: {author}")
            if publisher: lines.append(f"  - 발행기관: {publisher}")
            if year:      lines.append(f"  - 발행년: {year}")
            if cn:        lines.append(f"  - CN: `{cn}`")
            if abstract:  lines.append(f"  - 초록: {abstract}")
            lines.append("")

        lines.append("💡 상세정보는 CN번호로 `scienceon_report_details`를 호출하세요.")
        return "\n".join(lines)
    except Exception as e:
        return f"보고서 검색 중 오류: {e}"


@mcp.tool(annotations={"readOnlyHint": True})
async def scienceon_report_details(cn: str) -> str:
    """
    CN번호로 R&D 보고서 상세정보를 조회합니다.

    Args:
        cn: 보고서 고유 식별번호 (보고서 검색 결과의 CN 값)

    Returns:
        보고서 상세정보 (인용논문/특허/보고서 포함)
    """
    if err := _check_credentials():
        return err
    try:
        result = await _browse("REPORT", cn)
        if result["error"]:
            return f"오류: {result['message']}"
        if not result["records"]:
            return f"CN '{cn}'에 해당하는 보고서를 찾을 수 없습니다."

        r = result["records"][0]
        lines = [f"**보고서 상세정보** | CN: `{cn}`\n"]
        lines.append(f"**제목**: {r.get('Title', '')}")
        if r.get("Title2"):    lines.append(f"**부제목**: {r['Title2']}")
        if r.get("Author"):    lines.append(f"**저자**: {r['Author']}")
        if r.get("Publisher"): lines.append(f"**발행기관**: {r['Publisher']}")
        if r.get("Pubyear"):   lines.append(f"**발행년**: {r['Pubyear']}")
        if r.get("Keyword"):   lines.append(f"**키워드**: {r['Keyword']}")
        lines.append(f"**ScienceON**: https://scienceon.kisti.re.kr/srch/selectPORSrchReport.do?cn={cn}")

        abstract = _clean_html(r.get("Abstract", ""))
        if abstract:
            lines.append(f"\n**초록**:\n{abstract}")

        if r.get("FulltextURL"):
            lines.append(f"\n**원문**: {r['FulltextURL']}")
        if r.get("CitedPaperinfo"):
            lines.append(f"\n**인용논문**: {_truncate(r['CitedPaperinfo'], 200)}")
        if r.get("CitedPatentinfo"):
            lines.append(f"\n**인용특허**: {_truncate(r['CitedPatentinfo'], 200)}")
        if r.get("CitedReportinfo"):
            lines.append(f"\n**인용보고서**: {_truncate(r['CitedReportinfo'], 200)}")

        return "\n".join(lines)
    except Exception as e:
        return f"보고서 상세조회 중 오류: {e}"


# ── 동향 ──────────────────────────────────────

@mcp.tool(annotations={"readOnlyHint": True})
async def scienceon_news_trends(
    query: str,
    max_results: int = 10,
    page: int = 1,
) -> str:
    """
    KISTI ScienceON에서 과학기술 동향 기사를 검색합니다.
    국내외 과학기술 뉴스/기사 모음 (해외과학기술동향, 정보서비스 글로벌동향 등).

    Args:
        query: 검색 키워드
        max_results: 최대 결과 수 (기본 10, 최대 100)
        page: 페이지 번호 (기본 1)

    Returns:
        동향 기사 목록 (제목, 발행년, 초록, CN번호)
    """
    if err := _check_credentials():
        return err
    try:
        result = await _search("ATT", {"BI": query}, page, min(max_results, 100))
        if result["error"]:
            return f"오류: {result['message']}"
        records = result["records"]
        if not records:
            return f"'{query}'에 대한 동향 검색 결과가 없습니다."

        lines = [f"**과학기술 동향 검색 결과** | 검색어: '{query}' | 총 {result['total_count']:,}건 중 {len(records)}건\n"]
        for i, r in enumerate(records, 1):
            title    = r.get("Title", "제목 없음")
            year     = r.get("Pubyear", "")
            cn       = r.get("CN", "")
            abstract = _truncate(_clean_html(r.get("Abstract", "")), 200)

            lines.append(f"**[{i}] {title}**")
            if year: lines.append(f"  - 발행년: {year}")
            if cn:   lines.append(f"  - CN: `{cn}`")
            if abstract: lines.append(f"  - 내용: {abstract}")
            lines.append("")

        lines.append("💡 상세정보는 CN번호로 `scienceon_news_trend_details`를 호출하세요.")
        return "\n".join(lines)
    except Exception as e:
        return f"동향 검색 중 오류: {e}"


@mcp.tool(annotations={"readOnlyHint": True})
async def scienceon_news_trend_details(cn: str) -> str:
    """
    CN번호로 과학기술 동향 기사 상세정보를 조회합니다.

    Args:
        cn: 동향 기사 고유 식별번호 (동향 검색 결과의 CN 값)

    Returns:
        동향 기사 상세정보
    """
    if err := _check_credentials():
        return err
    try:
        result = await _browse("ATT", cn)
        if result["error"]:
            return f"오류: {result['message']}"
        if not result["records"]:
            return f"CN '{cn}'에 해당하는 동향 기사를 찾을 수 없습니다."

        r = result["records"][0]
        lines = [f"**동향 기사 상세정보** | CN: `{cn}`\n"]
        lines.append(f"**제목**: {r.get('Title', '')}")
        if r.get("Pubyear"):  lines.append(f"**발행년**: {r['Pubyear']}")
        if r.get("Publisher"): lines.append(f"**발행기관**: {r['Publisher']}")
        if r.get("DBCode"):   lines.append(f"**DB**: {r['DBCode']}")

        abstract = _clean_html(r.get("Abstract", ""))
        if abstract:
            lines.append(f"\n**내용**:\n{abstract}")

        if r.get("FulltextURL"):
            lines.append(f"\n**원문**: {r['FulltextURL']}")

        return "\n".join(lines)
    except Exception as e:
        return f"동향 상세조회 중 오류: {e}"


# ── 과학향기 ──────────────────────────────────

@mcp.tool(annotations={"readOnlyHint": True})
async def scienceon_scents(
    year: str,
    max_results: int = 10,
    page: int = 1,
) -> str:
    """
    KISTI ScienceON에서 과학향기 칼럼을 검색합니다.
    2003년부터 현재까지 과학기술 전 분야를 다루는 전문 칼럼 서비스입니다.
    ※ 과학향기 API는 발행연도(year)로만 검색 가능합니다.

    Args:
        year: 발행연도 (예: "2024")
        max_results: 최대 결과 수 (기본 10, 최대 100)
        page: 페이지 번호 (기본 1)

    Returns:
        과학향기 칼럼 목록 (제목, 권호, CN번호)
    """
    if err := _check_credentials():
        return err
    try:
        result = await _search("SCENT", {"PY": year}, page, min(max_results, 100))
        if result["error"]:
            return f"오류: {result['message']}"
        records = result["records"]
        if not records:
            return f"{year}년 과학향기 칼럼 검색 결과가 없습니다."

        lines = [f"**과학향기 칼럼 목록** | {year}년 | 총 {result['total_count']:,}건 중 {len(records)}건\n"]
        for i, r in enumerate(records, 1):
            title  = r.get("ScentTitle", "제목 없음")
            volume = r.get("Volume", "")
            cn     = r.get("CN", "")

            lines.append(f"**[{i}] {title}**")
            if volume: lines.append(f"  - 권호: {volume}")
            if cn:     lines.append(f"  - CN: `{cn}`")
            lines.append("")

        lines.append("💡 본문은 CN번호로 `scienceon_scent_details`를 호출하세요.")
        return "\n".join(lines)
    except Exception as e:
        return f"과학향기 검색 중 오류: {e}"


@mcp.tool(annotations={"readOnlyHint": True})
async def scienceon_scent_details(cn: str) -> str:
    """
    CN번호로 과학향기 칼럼 상세정보 및 본문을 조회합니다.

    Args:
        cn: 과학향기 고유 식별번호 (과학향기 검색 결과의 CN 값)

    Returns:
        과학향기 칼럼 상세정보 및 본문
    """
    if err := _check_credentials():
        return err
    try:
        result = await _browse("SCENT", cn)
        if result["error"]:
            return f"오류: {result['message']}"
        if not result["records"]:
            return f"CN '{cn}'에 해당하는 과학향기 칼럼을 찾을 수 없습니다."

        r = result["records"][0]
        lines = [f"**과학향기 칼럼** | CN: `{cn}`\n"]
        lines.append(f"**제목**: {r.get('ScentTitle', '')}")
        if r.get("Volume"): lines.append(f"**권호**: {r['Volume']}")

        content = _clean_html(r.get("Content", ""))
        if content:
            lines.append(f"\n**본문**:\n{content}")

        return "\n".join(lines)
    except Exception as e:
        return f"과학향기 상세조회 중 오류: {e}"


# ── 연구자 ────────────────────────────────────

@mcp.tool(annotations={"readOnlyHint": True})
async def scienceon_researchers(
    query: str,
    max_results: int = 10,
    page: int = 1,
) -> str:
    """
    KISTI ScienceON에서 연구자를 검색합니다.

    Args:
        query: 연구자 이름 또는 키워드
        max_results: 최대 결과 수 (기본 10, 최대 100)
        page: 페이지 번호 (기본 1)

    Returns:
        연구자 목록 (이름, 소속기관, 논문/특허/보고서 건수, CN번호)
    """
    if err := _check_credentials():
        return err
    try:
        result = await _search("RESEARCHER", {"BI": query}, page, min(max_results, 100))
        if result["error"]:
            return f"오류: {result['message']}"
        records = result["records"]
        if not records:
            return f"'{query}'에 대한 연구자 검색 결과가 없습니다."

        lines = [f"**연구자 검색 결과** | 검색어: '{query}' | 총 {result['total_count']:,}건 중 {len(records)}건\n"]
        for i, r in enumerate(records, 1):
            name_kor  = r.get("AuthorNameKor", "")
            name_eng  = r.get("AuthorNameEng", "")
            inst_kor  = r.get("AuthorInstKor", "")
            email     = r.get("Email", "")
            art_cnt   = r.get("ArticleCnt", "0")
            pat_cnt   = r.get("PatentCnt", "0")
            rpt_cnt   = r.get("ReportCnt", "0")
            cn        = r.get("CN", "")

            name = name_kor or name_eng or "이름 없음"
            lines.append(f"**[{i}] {name}**")
            if name_eng and name_kor: lines.append(f"  - 영문명: {name_eng}")
            if inst_kor:  lines.append(f"  - 소속: {inst_kor}")
            if email:     lines.append(f"  - 이메일: {email}")
            lines.append(f"  - 실적: 논문 {art_cnt}건 / 특허 {pat_cnt}건 / 보고서 {rpt_cnt}건")
            if cn: lines.append(f"  - CN: `{cn}`")
            lines.append("")

        lines.append("💡 상세정보는 CN번호로 `scienceon_researcher_details`를 호출하세요.")
        return "\n".join(lines)
    except Exception as e:
        return f"연구자 검색 중 오류: {e}"


@mcp.tool(annotations={"readOnlyHint": True})
async def scienceon_researcher_details(cn: str) -> str:
    """
    CN번호로 연구자 상세정보를 조회합니다.

    Args:
        cn: 연구자 고유 식별번호 (연구자 검색 결과의 CN 값)

    Returns:
        연구자 상세정보
    """
    if err := _check_credentials():
        return err
    try:
        result = await _browse("RESEARCHER", cn)
        if result["error"]:
            return f"오류: {result['message']}"
        if not result["records"]:
            return f"CN '{cn}'에 해당하는 연구자를 찾을 수 없습니다."

        r = result["records"][0]
        lines = [f"**연구자 상세정보** | CN: `{cn}`\n"]
        if r.get("AuthorNameKor"): lines.append(f"**이름(국문)**: {r['AuthorNameKor']}")
        if r.get("AuthorNameEng"): lines.append(f"**이름(영문)**: {r['AuthorNameEng']}")
        if r.get("AuthorInstKor"): lines.append(f"**소속(국문)**: {r['AuthorInstKor']}")
        if r.get("AuthorInstEng"): lines.append(f"**소속(영문)**: {r['AuthorInstEng']}")
        if r.get("Email"):         lines.append(f"**이메일**: {r['Email']}")
        if r.get("Keyword"):       lines.append(f"**키워드**: {r['Keyword']}")
        lines.append(f"**실적**: 논문 {r.get('ArticleCnt','0')}건 / 특허 {r.get('PatentCnt','0')}건 / 보고서 {r.get('ReportCnt','0')}건")

        return "\n".join(lines)
    except Exception as e:
        return f"연구자 상세조회 중 오류: {e}"


# ── 연구기관 ──────────────────────────────────

@mcp.tool(annotations={"readOnlyHint": True})
async def scienceon_organizations(
    query: str,
    max_results: int = 10,
    page: int = 1,
) -> str:
    """
    KISTI ScienceON에서 연구기관을 검색합니다.
    ※ 한글 기관명으로 검색하세요. (예: "한국과학기술정보연구원")

    Args:
        query: 기관명 (한글 권장)
        max_results: 최대 결과 수 (기본 10, 최대 100)
        page: 페이지 번호 (기본 1)

    Returns:
        연구기관 목록 (기관명, 키워드, CN번호)
    """
    if err := _check_credentials():
        return err
    try:
        result = await _search("ORGAN", {"BI": query}, page, min(max_results, 100))
        if result["error"]:
            return f"오류: {result['message']}"
        records = result["records"]
        if not records:
            return f"'{query}'에 대한 연구기관 검색 결과가 없습니다."

        lines = [f"**연구기관 검색 결과** | 검색어: '{query}' | 총 {result['total_count']:,}건 중 {len(records)}건\n"]
        for i, r in enumerate(records, 1):
            name_kor = r.get("OrganKor", "")
            name_eng = r.get("OrganEng", "")
            keyword  = r.get("Keyword", "")
            cn       = r.get("CN", "")

            name = name_kor or name_eng or "기관명 없음"
            lines.append(f"**[{i}] {name}**")
            if name_eng and name_kor: lines.append(f"  - 영문명: {name_eng}")
            if keyword: lines.append(f"  - 키워드: {_truncate(keyword, 100)}")
            if cn: lines.append(f"  - CN: `{cn}`")
            lines.append("")

        lines.append("💡 상세정보는 CN번호로 `scienceon_organization_details`를 호출하세요.")
        return "\n".join(lines)
    except Exception as e:
        return f"연구기관 검색 중 오류: {e}"


@mcp.tool(annotations={"readOnlyHint": True})
async def scienceon_organization_details(cn: str) -> str:
    """
    CN번호로 연구기관 상세정보를 조회합니다.

    Args:
        cn: 연구기관 고유 식별번호 (연구기관 검색 결과의 CN 값)

    Returns:
        연구기관 상세정보
    """
    if err := _check_credentials():
        return err
    try:
        result = await _browse("ORGAN", cn)
        if result["error"]:
            return f"오류: {result['message']}"
        if not result["records"]:
            return f"CN '{cn}'에 해당하는 연구기관을 찾을 수 없습니다."

        r = result["records"][0]
        lines = [f"**연구기관 상세정보** | CN: `{cn}`\n"]
        if r.get("OrganKor"): lines.append(f"**기관명(국문)**: {r['OrganKor']}")
        if r.get("OrganEng"): lines.append(f"**기관명(영문)**: {r['OrganEng']}")
        if r.get("Keyword"):  lines.append(f"**키워드**: {r['Keyword']}")

        return "\n".join(lines)
    except Exception as e:
        return f"연구기관 상세조회 중 오류: {e}"


# ── 기술트렌드 ────────────────────────────────

@mcp.tool(annotations={"readOnlyHint": True})
async def scienceon_tech_trends(
    query: str,
    max_results: int = 10,
    page: int = 1,
) -> str:
    """
    KISTI ScienceON에서 기술트렌드 토픽을 검색합니다.
    특정 기술 키워드/토픽 중심의 트렌드 분석 서비스입니다.
    (예: "디지털 트윈", "메타버스", "양자컴퓨팅" 등 신기술 개념 정의 및 연관 콘텐츠 제공)

    Args:
        query: 검색 키워드
        max_results: 최대 결과 수 (기본 10, 최대 100)
        page: 페이지 번호 (기본 1)

    Returns:
        기술트렌드 토픽 목록 (트렌드명, 연관키워드, 정의, ContentURL, PdfURL)
    """
    if err := _check_credentials():
        return err
    try:
        result = await _search("TREND", {"BI": query}, page, min(max_results, 100))
        if result["error"]:
            return f"오류: {result['message']}"
        records = result["records"]
        if not records:
            return f"'{query}'에 대한 기술트렌드 검색 결과가 없습니다."

        lines = [f"**기술트렌드 검색 결과** | 검색어: '{query}' | 총 {result['total_count']:,}건 중 {len(records)}건\n"]
        for i, r in enumerate(records, 1):
            title    = r.get("Title", "제목 없음")
            keywords = r.get("RelatedKeywords", "")
            definition = _truncate(_clean_html(r.get("Definition", "")), 200)
            pub_date = _fmt_date(r.get("PublDate", ""))
            cn       = r.get("CN", "")
            content_url = r.get("ContentURL", "")
            pdf_url  = r.get("PdfURL", "")

            lines.append(f"**[{i}] {title}**")
            if pub_date:  lines.append(f"  - 생성일: {pub_date}")
            if keywords:  lines.append(f"  - 연관키워드: {_truncate(keywords, 150)}")
            if definition: lines.append(f"  - 정의: {definition}")
            if cn:         lines.append(f"  - CN: `{cn}`")
            if content_url: lines.append(f"  - 상세보기: {content_url}")
            if pdf_url:    lines.append(f"  - PDF: {pdf_url}")
            lines.append("")

        return "\n".join(lines)
    except Exception as e:
        return f"기술트렌드 검색 중 오류: {e}"


# ── 금주의과학기술뉴스 ────────────────────────

@mcp.tool(annotations={"readOnlyHint": True})
async def scienceon_weekly_news(
    date: str,
    max_results: int = 20,
) -> str:
    """
    금주의 과학기술뉴스를 조회합니다.
    주차별로 신뢰성 높은 국내외 과학기술뉴스를 제공합니다.

    Args:
        date: 조회 날짜 (형식: YYYYMMDD, 예: "20250224")
              해당 날짜가 포함된 주의 뉴스를 반환합니다.
        max_results: 최대 결과 수 (기본 20)

    Returns:
        해당 주의 과학기술뉴스 목록 (제목, 내용요약, 국가전략기술분류, 원문URL)
    """
    if err := _check_credentials():
        return err
    try:
        result = await _search("SNEWS", {"RD": date}, 1, min(max_results, 100))
        if result["error"]:
            return f"오류: {result['message']}"
        records = result["records"]
        if not records:
            return (
                f"{date} 날짜의 금주의과학기술뉴스가 없습니다.\n"
                "날짜 형식: YYYYMMDD (예: 20250224)\n"
                "뉴스는 매주 월요일 기준으로 등록됩니다."
            )

        lines = [f"**금주의 과학기술뉴스** | {date} | 총 {len(records)}건\n"]
        for i, r in enumerate(records, 1):
            title      = r.get("sj", "제목 없음")
            contents   = _truncate(r.get("contents", ""), 200)
            category   = r.get("cdNm", "")
            origin_url = r.get("originUrl", "")
            reg_date   = r.get("registDt", "")

            lines.append(f"**[{i}] {title}**")
            if category:   lines.append(f"  - 분류: {category}")
            if reg_date:   lines.append(f"  - 등록일: {reg_date}")
            if contents:   lines.append(f"  - 내용: {contents}")
            if origin_url: lines.append(f"  - 원문: {origin_url}")
            lines.append("")

        return "\n".join(lines)
    except Exception as e:
        return f"금주의과학기술뉴스 조회 중 오류: {e}"


# ─────────────────────────────────────────────
# 엔트리포인트
# ─────────────────────────────────────────────

def main():
    if err := _check_credentials():
        logger.error(err)
    mcp.run()

if __name__ == "__main__":
    main()
