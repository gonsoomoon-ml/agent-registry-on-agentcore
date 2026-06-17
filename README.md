# agent-registry-on-agentcore

**에이전트 계층(Agent Layer) = ⓐ Agent Registry + ⓑ A2A 런타임** 개념을
**Amazon Bedrock AgentCore**에서 실제로 검증한 테스트와 산출물.

> 검증 가설: 엔터프라이즈 AI Gateway의 "에이전트 계층"은 두 원시(primitive)로 구성된다 —
> **ⓐ Agent Registry**(에이전트·도구의 발견·거버넌스·수명주기 = control plane) +
> **ⓑ A2A 런타임**(레지스트리에서 발견한 에이전트를 안전하게 호출·위임 = data plane).
> 이 repo는 그 가설이 AgentCore의 실 API로 성립함을 라이브로 입증한다.

- 검증 일자: 2026-06-17 · boto3 `1.43.25`
- 두 부분으로 구성:
  - **개념 검증** — `test_agentcore_registry.py` : 레지스트리 수명주기 (region `us-west-2`, 자가 정리) — §2~5
  - **워킹 데모** — `agents_and_tools.py` / `register.py` / `client.py` : 호출자가 런타임을 하드코딩하지 않고 **레지스트리에서 harnessArn을 받아** AgentCore **Harness**(Sonnet 4.6)를 호출 → 실제 결과 (region `us-east-1`) — §6
- 비용: 레지스트리·Harness는 메타데이터/설정 — 유휴 시 ~$0. 데모 호출 시에만 소액 LLM 토큰.

---

## 1. 아키텍처 — 발견(control plane) → 실행(data plane)

```
   caller : app / supervisor agent
      │
      │  (1) DISCOVER  ─ "승인된 churn-predictor를 찾아줘"  (경로를 통과하지 않고 '조회')
      ▼
┌─ CONTROL PLANE ─ bedrock-agentcore-control ───────────────────────────
│  [A] AGENT REGISTRY        카탈로그 + 거버넌스 (queried, beside the path)
│      records   : A2A agents │ MCP tools │ skills   (descriptorType)
│      lifecycle : DRAFT → PENDING_APPROVAL → APPROVED → DEPRECATED
│      discover  : SearchRegistryRecords  (semantic)
│      access    : AWS_IAM │ CUSTOM_JWT
└───────────────────────────────────────────────────────────────────────
      │
      │  returns : 승인된 recordArn + version + endpoint
      ▼
      │  (2) EXECUTE   ─ 발견한 대상을 데이터 경로로 호출  (경로를 '통과')
      ▼
┌─ DATA PLANE ─ bedrock-agentcore (runtime) ────────────────────────────
│  [B] A2A RUNTIME    : InvokeAgentRuntime · GetAgentCard · agent ↔ agent
│      GATEWAY (MCP)  : MCP 도구 프록시 (agent → resource) · MCP ↔ REST/Lambda
│      cross-cutting  : auth · route · translate · trace · guardrails
└───────────────────────────────────────────────────────────────────────
      │
      │  (3) RESULT  ─ 결과가 caller로 회귀
      ▼
   agents / tools / data sources   (A2A peers, MCP tools)
```

**설명.** 핵심은 두 평면(plane)이 **서로 다른 위치**에 산다는 점이다.

- **Control plane = Agent Registry.** 요청 경로 *옆*에 있고 **조회(query)** 대상이다.
  "우리 조직에 어떤 에이전트·도구가 있고, 승인됐는지, 어떤 버전인지"를 답한다.
  트래픽이 이곳을 *통과하지 않는다* — 호출 전에 한 번 물어보는 카탈로그다.
- **Data plane = A2A Runtime + Gateway.** 요청 경로 *위*에 있고 라이브 호출이 이곳을 **통과(traverse)** 한다.
  레지스트리에서 발견한 에이전트를 A2A로 호출(`InvokeAgentRuntime`/`GetAgentCard`)하거나,
  도구를 Gateway(MCP 프록시)로 호출하며 그 과정에서 인증·라우팅·변환·추적을 적용한다.

흐름은 항상 **(1) 레지스트리에서 발견 → (2) 런타임/게이트웨이로 실행 → (3) 결과 회귀**.
이 "discover → execute" 분리가 곧 가설의 **ⓐ Registry + ⓑ A2A 런타임** 구조다.

> 용어 주의: AgentCore **Gateway**는 MCP **도구** 프록시(agent→resource)이며 **에이전트↔에이전트 게이트웨이가 아니다.**
> 에이전트 *발견·거버넌스*는 Registry, 에이전트 *간 실행*은 Runtime+A2A가 담당한다.

### 네 가지 원시 요약

