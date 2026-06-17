# Agent Gateway vs Agent Registry — 업계 개념 정리

에이전트 계층을 설계할 때 자주 혼동되는 두 원시를 출처 기반으로 구분한다.
(2025–2026 벤더/표준 자료 기준. 본 repo의 AgentCore 라이브 검증을 위한 개념 토대.)

## 한 줄 정의

- **Agent Gateway** = **런타임 데이터패스 프록시/메시.** 에이전트와 LLM·도구(MCP)·다른 에이전트(A2A)
  사이에서 보안·라우팅·프로토콜 변환·관측성·가드레일을 담당. *연결 계층이지 카탈로그가 아니다.*
- **Agent Registry** = **컨트롤플레인 카탈로그.** 에이전트·도구가 무엇이 있고, 승인됐는지,
  어떤 버전인지를 관리하는 인벤토리. *발견·거버넌스·수명주기 계층.*

## 분리 비교

| 관심사 | Agent Gateway (runtime) | Agent Registry (control plane) |
|---|---|---|
| 역할 | 라이브 트래픽의 연결·보안·라우팅 | 발견·거버넌스·수명주기 |
| 대표 기능 | A2A/MCP/LLM 라우팅, 인증, 가드레일, 트레이싱, 레이트리밋 | 퍼블리시/승인/폐기, 시맨틱 검색, 버전관리, 메타데이터, 접근 거버넌스 |
| 위치 | 요청 경로 **위**(traversed) | 경로 **옆**(queried) |
| AWS | AgentCore Gateway(MCP 도구 프록시)·Runtime(A2A) | **AWS Agent Registry** (AgentCore, Preview) |
| 오픈소스 | agentgateway.dev, Solo.io Agent Mesh, Gravitee A2A Proxy | A2A Agent Cards(분산 발견), awslabs `a2a-agent-registry-on-aws` 샘플 |

## 조합 방식 (control plane + data plane)

레지스트리에서 **발견**하고 → 게이트웨이로 **실행**한다.
에이전트/사람이 승인·버전관리된 에이전트를 레지스트리에서 찾고,
그 호출을 게이트웨이가 라우팅·인증·관측한다. (TrueFoundry, AWS 모두 동일 구조로 포지셔닝.)

## 표준화 수준 (주의)

- "Agent Gateway"는 **신생 벤더 용어**이며 표준 스펙이 아니다. 표준은 **MCP**(도구)·**A2A**(에이전트 간)
  프로토콜이고, 게이트웨이는 그 프로토콜을 구현하는 프록시.
- 일부 벤더(TrueFoundry)는 registry/discovery를 Agent Gateway의 한 기능으로 **번들**해 경계가 흐려진다.
  반면 AWS·Solo.io는 Gateway(런타임)와 Registry(카탈로그)를 **별도 서비스로 분리**한다.
- **A2A 프로토콜은 중앙 레지스트리를 강제하지 않는다** — `/.well-known/agent-card.json`(RFC 8615)
  기반 분산 발견이 기본이고, 큐레이션 레지스트리는 선택적 배포 결정.

## 주요 출처

- https://agentgateway.dev/ — 오픈소스 Agent Gateway 정식 정의(LF 프로젝트)
- https://www.truefoundry.com/blog/agent-gateway — 역량 + gateway vs registry
- https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry.html — AWS Agent Registry (Preview)
- https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html — AgentCore Gateway = MCP 도구 프록시
- https://a2a-protocol.org/latest/topics/agent-discovery/ — A2A 분산 발견(중앙 레지스트리 비강제)
- https://www.solo.io/press-releases/solo-io-launches-agent-gateway-and-introduces-agent-mesh — Agent Mesh
