"""Application-owned conversation, versioned goals and isolated worker status.

Model checkpoints are a per-turn execution cache, never the source of task truth.
"""
import copy
import json
import time

from .store import uid


TERMINAL_WORKER = {'completed', 'succeeded', 'failed', 'cancelled', 'interrupted', 'skipped'}


class TaskCoordinator:
    def __init__(self, store):
        self.store = store

    def active(self, sid, project_id=None):
        session = self.store.get('session', sid)
        tid = session.get('focus_task_id')
        if not tid:
            return None
        task = self.store.get('task', tid)
        if (task['session_id'] != sid or task.get('project_id') != project_id or
            task['status'] in {'cancelled', 'superseded', 'interrupted'}):
            return None
        return task

    def snapshot(self, task):
        return {k: copy.deepcopy(task[k]) for k in ('id', 'revision', 'objective', 'mode', 'constraints',
            'original_request', 'source_message_ids', 'parameters', 'pending_question', 'status')}

    def context(self, item):
        task = self.active(item['session_id'], item.get('project_id'))
        if not task:
            return None
        result = self.snapshot(task)
        result['workers'] = [w for w in self.store.all('worker') if w.get('task_id') == task['id']][-6:]
        return result

    def read_turns(self, sid, message_ids, offset=0):
        if not 1 <= len(message_ids) <= 4 or offset < 0:
            raise ValueError('每次读取1至4条会话消息，分页位置不得为负')
        result = []
        for mid in message_ids:
            message = self.store.get('message', mid)
            if message['session_id'] != sid or message.get('notification'):
                raise ValueError('只能读取本会话的用户问答')
            text = json.dumps({'user': message.get('content'), 'assistant': message.get('response')}, ensure_ascii=False)
            result.append({'message_id': mid, 'text': text[offset:offset+2500],
                'next_offset': offset+2500 if len(text) > offset+2500 else None})
        return {'turns': result}

    def bind(self, item, intent, workflow):
        """One model decision becomes an atomic, source-linked goal revision."""
        with self.store.lock:
            session = self.store.get('session', item['session_id'])
            previous = self.active(item['session_id'], item.get('project_id'))
            continuing = intent.continue_task and previous is not None
            if intent.continue_task and not previous:
                raise ValueError('原任务已失效，请明确本轮完整需求')
            if continuing and previous['mode'] == 'read_only' and intent.action in {'plan', 'export', 'display', 'cancel'}:
                raise ValueError('上一任务仅讨论；补充回答不提供执行授权。需要运行时请明确提出新的执行请求。')
            # A conversation about a running worker is a separate question; it
            # must not revise that worker's already-submitted task contract.
            if continuing and previous['status'] in {'running', 'reviewing'}:
                continuing = False
            mode = ('execute' if intent.action in {'plan', 'export', 'display', 'cancel'} else
                    'read_only' if intent.action in {'query', 'discuss', 'compare'} else intent.purpose)
            if continuing:
                task = copy.deepcopy(previous)
                task['revision'] += 1
                if mode != 'unknown':
                    task['mode'] = mode
            else:
                task = {'id': uid('task'), 'session_id': item['session_id'], 'project_id': item.get('project_id'),
                    'revision': 1, 'mode': mode, 'original_request': item['content'],
                    'source_message_ids': [], 'constraints': [], 'parameters': {}, 'created': time.time()}
            task['source_message_ids'].append(item['id'])
            task['constraints'] = list(dict.fromkeys(task['constraints'] + intent.constraints))
            task['parameters'].update(intent.parameter_overrides.model_dump(exclude_unset=True))
            if intent.action == 'clarify':
                # Do not promote an earlier ambiguous guess into a resolved slot.
                for key in intent.unresolved_fields:
                    task['parameters'].pop(key, None)
            task.update(objective=intent.request_summary or item['content'], current_message_id=item['id'],
                workflow_id=workflow['id'], pending_question=intent.question if intent.action == 'clarify' else '',
                status='waiting_input' if intent.action == 'clarify' else 'answering' if mode == 'read_only' else 'validating',
                updated=time.time())
            session['focus_task_id'] = task['id']
            workflow.update(task_id=task['id'], task_revision=task['revision'], task_context=self.snapshot(task))
            item.update(task_id=task['id'], task_revision=task['revision'])
            objects = [('session', session), ('task', task), ('message', item), ('workflow', workflow)]
            if previous and not continuing and previous['status'] == 'waiting_input' and previous['mode'] == 'read_only':
                previous.update(status='superseded', updated=time.time())
                objects.append(('task', previous))
            self.store.publish_objects(objects, item['session_id'], 'task', task=self.snapshot(task))
            return task

    def sync_workflow(self, workflow):
        if not workflow.get('task_id'):
            return
        with self.store.lock:
            task = self.store.get('task', workflow['task_id'])
            if task['revision'] != workflow['task_revision'] or task['workflow_id'] != workflow['id']:
                return  # A late worker result is history, not a new user goal.
            if task['status'] in {'cancelled', 'interrupted', 'superseded'}:
                return
            action = workflow.get('intent', {}).get('action')
            if action in {'query', 'discuss', 'clarify'} and workflow['status'] not in {'failed', 'cancelled', 'interrupted'}:
                return  # The answer/clarification, not routing, completes this turn.
            state = {'submitted': 'running', 'waiting_choice': 'waiting_input', 'blocked': 'waiting_input',
                     'completed': 'completed'}.get(workflow['status'], workflow['status'])
            task.update(status=state, updated=time.time())
            if state == 'waiting_input':
                task['pending_question'] = workflow.get('detail', '')
            self.store.publish_objects([('task', task)], task['session_id'], 'task', task=self.snapshot(task))

    def finish_turn(self, item):
        if not item.get('task_id'):
            return
        with self.store.lock:
            task = self.store.get('task', item['task_id'])
            if task['revision'] != item['task_revision'] or task['current_message_id'] != item['id']:
                return
            if task['status'] == 'answering':
                task.update(status='answered' if item['status'] == 'succeeded' else item['status'], updated=time.time())
                self.store.publish_objects([('task', task)], item['session_id'], 'task', task=self.snapshot(task))

    def worker(self, actor, record, workflow, **facts):
        """A structured mailbox: identity and parent revision precede results."""
        key = actor + ':' + record['id']
        with self.store.lock:
            old = next((w for w in self.store.all('worker') if w['id'] == key), None)
            if old and old['status'] in TERMINAL_WORKER and old['status'] != record['status']:
                return old  # Ignore delayed progress or duplicate terminal changes.
            worker = {**(old or {}), 'id': key, 'actor': actor, 'session_id': record['session_id'],
                'workflow_id': workflow['id'], 'task_id': workflow.get('task_id'),
                'task_revision': workflow.get('task_revision'), 'status': record['status'],
                'run_id': record.get('run_id', record['id']), **facts}
            if old and all(old.get(k) == v for k, v in worker.items()):
                return old
            worker['updated'] = time.time()
            self.store.publish_objects([('worker', worker)], record['session_id'], 'worker', worker=worker)
            return worker

    def recover(self):
        for kind, active_states in [('task', {'answering', 'validating', 'running', 'reviewing'}),
                                    ('worker', {'queued', 'running'})]:
            for obj in self.store.all(kind):
                if obj['status'] in active_states:
                    obj.update(status='interrupted', updated=time.time())
                    self.store.put(kind, obj)

    def conversation(self, item):
        """Both model stages read the same durable user/assistant turns.

        Clarification and workflow replies bypass the Q&A model, so its tool
        checkpoint is not a complete conversation history.
        """
        messages = self.store.all('message')
        # INSERT OR REPLACE changes rowid when an older reply is updated.
        # Creation time, rather than last write order, defines conversation order.
        if all(m.get('created') is not None for m in messages):
            messages.sort(key=lambda m: m['created'])
        position = next((i for i, m in enumerate(messages) if m['id'] == item['id']), len(messages))
        workflows = {w['id']: w for w in self.store.all('workflow') if w['session_id'] == item['session_id']}
        turns = []
        for i, message in enumerate(messages):
            if (message['id'] == item['id'] or message['session_id'] != item['session_id'] or
                message.get('notification') or message.get('status') in {'queued', 'running'} or
                not message.get('content') or not message.get('response')):
                continue
            if item.get('project_id') and message.get('project_id') != item['project_id']:
                continue
            if item.get('created') is not None and message.get('created') is not None:
                if message['created'] > item['created']:
                    continue
            elif i >= position:
                continue
            workflow = workflows.get(message.get('workflow_id'), {})
            answer = message['response']
            turns.append({'message_id': message['id'], 'user': message['content'],
                'assistant': answer[:3000] + ('\n[较早回复过长，已省略后文]' if len(answer) > 3000 else ''),
                'action': workflow.get('intent', {}).get('action'), 'workflow_status': workflow.get('status')})
        # Protect the original question and every answer in an unfinished
        # clarification chain from ordinary history budget trimming.
        pending = []
        for turn in reversed(turns):
            if turn['action'] != 'clarify' or turn['workflow_status'] != 'blocked':
                break
            pending.insert(0, turn['message_id'])
        turns = turns[-max(8, len(pending)):]
        while len(json.dumps(turns, ensure_ascii=False)) > 16000 and len(turns) > max(1, len(pending)):
            turns.pop(0)
        return {'recent_dialogue': turns, 'pending_clarification': pending}
