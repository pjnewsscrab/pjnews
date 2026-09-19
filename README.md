# policy-news-bot

대외협력·입법·규제 실무용 텔레그램 뉴스 모니터링 봇. GitHub Actions에서 무료로 돌아간다.

## 무엇을 하나

- **실시간 배치**: 평일 05~23시 매시 07분, 주말 3시간 간격으로 뉴스를 수집해 텔레그램으로 발송
- **조간 브리핑 (평일 08:00)**: 게임·넷마블 트랙 일일 리포트 (`game`)
- **마감 리포트 (평일 18:00)**: 외교·통일 트랙 일일 리포트 (`foreign`)

발송 시각을 옮기려면 `news.yml`의 cron과 `news_bot_v4.py`의 `DAILY_SCHEDULE`을 **함께** 고쳐야 한다. 한쪽만 바꾸면 트랙 판정이 조용히 어긋난다.

## 트랙 구조

| 섹션 | 성격 | 최소 점수 |
|---|---|---|
| `게임·넷마블` | 게임 산업·규제, 정당/국회 게임 기구 | 4 (우선) |
| `외교·통일` | 외교부·통일부·재외동포청·ODA·북한 | 4 (우선) |
| `국회·입법` | 법안 발의·통과, 입법예고, 국정감사, 특위 | 4 (우선) |
| `정치` `경제` `사회` `국제` `과학·기술` `문화·스포츠` `종합` | 일반 커버리지 | 4 |

우선 섹션은 세 가지 특권을 갖는다.

1. `balanced_rows()`의 라운드로빈에서 **맨 앞 버킷**을 차지한다
2. `candidate_rows()`에서 AI 검토 후보 **최소 자리를 예약**받는다
3. AI 선정 시 `priority_minimum_score`(= 기준점수 − 1)로 **문턱이 한 단계 낮다**

## 설정 — config.json이 기준이다

`config.json`에 적은 항목은 코드의 `DEFAULT_*`에 **병합**된다. 파일에 한 줄 추가하면 그대로 운영에 반영된다.

```jsonc
{
  "search_sections": { "새 검색어": "게임·넷마블" },   // 검색어 추가 (섹션 지정)
  "drop_search_sections": ["문화"],                  // 기본 검색어 제거
  "section_words":  { "게임·넷마블": ["새 분류어"] },   // 제목 분류어 추가
  "section_strong": { "게임·넷마블": ["확실한 단어"] }, // 본문에서만 인정할 단어
  "priority_sections": ["게임·넷마블", "외교·통일", "국회·입법"],
  "high_volume_queries": ["국회"],                    // 2페이지까지 조회할 검색어
  "news_min_score": 4,
  "lookback_hours": 3,
  "night_lookback_hours": 7,
  "weekend_lookback_hours": 4,
  "max_articles_per_batch": 8
}
```

> `queries`와 `groups` 키는 **폐기**됐다. 검색어는 `search_sections`에서 자동 파생된다.
> 예전 버전은 `cfg['queries']`를 코드에서 덮어써서 config.json 편집이 반영되지 않았다.

### 검색어를 늘리는 게 가장 효과가 크다

네이버 뉴스 검색 API는 하루 25,000건 무료다. 현재 약 88개 검색어 × 하루 19회 ≈ 1,700건으로 **여유가 14배 남는다.** 놓치는 사안이 있으면 주저 말고 `search_sections`에 추가하라.

## 점수 규칙

`priority_analysis()`는 신호어를 네 축으로 나눠 채점한다.

| 축 | 예시 | 가중치 |
|---|---|---|
| `STRONG_SIGNALS` | 발의, 통과, 가결, 개정, 입법예고, 시행령, 과징금, 제재 | +2 |
| `ORG_SIGNALS` | 위원장, 선임, 인선, 출범, **비상설특별위원회**, 특위, 간사 | +1 |
| `MEDIUM_SIGNALS` | 단독, 발표, 공약, 국정감사, 공청회, 실적 | +1 |
| `NOISE_SIGNALS` | 시상식, 우승, 팬미팅, 쿠폰, 사전예약 / `MC` `GV` `OST` | 상한 2점 |

추가 보정: 우선 섹션은 하한 4점(입법·인사 신호가 함께 있으면 5점), 문화·스포츠 −1점, **지방의회 단신**(시의원·조례 등)은 중앙 정책어가 없으면 상한 2점.

