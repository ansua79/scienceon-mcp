# ScienceON-MCP

![ScienceON](./media/so-api-gateway.png)

KISTI ScienceON OpenAPI를 MCP(Model Context Protocol) 서버로 래핑한 프로젝트입니다.
Claude 등 MCP 클라이언트에서 ScienceON의 논문, 특허, 보고서, 동향, 과학향기, 연구자, 연구기관, 기술트렌드, 과학기술뉴스를 직접 검색할 수 있습니다.
2026년6월 현재 ScienceON API Gateway에서 제공하는 모든 API를 활용할 수 있습니다.

> MCP를 지원하는 모든 클라이언트(Claude Desktop, Cursor 등)에서 사용 가능합니다.

> 💡 **설치가 어려운 초보자라면?**
> 직접 설정 파일을 편집하지 않고 클릭만으로 설치·관리할 수 있는 GUI 도구
> **[STIMCP Manager](https://github.com/ansua79/stimcp-manager)** 를 사용하세요.

## 변경 이력

### v1.0.1
- 모든 ScienceON API 호출에 `User-Agent: scienceon-mcp/<버전>` 헤더 추가 (서버 측 호출 식별용)
- 텍스트 정제 개선: KISTI/NTIS 응답에 섞여 나오는 비표준 엔티티(`&quo;`, `&apos;`)를 정상 문자로 보정

### v1.0.0
- 최초 릴리스 (17개 도구)

## 도구 목록 (17개)

| 도구명 | 분류 | 설명 |
|--------|------|------|
| `scienceon_papers` | 논문 | 국내외학술지, 학술회의논문, 국내학위논문, 저널·프로시딩 서지 등 검색 |
| `scienceon_paper_details` | 논문 | CN번호로 논문 상세정보 조회 (인용/참고문헌, 유사논문 포함) |
| `scienceon_patents` | 특허 | 한국·미국·유럽·일본·국제특허 등 검색 |
| `scienceon_patent_details` | 특허 | CN번호로 특허 상세정보 조회 (유사특허, 인용특허 포함) |
| `scienceon_patent_citations` | 특허 | CN번호로 특허 인용/피인용 관계 조회 |
| `scienceon_reports` | 보고서 | 국가연구개발보고서, 각종 분석리포트 등 검색 |
| `scienceon_report_details` | 보고서 | CN번호로 보고서 상세정보 조회 (인용논문/특허/보고서 포함) |
| `scienceon_news_trends` | 동향 | 해외과학기술동향, 과학기술 정책동향, 정보서비스 글로벌동향 등 검색 |
| `scienceon_news_trend_details` | 동향 | CN번호로 동향 기사 상세정보 조회 |
| `scienceon_scents` | 과학향기 | 과학 대중화 메일 매거진 (칼럼·상식기사), 발행연도로 검색 (예: "2024") |
| `scienceon_scent_details` | 과학향기 | CN번호로 과학향기 칼럼 본문 조회 |
| `scienceon_researchers` | 연구자 | 국내 식별 연구자의 논문·보고서·특허 목록 포함 검색 |
| `scienceon_researcher_details` | 연구자 | CN번호로 연구자 상세정보 조회 |
| `scienceon_organizations` | 연구기관 | 국내 식별 연구기관의 논문·보고서·특허 목록 포함 검색 (한글 기관명 권장) |
| `scienceon_organization_details` | 연구기관 | CN번호로 연구기관 상세정보 조회 |
| `scienceon_tech_trends` | ScienceON Trend | 키워드로 기술트렌드 토픽 검색 (연관키워드, 정의, PDF 포함) |
| `scienceon_weekly_news` | 금주의과학기술뉴스 | 주차별·월별 신뢰성 높은 국내외 과학기술뉴스, 날짜(YYYYMMDD)로 조회 |

---

## 요구사항

| 항목 | 방법 1 (uvx) | 방법 2 (uv 소스) |
|------|:---:|:---:|
| uv | ✅ 필수 | ✅ 필수 |
| git | — | ✅ 필수 |
| Python | ✅ uv가 자동 설치 | ✅ uv가 자동 설치 |
| 패키지 (fastmcp 등) | ✅ uvx가 자동 설치 | ✅ uv run이 자동 설치 |

- **ScienceON OpenAPI 인증정보** (API Key, Client ID, MAC Address)
  - 신청: [ScienceON OpenAPI](https://scienceon.kisti.re.kr/openapi)

### Windows 11에서 사전 설치

**1. uv 설치**

PowerShell을 열고 아래 명령을 실행합니다.

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

설치 후 터미널을 재시작하면 `uv`, `uvx` 명령을 사용할 수 있습니다.

**2. git 설치** (방법 2 사용 시에만 필요)

```powershell
winget install --id Git.Git
```

---

## 설치 및 설정

### 방법 1: uvx 사용 (권장) ⭐

**가장 쉽고 권장하는 방법입니다.** [PyPI](https://pypi.org/project/scienceon-mcp/)에 배포된 패키지를 사용하므로,
저장소 클론이나 소스 다운로드 없이 `uvx`가 최신 버전을 자동으로 설치·실행합니다.
uv만 설치되어 있으면 되고, 새 버전이 나오면 자동으로 반영됩니다.

**Claude Desktop 설정** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "scienceon": {
      "command": "uvx",
      "args": ["scienceon-mcp"],
      "env": {
        "SCIENCEON_API_KEY": "your_api_key",
        "SCIENCEON_CLIENT_ID": "your_client_id",
        "SCIENCEON_MAC_ADDRESS": "your_mac_address"
      }
    }
  }
}
```

---

### 방법 2: uv로 소스 직접 실행 (개발자용)

소스를 직접 수정하거나 PyPI 미배포 버전을 쓰려는 경우에만 사용합니다.
저장소를 클론한 후 소스에서 직접 실행합니다.

**저장소 클론** (예: `C:\mcp` 폴더 기준):

```powershell
cd C:\mcp
git clone https://github.com/ansua79/scienceon-mcp
```

> git이 없다면 PowerShell에서 `winget install --id Git.Git` 으로 설치하세요.

**Claude Desktop 설정** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "scienceon": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "C:\\mcp\\scienceon-mcp",
        "scienceon-mcp"
      ],
      "env": {
        "SCIENCEON_API_KEY": "your_api_key",
        "SCIENCEON_CLIENT_ID": "your_client_id",
        "SCIENCEON_MAC_ADDRESS": "your_mac_address"
      }
    }
  }
}
```

---

### Claude Desktop 설정 파일 위치

| OS | 경로 |
|----|------|
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |

설정 파일을 수정한 후 Claude Desktop을 재시작하면 적용됩니다.

---

## 기술 스택

- Python 3.10+
- [FastMCP](https://gofastmcp.com) 2.10+
- httpx
- pycryptodome (ScienceON AES 인증)

> 모든 API 요청에는 `User-Agent: scienceon-mcp/<버전>` 헤더가 포함되어, ScienceON 서버 로그에서 이 MCP를 통한 호출을 식별할 수 있습니다.

## 관련 링크

- [ScienceON](https://scienceon.kisti.re.kr) — KISTI 과학기술 지식인프라 서비스
- [KISTI 한국과학기술정보연구원](https://www.kisti.re.kr) — 본 API를 제공하는 기관
- [STIMCP Manager](https://github.com/ansua79/stimcp-manager) — MCP 서버를 클릭만으로 설치·관리하는 GUI 도구 (초보자용)
- [kisti-mcp](https://github.com/ansua79/kisti-mcp) — KISTI 관련 MCP 프로젝트

## 라이선스

CC-BY-NC-4.0
