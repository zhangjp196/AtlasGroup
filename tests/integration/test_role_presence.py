from services.agent_zero.stores import zero_store
from services.agent_zero.task_layer import conversation


def test_role_presence_is_shared_by_api_and_agent_tool(client, app_env):
    api = '/api/agent-zero'
    response = client.post(f'{api}/roles', json={'name': 'Presence test'})
    assert response.status_code == 200
    role = response.json()['data']
    assert role['status'] == 'online'

    updated = client.patch(f"{api}/roles/{role['id']}", json={'status': 'offline'})
    assert updated.json()['data']['status'] == 'offline'
    # Changing capabilities must not reset presence.
    updated = client.patch(f"{api}/roles/{role['id']}", json={'tools': ['web']})
    assert updated.json()['data']['status'] == 'offline'

    result = conversation._exec_role_manage({'id': 'global'}, {
        'action': 'update', 'role_id': role['id'], 'status': 'online',
    })
    assert result['role']['status'] == 'online'
    assert zero_store.get_role(role['id'])['envelope']['status'] == 'online'
    assert client.patch(f"{api}/roles/{role['id']}", json={'status': 'invalid'}).status_code == 422