| 원시 | 역할 | 평면 | 본 repo 검증 수준 |
|---|---|---|---|
| **Agent Registry** | 에이전트·도구·스킬 카탈로그 — 발견·승인·버전·폐기 | control | ✅ **라이브 검증** (lifecycle 전 구간) |
| **A2A protocol** | 에이전트 간 표준 — Agent Card, 분산 발견, 위임 | 표준 | ✅ **스키마 검증** (Registry가 A2A v0.3.0 카드 강제 검증) |
| **Agent Runtime** | A2A 에이전트 실행/호출 엔드포인트 | data | 🔍 API 표면 확인 (read-only) |
| **Gateway** | MCP **도구** 프록시 (agent→resource) | data | 🔍 API 표면 확인 (read-only) |

(전체 API 목록은 `docs/agentcore-api-inventory.md`, 개념 비교는 `docs/agent-gateway-vs-registry.md`)

---

## 2. 거버넌스 수명주기 (ⓐ Registry가 API로 강제)

```
 [ DRAFT ]
     │   SubmitRegistryRecordForApproval
     ▼
 [ PENDING_APPROVAL ] ──UpdateRegistryRecordStatus = REJECTED──▶ [ REJECTED ]
     │
     │   UpdateRegistryRecordStatus = APPROVED      (보안·품질 리뷰 통과)
     ▼
 [ APPROVED ]
     │   UpdateRegistryRecordStatus = DEPRECATED    (graceful deprecation)
     ▼
 [ DEPRECATED ]
```

**설명.** 사용자 정의의 "퍼블리시/승인/폐기 워크플로우"가 추상이 아니라 **레코드 상태머신으로 실재**한다.
`CreateRegistry(approvalConfiguration.autoApproval=False)`로 만들면 새 레코드는 `DRAFT`에서 출발하고,
`APPROVED` 없이는 배포 불가 — 신규 에이전트/도구에 보안·품질 게이트를 강제할 수 있다.
폐기는 삭제가 아니라 `DEPRECATED` 전이라 소비자에게 graceful하게 알린다.

---

## 3. 라이브 검증 결과 (ⓐ Agent Registry)

| 검증 항목 | 결과 | 증거 / 사용 API |
|---|---|---|
| 레지스트리 생성 | ✅ `READY`(~70초) | `CreateRegistry(approvalConfiguration.autoApproval=False)` |
| **ⓑ A2A 레코드 (스키마 검증)** | ✅ | `descriptorType=A2A`, `descriptors.a2a.agentCard` — **잘못된 카드는 `does not match any supported version`으로 거부**, 유효한 A2A v0.3.0 카드만 수락 |
| 도구 레코드(한 레지스트리 공동 카탈로그) | ✅ | `descriptorType=CUSTOM` — 에이전트 + 도구가 같은 레지스트리에 공존 |
| **① 퍼블리시/승인/폐기 수명주기** | ✅ enum 강제 | `DRAFT → PENDING_APPROVAL → APPROVED → DEPRECATED` + `SubmitRegistryRecordForApproval` / `UpdateRegistryRecordStatus` |
| **⑤ 접근 제어** | ✅ | `CreateRegistry(authorizerType = AWS_IAM \| CUSTOM_JWT)` |
| **② 시맨틱 디스커버리** | ⚠️ 존재하나 지연 | `SearchRegistryRecords` 작동, 단 신규/DRAFT는 즉시 미색인 (eventual consistency) |

### 사용자 정의 "Agent Registry 5대 기능" ↔ 실제 AWS API

| 사용자 정의 기능 | AgentCore API | 상태 |
|---|---|---|
| ① 퍼블리시/승인/폐기 워크플로우 | `SubmitRegistryRecordForApproval` + `UpdateRegistryRecordStatus` (status enum) | ✅ 네이티브 |
| ② 시맨틱 디스커버리 | `SearchRegistryRecords` (data plane) | ✅ 네이티브(색인 지연) |
| ③ 버전 관리 | `recordVersion` + `ListAgentRuntimeVersions` | ✅ 네이티브 |
| ④ 의존성 그래프 | (AWS 표준 없음) | ⚠️ **고객 구축/시각화 레이어** |
| ⑤ 접근 제어 | `authorizerType=AWS_IAM\|CUSTOM_JWT` + 레코드 상태 | ✅ 네이티브 |

→ **5개 중 4개가 AWS 네이티브 API로 직접 매핑.** ④ 의존성 그래프만 사용자의 독자 추가이며,
슬라이드/제안에서는 "고객이 메타데이터 위에 구축하는 시각화 레이어"로 정직하게 표기할 것.

---

## 4. 테스트 흐름 & 실행

