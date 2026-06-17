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
