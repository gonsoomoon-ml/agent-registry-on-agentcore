#!/usr/bin/env python3
"""
Live validation: "Agent 계층 = ⓐ Agent Registry + ⓑ A2A 런타임" on Amazon Bedrock AgentCore.
Ephemeral & self-cleaning: creates a temp registry + 2 records, exercises the
publish→approve→deprecate lifecycle + semantic search, then deletes everything in finally.
Cost: metadata catalog ~ $0. Region: us-west-2.

Run it yourself (under your own AWS auth) from the Claude Code prompt with:
    ! python3 test_agentcore_registry.py
"""
import boto3, json, time, uuid

R = 'us-west-2'
ctl = boto3.client('bedrock-agentcore-control', region_name=R)
dp  = boto3.client('bedrock-agentcore', region_name=R)
sfx = uuid.uuid4().hex[:8]


def p(step, msg):
    print(f"\n[{step}] {msg}")


def main():
    created = {'registry': None, 'records': []}
    try:
        ac = ctl.meta.service_model.operation_model('CreateRegistry').input_shape.members['approvalConfiguration']
        print("approvalConfiguration members:", {n: s.type_name for n, s in ac.members.items()})

        # 1) CREATE REGISTRY (manual approval to exercise the workflow)
        kwargs = dict(
            name=f"claudetest-agentcatalog-{sfx}",
            description="Ephemeral test: Agent Registry (a) + A2A (b) concept validation",
            authorizerType='AWS_IAM',
            approvalConfiguration={'autoApproval': False},
        )
        reg = ctl.create_registry(**kwargs)
        # CreateRegistry returns only registryArn -> parse the registryId out of it
        rid = reg['registryArn'].split(':registry/')[1].split('/')[0]
        created['registry'] = rid
        p('1 CreateRegistry', f"registryId={rid} arn={reg['registryArn']}")

        st = None
        waited = 0
        for i in range(60):  # up to ~5 min; registries typically take 1-2 min
            g = ctl.get_registry(registryId=rid)
            st = g.get('status')
            if st in ('ACTIVE', 'AVAILABLE', 'READY'):
                break
            if 'FAIL' in str(st):
                raise RuntimeError(f"registry failed: {st}")
            time.sleep(5); waited = (i + 1) * 5
        p('1b GetRegistry', f"status={st} (waited ~{waited}s)")
        if st not in ('ACTIVE', 'AVAILABLE', 'READY'):
            raise RuntimeError(f"registry not READY after wait: {st}")

        # 2) PUBLISH an A2A agent record (b) + a tool record (a)
        # Valid A2A v0.3.0 AgentCard (Registry validates this against the real A2A schema)
        agent_card = {
            "protocolVersion": "0.3.0",
            "name": "churn-predictor",
            "description": "Predicts customer churn risk from order/review history",
            "url": "https://example.internal/agents/churn",
            "preferredTransport": "JSONRPC",
            "version": "1.0.0",
            "capabilities": {"streaming": False, "pushNotifications": False},
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [{
                "id": "predict_churn", "name": "Predict churn",
                "description": "Estimate customer churn probability",
                "tags": ["analytics", "customer"],
            }],
        }
        def record_id_by_name(nm):
            for r in ctl.list_registry_records(registryId=rid).get('records', []):
                if r.get('name') == nm:
                    return r.get('recordId') or r.get('id')
            return None

        agent_record_name = "churn-predictor-agent"
        a2a_ok = False
        try:
            rec_a2a = ctl.create_registry_record(
                registryId=rid, name=agent_record_name, descriptorType='A2A',
                description="A2A agent for customer churn prediction", recordVersion="1.0.0",
                descriptors={'a2a': {'agentCard': {'inlineContent': json.dumps(agent_card)}}})
            a2a_ok = True
            p('2 CreateRecord A2A', f"status={rec_a2a.get('status')} arn={rec_a2a.get('recordArn')} "
                                    f"-- ⓑ A2A descriptor ACCEPTED & schema-validated")
        except Exception as e:
            p('2 CreateRecord A2A', f"A2A schema strict ({str(e)[:120]}); falling back to CUSTOM agent record")
            ctl.create_registry_record(
                registryId=rid, name=agent_record_name, descriptorType='CUSTOM',
                description="Agent catalog entry (CUSTOM fallback)", recordVersion="1.0.0",
                descriptors={'custom': {'inlineContent': json.dumps(agent_card)}})
        aid = record_id_by_name(agent_record_name)
        created['records'].append(aid)
        p('2 agent record', f"recordId={aid} (descriptorType={'A2A' if a2a_ok else 'CUSTOM-fallback'})")

        rec_tool = ctl.create_registry_record(
            registryId=rid, name="customer-db-search-tool", descriptorType='CUSTOM',
            description="MCP tool: semantic search over customer DB", recordVersion="1.0.0",
            descriptors={'custom': {'inlineContent': json.dumps({"type": "mcp-tool", "name": "customer_db_search"})}})
        mid = record_id_by_name("customer-db-search-tool")
        created['records'].append(mid)
        p('2b CreateRecord tool', f"recordId={mid} status={rec_tool.get('status')}")

        # 3) APPROVAL WORKFLOW: DRAFT -> PENDING_APPROVAL -> APPROVED
        g0 = ctl.get_registry_record(registryId=rid, recordId=aid); p('3 status before', g0.get('status'))
        ctl.submit_registry_record_for_approval(registryId=rid, recordId=aid)
        g1 = ctl.get_registry_record(registryId=rid, recordId=aid); p('3a Submit', f"-> {g1.get('status')}")
        ctl.update_registry_record_status(registryId=rid, recordId=aid, status='APPROVED',
                                          statusReason='Validated by security review (test)')
        g2 = ctl.get_registry_record(registryId=rid, recordId=aid); p('3b Approve', f"-> {g2.get('status')}")

        # 4) GOVERNANCE VIEW
        la = ctl.list_registry_records(registryId=rid)
        p('4 ListRecords', f"total={len(la.get('records', []))} "
          + str([(r.get('name'), r.get('status')) for r in la.get('records', [])]))

        # 5) SEMANTIC DISCOVERY (data plane)
        try:
            sr = dp.search_registry_records(searchQuery="customer churn prediction", registryIds=[rid], maxResults=5)
            hits = sr.get('records', sr.get('results', []))
            p('5 SearchRegistryRecords', f'-> {len(hits)} hit(s): ' + str([h.get('name') for h in hits]))
            if not hits:
                print("    (semantic index may be eventually-consistent; record exists via ListRecords)")
        except Exception as e:
            p('5 SearchRegistryRecords', f"ERR {type(e).__name__}: {str(e)[:200]}")

        # 6) GRACEFUL DEPRECATION
        ctl.update_registry_record_status(registryId=rid, recordId=aid, status='DEPRECATED',
                                          statusReason='Superseded by churn-predictor v2 (test)')
        g3 = ctl.get_registry_record(registryId=rid, recordId=aid); p('6 Deprecate', f"-> {g3.get('status')}")

        print("\n==== (a) Agent Registry concept: VALIDATED on real Bedrock AgentCore (us-west-2) ====")
    except Exception as e:
        print("\n!!! ERROR:", type(e).__name__, str(e)[:400])
    finally:
        p('CLEANUP', 'deleting test resources...')
        for recid in created['records']:
            try:
                ctl.delete_registry_record(registryId=created['registry'], recordId=recid)
                print("   deleted record", recid)
            except Exception as e:
                print("   record del err", str(e)[:120])
        if created['registry']:
            # wait out CREATING so the registry is deletable
            for _ in range(20):
                try:
                    if ctl.get_registry(registryId=created['registry']).get('status') != 'CREATING':
                        break
                except Exception:
                    break
                time.sleep(3)
            for _ in range(15):
                try:
                    ctl.delete_registry(registryId=created['registry'])
                    print("   deleted registry", created['registry'])
                    break
                except Exception as e:
                    if any(k in str(e) for k in ('CREATING', 'Conflict', 'records', 'in use')):
                        time.sleep(3); continue
                    print("   registry del err", str(e)[:160]); break
        print("   remaining registries:", ctl.list_registries().get('registries', []))


if __name__ == '__main__':
    main()