```
test_agentcore_registry.py   (us-west-2, self-cleaning)

  CreateRegistry  ──▶  wait READY (~70s)
       │
       ├─▶ CreateRegistryRecord  A2A agent  (agentCard v0.3.0)  ─┐ schema-validated;
       ├─▶ CreateRegistryRecord  tool (CUSTOM)                  ─┘ one registry, many types
       │
       ├─▶ SubmitRegistryRecordForApproval ──▶ UpdateRegistryRecordStatus = APPROVED
       ├─▶ SearchRegistryRecords            (semantic; eventually consistent)
       ├─▶ UpdateRegistryRecordStatus = DEPRECATED
       │
       └─▶ finally:  DeleteRegistryRecord × N ──▶ DeleteRegistry   (full teardown)
```

```bash
# 본인 AWS 자격증명(us-west-2, AgentCore 권한) 하에서:
python3 test_agentcore_registry.py
```

스크립트는 임시 리소스만 생성하고 `finally`에서 전량 삭제한다(완전 되돌림).
실패해도 누수가 없도록 정리 루틴이 동작하며, 그래도 잔여물이 남으면
`CreateRegistry` 응답이 비어 ID를 못 잡은 경우이니 아래 CloudTrail 방법으로 복구·삭제할 것.

---

## 5. 운영 학습 (실측에서 얻은 것)

- **control-plane `list`/`search`는 강한 eventual consistency** — 방금 만든 DRAFT 레코드가
  `ListRegistryRecords`/`SearchRegistryRecords`에 한동안 안 뜬다. 반면 **`GetRegistryRecord`(ID 직접 조회)는 즉시 강한 일관성.**
- **정리(cleanup)는 레코드 ID에 의존** — `list`가 비면 레코드를 못 지워 레지스트리 삭제가 막힌다.
  실무 권장: **레코드 생성 즉시 응답의 `recordArn`을 로깅**해 ID를 확보할 것.
- ID를 잃었을 때의 안전망: **CloudTrail `LookupEvents(EventName=CreateRegistryRecord)`** 가
  `responseElements.recordArn`을 보존하므로 ID 복구 가능 (이 repo의 검증 중 실제로 사용).
- 레지스트리는 생성에 **1~2분(`CREATING`→`READY`)** 소요 — READY 전 레코드 생성 시 `ConflictException`.
- `CreateRegistry` 응답은 **`registryArn`만** 반환(별도 `registryId` 없음) → ARN에서 파싱.

---

## 6. 워킹 데모 — 호출자는 런타임을 하드코딩하지 않는다 (레지스트리 간접화)

**핵심 메시지: 레지스트리는 *간접화(indirection)* 계층이다.** 호출자(`client.py`)는 에이전트의
런타임을 코드에 박지 않는다 — 레지스트리에 "`churn-predictor`를 구현한 런타임이 뭐냐"고 물어
**가리키는 harnessArn을 호출**한다. 레코드 뒤의 런타임을 갈아끼워도 호출자 코드는 그대로다.
이것이 §1의 "discover → execute"이자, AI Gateway의 "단일 통제점 뒤에서 갈아끼우기"와 같은 가치다.

> **정직한 범위.** 이 데모의 조회는 `.agentcore_state.json`에 적힌 **알려진 recordId로 `GetRegistryRecord`**
> 하는 *lookup*이다(재현성을 위해). 레지스트리의 *진짜* 발견 기능 — 시맨틱 검색(§3 ②)과
> 승인 게이트(§2 lifecycle) — 는 별도로 증명돼 있고, 여기서는 호출 경로를 단순·결정적으로 두었다.

에이전트는 컨테이너 없는 관리형 런타임 **AgentCore Harness**(Sonnet 4.6)이고, 도구는
`inline_function`으로 선언해 **클라이언트가 로컬 실행**한다. 도메인은 고객 이탈(churn) 예측.

```
agents_and_tools.py  →  register.py  →  client.py
   (1) 정의              (2) 등록          (3) 조회 → 호출 → 결과

   user: "Assess churn risk for C-1001"
      │
      ▼  client.py: .agentcore_state.json(recordId) → GetRegistryRecord
┌─ RESOLVE ─ registry (간접화 지점) ────────────────────────────
│  agent record → harnessArn   ← 호출자는 이 arn을 '하드코딩하지 않고' 레지스트리에서 받는다
│  tool record  → customer_db_search (inline_function 스펙)
└───────────────────────────────────────────────────────────────
      │
      ▼  InvokeHarness(harnessArn, messages)
┌─ AGENT ─ AgentCore Harness (관리형, Sonnet 4.6) ──────────────
│  systemPrompt 루브릭으로 추론 → tool_use emit
└───────────────────────────────────────────────────────────────
      │  stopReason = tool_use
      ▼  client가 도구를 '로컬' 실행 (inline_function)
┌─ TOOL ─ customer_db_search("C-1001")  [client-side] ──────────
│  → {orders_last_90d:1, avg_review_score:2.3, late_shipments:2, days_since_last_order:74}
└───────────────────────────────────────────────────────────────
      │  toolResult → InvokeHarness (다음 턴)
      ▼
   RESULT: {"churn_risk":"high","score":0.95,"reasons":[...]}
```

