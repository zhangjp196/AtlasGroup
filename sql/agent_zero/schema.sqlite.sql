-- Agent Zero sqlite 表结构（由 services/agent_zero/models/zero.py 导出，参考用）。
-- 服务启动时 init_database() 会按 ORM 模型幂等建表，并给旧库做只加列的增量迁移；本文件无需手工执行。

CREATE TABLE workspaces (
	id TEXT NOT NULL, 
	kind TEXT DEFAULT 'group' NOT NULL,
	name TEXT NOT NULL, 
	member_role_ids JSON, 
	zones JSON, 
	origin JSON, 
	host_role_id TEXT, 
	root_path TEXT, 
	archived BOOLEAN DEFAULT 0 NOT NULL, 
	created_by TEXT NOT NULL, 
	created_at DATETIME DEFAULT (datetime('now','localtime')) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_workspaces_kind CHECK (kind IN ('task','group','role'))
);

CREATE TABLE roles (
	id TEXT NOT NULL, 
	group_id TEXT, 
	name TEXT, 
	persona TEXT, 
	expertise TEXT, 
	avatar TEXT,
	envelope JSON NOT NULL, 
	created_at DATETIME DEFAULT (datetime('now','localtime')), 
	PRIMARY KEY (id)
);

CREATE TABLE gdag_nodes (
	id TEXT NOT NULL, 
	type TEXT NOT NULL, 
	camp TEXT NOT NULL, 
	props JSON NOT NULL, 
	confidence FLOAT, 
	sources JSON, 
	created_at DATETIME DEFAULT (datetime('now','localtime')) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_gdag_nodes_type CHECK (type IN ('entity','fact','relation','task','step','artifact','experience','tool_call'))
);

