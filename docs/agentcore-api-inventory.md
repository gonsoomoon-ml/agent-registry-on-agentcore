# Amazon Bedrock AgentCore — Agent-Layer API Inventory

Read-only boto3 introspection (no AWS resources created). Region: `us-west-2`.
boto3 1.43.25.  Captured as part of the Agent Registry + A2A live validation.

## Control plane — `bedrock-agentcore-control`

### Registry
- `CreateRegistry`
- `CreateRegistryRecord`
- `DeleteRegistry`
- `DeleteRegistryRecord`
- `GetRegistry`
- `GetRegistryRecord`
- `ListRegistryRecords`
- `SubmitRegistryRecordForApproval`
- `UpdateRegistry`
- `UpdateRegistryRecord`
- `UpdateRegistryRecordStatus`

### AgentRuntime
- `CreateAgentRuntime`
- `CreateAgentRuntimeEndpoint`
- `DeleteAgentRuntime`
- `DeleteAgentRuntimeEndpoint`
- `GetAgentRuntime`
- `GetAgentRuntimeEndpoint`
- `ListAgentRuntimeEndpoints`
- `ListAgentRuntimeVersions`
- `ListAgentRuntimes`
- `UpdateAgentRuntime`
- `UpdateAgentRuntimeEndpoint`

### Gateway
- `CreateGateway`
- `CreateGatewayRule`
- `CreateGatewayTarget`
- `DeleteGateway`
- `DeleteGatewayRule`
- `DeleteGatewayTarget`
- `GetGateway`
- `GetGatewayRule`
- `GetGatewayTarget`
- `ListGatewayRules`
- `ListGatewayTargets`
- `ListGateways`
- `SynchronizeGatewayTargets`
- `UpdateGateway`
- `UpdateGatewayRule`
- `UpdateGatewayTarget`

### Target
- `CreateGatewayTarget`
- `DeleteGatewayTarget`
- `GetGatewayTarget`
- `ListGatewayTargets`
- `SynchronizeGatewayTargets`
- `UpdateGatewayTarget`

### TokenVault
- `GetTokenVault`
- `SetTokenVaultCMK`

### WorkloadIdentity
- `CreateWorkloadIdentity`
- `DeleteWorkloadIdentity`
- `GetWorkloadIdentity`
- `UpdateWorkloadIdentity`

### ApiKey
- `CreateApiKeyCredentialProvider`
- `DeleteApiKeyCredentialProvider`
- `GetApiKeyCredentialProvider`
- `ListApiKeyCredentialProviders`
- `UpdateApiKeyCredentialProvider`

## Data plane — `bedrock-agentcore`

### Registry
- `SearchRegistryRecords`

### AgentRuntime
- `InvokeAgentRuntime`
- `InvokeAgentRuntimeCommand`

### AgentCard
- `GetAgentCard`

### Invoke
- `InvokeAgentRuntime`
- `InvokeAgentRuntimeCommand`
- `InvokeBrowser`
- `InvokeCodeInterpreter`
- `InvokeHarness`

### Browser
- `GetBrowserSession`
- `InvokeBrowser`
- `ListBrowserSessions`
- `SaveBrowserSessionProfile`
- `StartBrowserSession`
- `StopBrowserSession`
- `UpdateBrowserStream`

### CodeInterpreter
- `GetCodeInterpreterSession`
- `InvokeCodeInterpreter`
- `ListCodeInterpreterSessions`
- `StartCodeInterpreterSession`
- `StopCodeInterpreterSession`

## Key enums (governance lifecycle & record types)

- **Registry record status** (publish→approve→deprecate lifecycle): `['DRAFT', 'PENDING_APPROVAL', 'APPROVED', 'REJECTED', 'DEPRECATED', 'CREATING', 'UPDATING', 'CREATE_FAILED', 'UPDATE_FAILED']`
- **Record descriptorType** (one registry catalogs all): `['MCP', 'A2A', 'CUSTOM', 'AGENT_SKILLS']`
- **Registry authorizerType** (access control): `['CUSTOM_JWT', 'AWS_IAM']`

## Harness — container-less managed agent runtime

A Harness is a managed agent loop: configure a model + system prompt + tools, then invoke with messages.

### Control plane — `bedrock-agentcore-control`
- `CreateHarness`  (required: `harnessName`, `executionRoleArn`)
- `GetHarness` · `ListHarnesses` · `UpdateHarness` · `DeleteHarness`

### Data plane — `bedrock-agentcore`
- `InvokeHarness`  (required: `harnessArn`, `runtimeSessionId`, `messages`) → Converse-style event stream

### Key config
- **model**: `bedrockModelConfig` (modelId, apiFormat=`converse_stream`) | `openAiModelConfig` | `geminiModelConfig` | `liteLlmModelConfig`
- **tools[].type**: `['remote_mcp', 'agentcore_browser', 'agentcore_gateway', 'inline_function', 'agentcore_code_interpreter']`
  - `inline_function` = declared to the agent, **executed by the client** (the model emits `tool_use`, the caller runs it and returns `toolResult`).
- **stream stopReason**: `end_turn` | `tool_use` | `max_tokens` | … (the agent loop continues while `tool_use`)

> Note: Sonnet 4.6 on Bedrock requires the cross-region inference profile `us.anthropic.claude-sonnet-4-6`
> (the bare foundation-model id is not invocable on-demand).