**간접화의 산 증거 (디버깅 중 실제로 일어남).** 처음엔 잘못된 모델 id로 Harness를 만들었고,
`UpdateHarness`로 모델을 `us.anthropic.claude-sonnet-4-6`로 **제자리 교체**했다. harnessArn(레코드가
가리키는 값)은 그대로였기에 **`client.py`를 한 줄도 고치지 않고** 다시 동작했다 — 레지스트리가
호출자와 런타임을 디커플링한다는 증거다.

또 하나의 축: 에이전트(Harness)는 **AWS에서 Sonnet 4.6로 추론**하고 도구는 **우리 프로세스에서 실행**된다.
`inline_function`은 모델이 `tool_use`를 내보내면 클라이언트가 실행해 `toolResult`를 회신하는 구조다 —
관리형 추론과 클라이언트 측 도구가 신뢰경계를 나눠 가진다.

### 실행

```bash
# region us-east-1 · AgentCoreHarnessRole · Sonnet 4.6 inference profile 사용
python3 register.py            # Harness + 레지스트리 + 레코드 생성, .agentcore_state.json 기록
python3 client.py C-1001       # 조회(레지스트리) → 호출(Harness) → 결과
python3 client.py C-2002       # 다른 고객
python3 client.py --cleanup    # Harness + 레지스트리 + 레코드 전량 삭제
```

### 라이브 결과 (검증됨)

| 입력 | mockup 신호 | 에이전트 판단 |
|---|---|---|
| `C-1001` | 4개 전부 (74일 미주문·리뷰 2.3·지연 2·주문 1) | `churn_risk: high` · `score 0.95` |
| `C-2002` | 0개 (활발·만족) | `churn_risk: low` · `score 0.05` |

동일한 에이전트·도구·레지스트리가 입력에 따라 정확히 다른 판단을 내린다.

### 라이브에서 얻은 함정 3가지 (소스에 반영됨)

- `harnessName`은 `[a-zA-Z][a-zA-Z0-9_]{0,39}` — **하이픈 불가**, 언더스코어만.
- `anthropic.claude-sonnet-4-6`는 on-demand 미지원 → **`us.` cross-region inference profile** 필요
  (`us.anthropic.claude-sonnet-4-6`). 목록(`list_inference_profiles`)엔 안 떠도 호출 가능.
- Harness `toolResult` content는 `json` 구조 타입을 거부 → **`text`(JSON 문자열)** 로 회신.

> 비즈니스 로직(이탈 루브릭)은 `agents_and_tools.py`의 `SYSTEM_PROMPT`에 있다 — 가중치·임계값을 바꾸면 판단이 바뀐다.

---

## 7. 파일 구성

```
agent-registry-on-agentcore/
├── README.md                          # 본 문서: 아키텍처 + 라이브 검증 + 워킹 데모
├── test_agentcore_registry.py         # 개념 검증: Registry 수명주기 (자가 정리, us-west-2)
├── agents_and_tools.py                # 데모(1): 도구 구현(mockup) + 에이전트 정의(model·prompt·tool)
├── register.py                        # 데모(2): Harness 생성 + 레지스트리 등록 → .agentcore_state.json
├── client.py                          # 데모(3): 발견 → InvokeHarness 루프(도구 로컬) → 결과 / --cleanup
└── docs/
    ├── agentcore-api-inventory.md     # Registry/Runtime/Gateway/A2A/Harness API 인벤토리(read-only)
    └── agent-gateway-vs-registry.md   # 업계 개념 정리: Gateway(런타임) vs Registry(카탈로그)
```

## 8. 핵심 결론

**"에이전트 계층 = Agent Registry + A2A 런타임"은 AgentCore에서 가공 개념이 아니라 실제 API 표면으로 존재한다.**
하나의 레지스트리가 A2A 에이전트(스키마 검증)와 도구를 공동 카탈로그하고,
`DRAFT→APPROVED→DEPRECATED` 거버넌스 수명주기를 API로 강제한다.
이는 "AWS에 관리형 에이전트 레지스트리가 없다"는 통념(2025년 기준)을 교정한다 —
AWS Agent Registry는 Preview로 실재하며, 본 repo가 그 동작을 라이브로 증명한다.
