# agent-registry-on-agentcore

**에이전트 계층(Agent Layer) = ⓐ Agent Registry + ⓑ A2A 런타임** 개념을
**Amazon Bedrock AgentCore**에서 실제로 검증한 테스트와 산출물.

> 검증 가설: 엔터프라이즈 AI Gateway의 "에이전트 계층"은 두 원시(primitive)로 구성된다 —
> **ⓐ Agent Registry**(에이전트·도구의 발견·거버넌스·수명주기 = control plane) +
> **ⓑ A2A 런타임**(레지스트리에서 발견한 에이전트를 안전하게 호출·위임 = data plane).
> 이 repo는 그 가설이 AgentCore의 실 API로 성립함을 라이브로 입증한다.

- Region: `us-west-2` · boto3 `1.43.25`
- 검증 일자: 2026-06-17
- 비용: 레지스트리는 메타데이터 카탈로그 — 사실상 $0, 테스트는 생성 즉시 전량 삭제

---

## 1. 네 가지 AgentCore 에이전트-계층 원시

| 원시 | 역할 | 위치 | 본 repo 검증 수준 |
|---|---|---|---|
| **Agent Registry** | 에이전트·도구·스킬의 카탈로그 — 발견·승인·버전·폐기 | control plane | ✅ **라이브 검증** (lifecycle 전 구간) |
| **A2A protocol** | 에이전트 간 표준 — Agent Card, 분산 발견, 위임 | 표준 | ✅ **스키마 검증** (Registry가 A2A v0.3.0 카드 강제 검증) |
| **Agent Runtime** | A2A 에이전트 실행/호출 엔드포인트 | data plane | 🔍 API 표면 확인 (read-only) |
| **Gateway** | MCP **도구** 프록시 (agent→resource) — A2A 게이트웨이 아님 | data plane | 🔍 API 표면 확인 (read-only) |

> 중요: AgentCore **Gateway**는 MCP **도구** 프록시(에이전트→리소스)이며,
> 에이전트↔에이전트 게이트웨이가 아니다. 에이전트 발견·거버넌스는 **Registry**,
> 에이전트 간 실행은 **Runtime + A2A**가 담당한다. (전체 API 목록은 `docs/agentcore-api-inventory.md`)

---

## 2. 라이브 검증 결과 (ⓐ Agent Registry)

`test_agentcore_registry.py`가 실제 계정에서 1 사이클을 수행하고 전량 정리:
**레지스트리 생성 → A2A 에이전트 + 도구 레코드 퍼블리시 → 승인 워크플로우 → 검색 → 폐기 → 삭제.**

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

## 3. 운영 학습 (실측에서 얻은 것)

- **control-plane `list`/`search`는 강한 eventual consistency** — 방금 만든 DRAFT 레코드가
  `ListRegistryRecords`/`SearchRegistryRecords`에 한동안 안 뜬다. 반면 **`GetRegistryRecord`(ID 직접 조회)는 즉시 강한 일관성.**
- **정리(cleanup)는 레코드 ID에 의존** — `list`가 비면 레코드를 못 지워 레지스트리 삭제가 막힌다.
  실무 권장: **레코드 생성 즉시 응답의 `recordArn`을 로깅**해 ID를 확보할 것.
- ID를 잃었을 때의 안전망: **CloudTrail `LookupEvents(EventName=CreateRegistryRecord)`** 가
  `responseElements.recordArn`을 보존하므로 ID 복구 가능 (이 repo의 검증 중 실제로 사용).
- 레지스트리는 생성에 **1~2분(`CREATING`→`READY`)** 소요 — READY 전 레코드 생성 시 `ConflictException`.
- `CreateRegistry` 응답은 **`registryArn`만** 반환(별도 `registryId` 없음) → ARN에서 파싱.

---

## 4. 실행 방법

```bash
# 본인 AWS 자격증명(us-west-2, AgentCore 권한) 하에서:
python3 test_agentcore_registry.py
```

스크립트는 임시 리소스만 생성하고 `finally`에서 전량 삭제한다(완전 되돌림).
실패해도 누수가 없도록 정리 루틴이 동작하며, 그래도 잔여물이 남으면
`CreateRegistry` 응답이 비어 ID를 못 잡은 경우이니 위 CloudTrail 방법으로 복구·삭제할 것.

---

## 5. 파일 구성

```
agent-registry-on-agentcore/
├── README.md                          # 본 문서: 개념 + 라이브 검증 결과 + 학습
├── test_agentcore_registry.py         # ⓐ Agent Registry 수명주기 라이브 테스트 (자가 정리)
└── docs/
    ├── agentcore-api-inventory.md     # Registry/Runtime/Gateway/A2A 전체 API 인벤토리(read-only)
    └── agent-gateway-vs-registry.md   # 업계 개념 정리: Gateway(런타임) vs Registry(카탈로그)
```

## 6. 핵심 결론

**"에이전트 계층 = Agent Registry + A2A 런타임"은 AgentCore에서 가공 개념이 아니라 실제 API 표면으로 존재한다.**
하나의 레지스트리가 A2A 에이전트(스키마 검증)와 도구를 공동 카탈로그하고,
`DRAFT→APPROVED→DEPRECATED` 거버넌스 수명주기를 API로 강제한다.
이는 "AWS에 관리형 에이전트 레지스트리가 없다"는 통념(2025년 기준)을 교정한다 —
AWS Agent Registry는 Preview로 실재하며, 본 repo가 그 동작을 라이브로 증명한다.
