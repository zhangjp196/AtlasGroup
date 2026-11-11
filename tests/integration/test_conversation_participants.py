API = '/api/agent-zero'


def test_participants_are_conversation_local_and_temporary_roles_are_scoped(client):
    role = client.post(f'{API}/roles', json={'name': 'Member'}).json()['data']
    first = client.post(f'{API}/conversations', json={}).json()['data']
    second = client.post(f'{API}/conversations', json={}).json()['data']
    temp = client.post(f'{API}/roles', json={
        'name': 'Temporary', 'scope': 'temporary', 'session_id': first['id'],
    }).json()['data']
    path = f"{API}/conversations/{first['id']}/participants"
    response = client.put(path, json={'role_ids': [role['id'], temp['id']]})
    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert {m['id'] for m in data['team']['members']} == {role['id'], temp['id']}
    assert not client.get(f"{API}/conversations/{second['id']}").json()['data']['team_id']
    assert client.put(f"{API}/conversations/{second['id']}/participants", json={
        'role_ids': [temp['id']],
    }).status_code == 400
    updated = client.put(path, json={'role_ids': [role['id']]}).json()['data']
    assert updated['team']['id'] == data['team']['id']
    assert [m['id'] for m in updated['team']['members']] == [role['id']]


def test_global_chat_is_separate_and_restored(client):
    management = client.post(f'{API}/app/global-agent').json()['data']
    chat = client.post(f'{API}/app/global-chat').json()['data']
    assert chat['id'] != management['id']
    assert chat['zone'] == 'global-chat'
    assert client.post(f'{API}/app/global-chat').json()['data']['id'] == chat['id']
    role = client.post(f'{API}/roles', json={'name': 'Global participant'}).json()['data']
    result = client.put(f"{API}/conversations/{chat['id']}/participants", json={'role_ids': [role['id']]})
    assert result.status_code == 200, result.text
    task = client.post(f"{API}/conversations/{chat['id']}/team-tasks", json={
        'goal': '检查任务派发', 'member_id': role['id'],
    })
    assert task.status_code == 200, task.text
    assert task.json()['data']['members'][0]['role_id'] == role['id']


def test_changing_participants_does_not_mutate_shared_team(client):
    team = client.post(f'{API}/teams', json={
        'name': 'Shared', 'leader': {'name': 'Leader'}, 'members': [{'name': 'Original'}],
    }).json()['data']
    first = client.post(f'{API}/conversations', json={'team_id': team['id']}).json()['data']
    second = client.post(f'{API}/conversations', json={'team_id': team['id']}).json()['data']
    role = client.post(f'{API}/roles', json={'name': 'New member'}).json()['data']
    updated = client.put(f"{API}/conversations/{first['id']}/participants", json={
        'role_ids': [role['id']],
    }).json()['data']
    assert updated['team']['id'] != team['id']
    assert client.get(f"{API}/conversations/{second['id']}").json()['data']['team_id'] == team['id']
    original = client.get(f"{API}/teams/{team['id']}").json()['data']
    assert original['members'][0]['name'] == 'Original'