CREATE TABLE audit_logs (
	id INTEGER NOT NULL, 
	actor TEXT NOT NULL, 
	action TEXT NOT NULL, 
	args JSON, 
	result JSON, 
	cost JSON, 
	ts DATETIME DEFAULT (datetime('now','localtime')) NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE app_settings (
	"key" TEXT NOT NULL, 
	value JSON NOT NULL, 
	updated_at DATETIME DEFAULT (datetime('now','localtime')) NOT NULL, 
	PRIMARY KEY ("key")
);

CREATE TABLE skills (
	id TEXT NOT NULL, 
	name TEXT NOT NULL, 
	description TEXT NOT NULL, 
	path TEXT NOT NULL, 
	source TEXT NOT NULL, 
	enabled BOOLEAN DEFAULT 1 NOT NULL, 
	created_at DATETIME DEFAULT (datetime('now','localtime')) NOT NULL, 
	updated_at DATETIME DEFAULT (datetime('now','localtime')) NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE agent_teams (
	id TEXT NOT NULL, 
	workspace_id TEXT NOT NULL, 
	name TEXT NOT NULL, 
	avatar TEXT,
	description TEXT, 
	kind TEXT DEFAULT 'group' NOT NULL,
	mode TEXT DEFAULT 'orchestrate' NOT NULL,
	max_speakers INTEGER DEFAULT 4 NOT NULL,
	leader_role_id TEXT, 
	member_role_ids JSON, 
	created_at DATETIME DEFAULT (datetime('now','localtime')) NOT NULL, 
	updated_at DATETIME DEFAULT (datetime('now','localtime')) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE,
	CONSTRAINT ck_agent_teams_kind CHECK (kind IN ('group', 'participants')),
	CONSTRAINT ck_agent_teams_mode CHECK (mode IN ('orchestrate', 'roundtable', 'single'))
);

CREATE TABLE conversations (
	id TEXT NOT NULL, 
	workspace_id TEXT NOT NULL, 
	role_id TEXT, 
	zone TEXT, 
	source TEXT DEFAULT 'chat' NOT NULL, 
	permission_mode TEXT DEFAULT 'once' NOT NULL, 
	run_mode TEXT DEFAULT 'autonomous' NOT NULL, 
	model_code TEXT, 
	thinking_level TEXT, 
	title TEXT, 
	title_source TEXT DEFAULT '' NOT NULL, 
	archived BOOLEAN DEFAULT 0 NOT NULL, 
	team_id TEXT, 
	forked_from_id TEXT, 
	forked_from_message_id TEXT, 
	created_by TEXT NOT NULL, 
	created_at DATETIME DEFAULT (datetime('now','localtime')) NOT NULL, 
	updated_at DATETIME DEFAULT (datetime('now','localtime')) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
);

CREATE TABLE conversation_bots (
	id TEXT NOT NULL PRIMARY KEY,
	conversation_id TEXT NOT NULL,
	provider TEXT NOT NULL,
	bot_id TEXT NOT NULL,
	secret TEXT NOT NULL,
	peer_id TEXT NOT NULL
);
CREATE UNIQUE INDEX uq_conversation_bots_account ON conversation_bots (provider, bot_id);
CREATE INDEX ix_conversation_bots_conversation_id ON conversation_bots (conversation_id);

CREATE TABLE bot_routes (
	id TEXT NOT NULL PRIMARY KEY,
	bot_id TEXT NOT NULL REFERENCES conversation_bots (id) ON DELETE CASCADE,
	conversation_id TEXT NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
	peer_id TEXT NOT NULL,
	created_at DATETIME DEFAULT (datetime('now','localtime')) NOT NULL
);
CREATE UNIQUE INDEX uq_bot_routes_bot ON bot_routes (bot_id);
CREATE INDEX ix_bot_routes_conversation_id ON bot_routes (conversation_id);

CREATE TABLE gdag_edges (
	from_id TEXT NOT NULL, 
	to_id TEXT NOT NULL, 
	kind TEXT NOT NULL, 
	weight FLOAT, 
	exec_state TEXT, 
	PRIMARY KEY (from_id, to_id, kind), 
	CONSTRAINT ck_gdag_edges_exec_state CHECK (exec_state IN ('pending','running','done','failed') OR exec_state IS NULL), 
	FOREIGN KEY(from_id) REFERENCES gdag_nodes (id), 
	FOREIGN KEY(to_id) REFERENCES gdag_nodes (id)
);

CREATE TABLE conversation_runs (
	id TEXT NOT NULL, 
	conversation_id TEXT NOT NULL, 
	role_id TEXT, 
	status TEXT NOT NULL, 
	mode TEXT DEFAULT 'chat' NOT NULL, 
	auto_execute BOOLEAN DEFAULT 0 NOT NULL, 
	error TEXT, 
	content TEXT, 
	mentions JSON, 
	cursor INTEGER NOT NULL, 
	queue_pos INTEGER DEFAULT 0 NOT NULL, 
	queued_msg_id TEXT, 
	thinking_level TEXT, 
	stop_requested BOOLEAN DEFAULT 0 NOT NULL, 
	stop_silent BOOLEAN DEFAULT 0 NOT NULL, 
	created_by TEXT NOT NULL, 
	created_at DATETIME DEFAULT (datetime('now','localtime')) NOT NULL, 
	updated_at DATETIME DEFAULT (datetime('now','localtime')) NOT NULL, 
	started_at DATETIME, 
	finished_at DATETIME, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_conversation_runs_status CHECK (status IN ('queued','running','waiting','done','failed','stopped')), 
	FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE
);

CREATE TABLE tool_approvals (
	id TEXT NOT NULL, 
	conversation_id TEXT NOT NULL, 
	run_id TEXT, 
	tool TEXT NOT NULL, 
	args JSON, 
	risk TEXT NOT NULL, 
	reasons JSON, 
	note TEXT, 
	status TEXT DEFAULT 'pending' NOT NULL, 
	scope TEXT, 
	role_id TEXT, 
	team_id TEXT, 
	created_at DATETIME DEFAULT (datetime('now','localtime')) NOT NULL, 
	decided_at DATETIME, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_tool_approvals_status CHECK (status IN ('pending','approved','denied','expired')), 
	FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE
);

CREATE TABLE permission_grants (
	id TEXT NOT NULL, 
	conversation_id TEXT NOT NULL, 
	tool TEXT NOT NULL, 
	created_at DATETIME DEFAULT (datetime('now','localtime')) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE
);

CREATE TABLE permission_scope_grants (
	id TEXT NOT NULL, 
	scope TEXT NOT NULL, 
	role_id TEXT, 
	team_id TEXT, 
	tool TEXT NOT NULL, 
	created_by TEXT DEFAULT 'local' NOT NULL, 
	created_at DATETIME DEFAULT (datetime('now','localtime')) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_permission_scope_grants_scope CHECK (scope IN ('role','team'))
);

CREATE INDEX idx_scope_grants_role ON permission_scope_grants (role_id, tool);

CREATE INDEX idx_scope_grants_team ON permission_scope_grants (team_id, tool);

CREATE TABLE todo_items (
	id TEXT NOT NULL, 
	conversation_id TEXT NOT NULL, 
	title TEXT NOT NULL, 
	status TEXT DEFAULT 'open' NOT NULL, 
	source TEXT DEFAULT 'user' NOT NULL, 
	position INTEGER DEFAULT 0 NOT NULL, 
	note TEXT, 
	created_at DATETIME DEFAULT (datetime('now','localtime')) NOT NULL, 
	updated_at DATETIME DEFAULT (datetime('now','localtime')) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_todo_items_status CHECK (status IN ('open','doing','done')), 
	FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE
);

CREATE TABLE goals (
	id TEXT NOT NULL, 
	conversation_id TEXT NOT NULL, 
	objective TEXT NOT NULL, 
	status TEXT DEFAULT 'active' NOT NULL, 
	token_budget INTEGER DEFAULT 0 NOT NULL, 
	max_runs INTEGER DEFAULT 0 NOT NULL, 
	tokens_used INTEGER DEFAULT 0 NOT NULL, 
	runs_used INTEGER DEFAULT 0 NOT NULL, 
	note TEXT, 
	created_at DATETIME DEFAULT (datetime('now','localtime')) NOT NULL, 
	updated_at DATETIME DEFAULT (datetime('now','localtime')) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_goals_status CHECK (status IN ('active','achieved','closed','exhausted')), 
	FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE
);

CREATE TABLE conversation_run_events (
	run_id TEXT NOT NULL, 
	seq INTEGER NOT NULL, 
	kind TEXT NOT NULL, 
	data JSON NOT NULL, 
	created_at DATETIME DEFAULT (datetime('now','localtime')) NOT NULL, 
	PRIMARY KEY (run_id, seq), 
	FOREIGN KEY(run_id) REFERENCES conversation_runs (id) ON DELETE CASCADE
);

CREATE TABLE scheduled_tasks (
	id TEXT NOT NULL, 
	title TEXT NOT NULL, 
	prompt TEXT NOT NULL, 
	workspace_id TEXT, 
	role_id TEXT, 
	conversation_id TEXT, 
	trigger_mode TEXT DEFAULT 'scheduled' NOT NULL, 
	schedule_kind TEXT DEFAULT 'daily' NOT NULL, 
	schedule JSON NOT NULL, 
	condition TEXT DEFAULT '' NOT NULL, 
	status TEXT DEFAULT 'active' NOT NULL, 
	run_mode TEXT DEFAULT 'chat' NOT NULL, 
	auto_execute BOOLEAN DEFAULT 0 NOT NULL, 
	max_runs INTEGER DEFAULT 0 NOT NULL, 
	run_count INTEGER DEFAULT 0 NOT NULL, 
	last_run_at DATETIME, 
	last_status TEXT, 
	last_run_id TEXT, 
	last_conversation_id TEXT, 
	last_result TEXT, 
	next_run_at DATETIME, 
	meta JSON NOT NULL, 
	created_at DATETIME DEFAULT (datetime('now','localtime')) NOT NULL, 
	updated_at DATETIME DEFAULT (datetime('now','localtime')) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_scheduled_tasks_trigger CHECK (trigger_mode IN ('scheduled','smart','manual')), 
	CONSTRAINT ck_scheduled_tasks_kind CHECK (schedule_kind IN ('once','interval','daily','weekly','smart')), 
	CONSTRAINT ck_scheduled_tasks_status CHECK (status IN ('active','paused','done'))
);

CREATE INDEX idx_scheduled_tasks_due ON scheduled_tasks (status, next_run_at);

CREATE INDEX idx_scheduled_tasks_conv ON scheduled_tasks (last_conversation_id);
