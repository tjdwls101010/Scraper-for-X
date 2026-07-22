# Implementation kickoff prompt (paste into a fresh session)

Open a new Claude Code session **in the agentic-x repo** and paste the box below.

---

```
agentic-x v0.3.0 "확장"을 구현해줘. 이건 구현 세션이야 (계획은 지난 세션에 끝났고,
계획과 구현을 분리하기 위한 것).

먼저 계획 세 문서를 정독해 (docs/plan/):
- docs/plan/2026-07-20-active-mode-expansion-plan.md   ← 로드맵 (메인)
- docs/plan/2026-07-20-recon-findings.md               ← 실측 근거 (query-id·봉투경로·txid 증거)
- docs/plan/IMPLEMENTATION-KICKOFF.md                  ← 이 프롬프트

배경 한 줄: agentic-x는 이미 "harvest-then-replay"(브라우저로 세션 1회 수확 → 이후 순수
httpx GraphQL)로 작동하는 성숙한 도구다. v0.3.0의 목표는 재구축이 아니라 (1) txid 벽에 막힌
search/replies를 뚫고, (2) 홈 피드와 소셜 그래프를 추가해, 나중에 만들 x-fetch 스킬이 빠른
프리미티브를 연쇄해 사람처럼 X를 탐색하게 하는 것. 다중 홉 탐색은 CLI가 아니라 스킬이 한다.

지난 세션 라이브 리콘으로 증명된 핵심(2026-07-20, 버려도 되는 계정):
- 홈 피드(HomeTimeline)는 txid 없이 순수 httpx로 200. 봉투 data.home.home_timeline_urt.
  instructions → 기존 parse.walk_instructions + build_tweet 그대로 파싱됨. 가장 싼 승리.
- search/replies만 txid 벽(신선한 query-id로도 404). txid는 single-use라 "캡처 후 재생"은
  죽었고(실측), 매 요청 순수-파이썬 생성(옵션 A)이 유일한 길. 생성 재료(검증 메타·애니메이션
  4프레임·ondemand.s)는 오늘 x.com에 다 있음 → A 실현 가능.

진행 방식 (각 Phase의 verify 게이트를 통과할 때까지 loop):
0) Phase 0 재검증: query-id는 회전하니 scratch/recon_login.py 로 재수확, scratch/recon_probe.py
   로 txid 벽 재확인, 그리고 소셜 그래프 op들의 query-id·txid 게이팅을 이번에 처음 프로브해.
   리콘·브라우저는 패키지 자체 scrapling(StealthySession)을 써. recon 프로필 세션이 만료됐으면
   버려도 되는 계정으로 재로그인.
1) 홈 피드 `feed` (txid 없음, 파서 재사용) — envelope root 한 줄 + home_timeline_variables +
   fetch_home + feed 서브커맨드 + 픽스처/테스트.
2) txid 코어 transaction.py — 먼저 스파이크로 "파이썬 생성 txid → SearchTimeline 200"을 증명
   (안 되면 시간 박스 안에서 멈추고 search/replies를 B(브라우저-관찰) 우선으로 전환). 그다음
   모듈 + ReadClient 배선 + 유닛테스트. 공개 MIT 구현을 포팅/벤더링하되 "리버스엔지니어링, 언젠가
   깨짐" 도크스트링 + 검증일자 스탬프. ungated op엔 절대 txid 안 붙임.
3) search + fetch --replies 언블록 — retrieve.py의 두 FeatureNotImplementedError 제거, txid 배선,
   "not implemented" 계약 테스트를 반대로 뒤집기(이제 작동), [browser] 뒤의 B 폴백 추가.
4) 소셜 그래프 — walk_user_instructions + User 리스트 출력 + following/followers/likers/retweeters
   + schema 갱신 + 픽스처.
5) 재포지셔닝/폴리시 — README/wiki/CHANGELOG, 버전 0.3.0, --help 강화.
6) x-fetch 스킬은 PyPI 배포 후 별도 세션.

반드시 지킬 제약:
- CLAUDE.md 준수: 최소 코드, 수술적 변경, 투기적 추상화·미요청 기능 금지. D2 스코프 밖(북마크/
  리스트/알림)은 만들지 마.
- rate floor(0.5s, non-bypassable)와 "단일 타깃·무 배치·무 데몬" 구조 유지.
- PII: scratch/, *.raw.json, output/, profiles/ 는 gitignore됨. 새 유닛 픽스처는 반드시
  스크럽·합성(scripts/check_fixtures_pii.py 통과). 실제 캡처 커밋 금지.
- test_no_scrapling_import.py 그린 유지: transaction.py는 순수 httpx(브라우저 X). B 폴백만
  scrapling을 lazy import.
- X는 페북보다 소송·밴에 공격적. DISCLAIMER.md 톤 약화 금지. 버려도 되는 계정만.

시작 전에: 계획 세 문서를 읽고 → 유닛테스트 그린 여부 확인
(`PYTHONPATH=src .venv/bin/python -m pytest -q tests -p no:cacheprovider`) → Phase 0 실행 계획을
짧게 제시하고 진행. 계획을 벗어나는 스코프 변경이 필요하면 먼저 물어봐.
```

---

**Notes for you (not part of the paste):**
- Repo remote is `github.com/tjdwls101010/Agentic-X` (auto-publishes to PyPI). The plan docs are committed on `main` under `docs/plan/`.
- Pre-existing uncommitted state: the wiki was moved `wiki/` → `docs/wiki/` but not staged (`git status` shows `D wiki/…` + untracked `docs/`). That is the user's own in-progress move — **not** part of this plan; don't entangle it. Commit only what you create.
- The venv doesn't have the package installed and its console-script shebang is stale (repo was relocated) — run Python as `PYTHONPATH=src .venv/bin/python -m …` and `git commit --no-verify` if the pre-commit hook can't launch. (Same workarounds as scraper-for-fb.)
- `init_script` for `StealthySession` must be an **absolute** path (scrapling validates it).
- This whole tree lives under `.tmp/` inside the scraper-for-fb repo temporarily; the user will move it out to its own directory. Keep everything repo-relative, no hardcoded `.tmp/...` paths.
