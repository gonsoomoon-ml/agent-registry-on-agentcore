# Agent Gateway — 에이전트 계층의 단일 통제점

> **한 줄 요약:** Agent Gateway는 **발견·거버넌스(Agent Registry)** 와 **라우팅·보안(A2A Runtime)** 을
> 결합한 에이전트 계층의 통제점이다 — "레지스트리에서 찾고, 런타임으로 안전하게 실행한다."

이 문서는 AI Gateway 3계층(LLM / Tool / **Agent**) 중 **에이전트 계층**을 하나의 접근법으로 정리한다.
근거가 되는 라이브 검증은 이 repo의 [README §3·§6](../README.md), 개념 구분은
[agent-gateway-vs-registry.md](agent-gateway-vs-registry.md), API 표면은
[agentcore-api-inventory.md](agentcore-api-inventory.md) 참고.

---

## 1. 문제

조직에 에이전트와 도구가 늘어나면, **"우리 조직에 어떤 에이전트가 있고, 어떤 도구를 사용할 수 있으며,
누가 관리하는지"를 파악하는 것 자체가 과제**가 된다. 여기에 에이전트가 다른 에이전트를 호출(A2A)하기
시작하면, **그 호출을 누가 인증·라우팅·감사하는가**라는 두 번째 과제가 더해진다. 통제점이 없으면
멀티에이전트 시스템은 한 계층 위의 "shadow AI"와 같은 **무통제 메시(mesh)** 가 된다.

## 2. 접근법: Agent Gateway = 두 원시의 결합

| 원시 | 평면 | 역할 |
|---|---|---|
| **Agent Registry** | control plane | 무엇이 있고·승인됐고·어떤 버전인가 — **발견·거버넌스** |
| **A2A Runtime** | data plane | 이 호출을 라우팅·인증·추적 — **실행·보안** |

흐름은 항상 **레지스트리에서 발견 → 런타임으로 실행**("discover → execute").
업계도 이렇게 나눈다 — AWS: AgentCore **Registry** + **Runtime/A2A**, OSS: **agentgateway** + **Agent Cards**.

```
   caller (app / supervisor agent)
      │  (1) 발견 : "어떤 런타임이 churn-predictor를 구현하나?"
      ▼
  ┌─ Agent Registry (control plane) ─ 발견·거버넌스 ─────────
  │   records · lifecycle · semantic search · access control
  └──────────────────────────────────────────────────────────
      │  returns : 승인된·버전이 명시된 런타임 핸들(예: harnessArn)
      ▼  (2) 실행 : A2A로 호출 (auth · route · trace)
  ┌─ A2A Runtime (data plane) ─ 실행·보안 ───────────────────
  │   agent ↔ agent → tool → data  (통합 인증·로깅·정책)
  └──────────────────────────────────────────────────────────
```

## 3. ① Agent Registry — 이 문제를 해결하는 중앙 카탈로그

**핵심 기능**

- **퍼블리시/승인/폐기 워크플로우:** 새 에이전트나 도구를 등록할 때 보안·품질 리뷰를 거치도록 강제.
  더 이상 사용되지 않는 에이전트는 graceful deprecation.
- **시맨틱 디스커버리:** "고객 이탈 예측"이라는 키워드로 관련 에이전트·도구·데이터소스를 한 번에 검색.
- **버전 관리:** 에이전트/도구의 버전별 추적, A/B 테스트 지원, 문제 발생 시 이전 버전으로 즉시 롤백.
- **의존성 그래프:** 에이전트 A가 도구 B에 의존하고, 도구 B가 서비스 C에 의존하는 관계를 시각화.
  변경 영향 분석에 활용.
- **접근 제어:** 에이전트별·팀별·환경별(dev/staging/prod) 접근 권한 관리.

## 4. ② A2A Runtime — 발견한 에이전트를 안전하게 실행

레지스트리에서 찾은 에이전트를 **A2A 프로토콜로 호출**한다: 호출 인증(누가 부를 수 있나) · 프로토콜 변환 ·
가드레일 · 그리고 **"에이전트 → 에이전트 → 도구 → 데이터" 전 경로의 통합 로깅·추적.**

## 5. 검증 — Amazon Bedrock AgentCore에서 라이브로

| 측면 | 결과 |
|---|---|
| Registry 5대 기능 | **4개가 AgentCore 네이티브 API로 직접 매핑** (퍼블리시/승인/폐기 · 시맨틱 검색 · 버전 · 접근 제어). **의존성 그래프만 고객 구축 레이어.** |
| 수명주기 | `DRAFT → PENDING_APPROVAL → APPROVED → DEPRECATED` 실제 전이 확인 |
| Runtime | 관리형 Harness(Sonnet 4.6)가 A2A로 추론·도구 호출·결과 반환 (C-1001 → high 0.95, C-2002 → low 0.05) |

> 기능 ↔ 실제 API 매핑: 퍼블리시/승인/폐기 = `SubmitRegistryRecordForApproval` + `UpdateRegistryRecordStatus` ·
> 시맨틱 디스커버리 = `SearchRegistryRecords` · 버전 = `recordVersion` · 접근 제어 = `authorizerType (AWS_IAM | CUSTOM_JWT)`.

## 6. 가치: 단일 통제점 = 간접화(indirection)

호출자는 런타임을 **하드코딩하지 않고 레지스트리에 묻는다.** → 레코드 뒤의 런타임을 갈아끼워도
**호출자 코드는 그대로**다 (라이브로 확인 — `UpdateHarness`로 모델을 교체해도 클라이언트 불변).
모델 계층의 **"모델 교체 = 게이트웨이 설정 변경"** 과 동일한 불변식이 에이전트 계층에서도 성립한다.

---

**요지:** 문제(파악 자체가 과제) → 접근법(Registry + Runtime) → 기능(5가지) → 검증(라이브 근거) → 가치(간접화).
"Agent Gateway"를 발견과 실행을 결합한 에이전트 계층 통제점으로 정의하면, AWS·OSS의 실제 구성과 일치하고
덱의 "단일 통제점" 논지를 에이전트 계층까지 일관되게 확장한다.