## 운영

```bash
python news_bot_v4.py --demo               # sample.json 미리보기, 발송 없음
python news_bot_v4.py                      # 실제 수집, 콘솔 출력만
python news_bot_v4.py --send --watch       # 실제 발송 + 슬롯 관리
python news_bot_v4.py --send --daily game      # 조간 브리핑(게임)만 수동 실행
python news_bot_v4.py --send --daily foreign   # 마감 리포트(외교·통일)만 수동 실행
python runner.py                           # Actions와 동일 경로 (Secret 필요)
```

Actions 화면의 **Run workflow**에서 `daily_report`를 `foreign` / `game` / `both`로 지정하면 리포트를 수동 발송할 수 있다.

## 필요한 Secret

| 이름 | 필수 | 용도 |
|---|---|---|
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | ✅ | 네이버 뉴스 검색 |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | ✅ | 발송 |
| `GEMINI_API_KEY` | 권장 | 선정·요약·리포트. 없으면 규칙 모드로 자동 강등 |
| `TELEGRAM_POLICY_CHAT_ID` | 선택 | 정책 전용 채널 분리 |

`GH_TOKEN`은 Actions가 자동 주입한다.

## 발송 기록

`newsbot-state.json`(기본 브랜치)에 보관한다. 저장 대상은 **발송·예약된 기사와 슬롯뿐**이며 API 키는 저장하지 않는다. 최초 실행 시 `live-rules.sqlite3`가 있으면 1회 이전한다.

텔레그램 발송 **직전마다** 원격에 먼저 저장하므로, 실행이 중간에 끊겨도 같은 기사가 다시 나가지 않는다. 저장소가 public이면 기사 제목·요약이 공개되므로 private 전환이나 별도 저장소를 권한다.

## 장애 대응

| 증상 | 확인 |
|---|---|
| 텔레그램에 ⚠️ 실패 알림 | Actions 로그의 traceback |
| 기사가 안 옴 | `INFO 유사 보도 제외 / 처리` 로그, `news_min_score` |
| 특정 사안을 놓침 | `config.json`의 `search_sections`에 검색어 추가 |
| 요약 없이 제목만 옴 | Gemini 폴백. 로그의 `Gemini 선정 생략 (사유)` |
| `deliveries`에 `sending` 잔류 | 발송 중 네트워크 단절. 수동 확인 필요 |

## 아키텍처

```
runner.py            Actions 진입점 · 상태 로드/저장 · 실패 알림
└─ news_bot_v4.py
   ├─ load_config()      config.json 병합 → 검색·분류 기준 확정
   ├─ collect()          네이버 검색(병렬) + 우선 언론사 RSS + 추가 RSS
   ├─ ingest()           URL/제목 중복 제거, 동일 매체 사본 병합
   ├─ profile_section()  섹션 분류 (우선 섹션 먼저, 제목 우선)
   ├─ priority_analysis() 규칙 채점
   ├─ gemini_selection() AI 선정 — 항목 단위 검증, 부분 실패 허용
   ├─ enrich_summaries() 네이버 본문 발췌 기반 재요약
   ├─ message()          단일 HTML 카드 서식
   └─ run_daily()        트랙별 일일 리포트
```

## 보안

- 모든 AI 프롬프트가 입력 기사를 신뢰할 수 없는 자료로 명시하고 지시문을 무시하도록 지시
- `ai_text()`가 AI 응답의 URL·말줄임표·미완결 문장을 차단
- 선정 결과는 후보 ID 화이트리스트로 대조 — 없는 기사를 만들어낼 수 없음
- 본문 수집은 네이버 호스트 화이트리스트 + 리디렉션 차단 + Content-Type 검사
- 예외 로그에 키·요청 URL을 남기지 않음

## 남은 개선 과제

- [ ] 열린국회정보 API(open.assembly.go.kr) 연동 — 의안 발의·위원회 일정
- [ ] 국민참여입법센터 입법예고 RSS
- [ ] 텔레그램 포럼 토픽으로 섹션별 스레드 분리
- [ ] 상태 저장을 Actions cache 또는 별도 저장소로 이전 (커밋 히스토리 보호)
- [ ] 금요일 주간 리포트
