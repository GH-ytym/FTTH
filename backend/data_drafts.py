"""Versioned per-session inputs; running jobs keep immutable project snapshots."""
import asyncio
import copy
import time

from .models import ProjectInput
from .store import uid


class DraftConflict(ValueError):
    pass


class DataDrafts:
    def data_draft(self, sid):
        with self.store.lock:
            session = self.store.get('session', sid)
            if 'data_draft' not in session:
                view = self.inspect(sid) if session.get('project_id') else {}
                session['data_draft'] = {'revision': 0, 'sources': view.get('inputs', {}),
                    'recognition_path': view.get('recognition_path'), 'status': 'saved',
                    'snapshot_revision': 0 if session.get('project_id') else None,
                    'project_id': session.get('project_id'), 'updated': time.time()}
                self.store.put('session', session)
            return copy.deepcopy(session['data_draft'])

    def save_data_draft(self, sid, spec):
        with self.store.lock:
            current = self.data_draft(sid)
            if spec.revision != current['revision']:
                raise DraftConflict('数据版本已变化，请重新加载后保存；未覆盖其他修改。')
            sources = {k: v.model_dump() for k, v in spec.sources.items() if v.path.strip()}
            current.update(revision=current['revision'] + 1, sources=sources,
                           recognition_path=spec.recognition_path or None, updated=time.time(), status='saved')
            session = self.store.get('session', sid)
            session['data_draft'] = current
            self.store.put('session', session)
            return copy.deepcopy(current)

    async def prepare_data(self, sid, expected_revision=None):
        if not hasattr(self, '_data_locks'):
            self._data_locks = {}
        async with self._data_locks.setdefault(sid, asyncio.Lock()):
            draft = self.data_draft(sid)
            if expected_revision is not None and draft['revision'] != expected_revision:
                raise DraftConflict('发送后数据已改变，请按当前数据重新提出检查或规划。')
            if draft['project_id'] and draft.get('snapshot_revision') == draft['revision']:
                return self.store.get('project', draft['project_id'])
            project = await self.register(ProjectInput(sources=draft['sources'], recognition_path=draft['recognition_path']))
            with self.store.lock:
                current = self.data_draft(sid)
                if current['revision'] != draft['revision']:
                    raise DraftConflict('登记期间数据已改变，本次未采用过期数据；请重新检查或规划。')
                session = self.store.get('session', sid)
                current.update(project_id=project['id'], snapshot_revision=draft['revision'])
                session.update(project_id=project['id'], data_draft=current)
                self.store.put('session', session)
            return project

    async def check_data(self, sid, request=None, expected_revision=None):
        record = {'id': uid('check'), 'session_id': sid, 'created': time.time(),
                  'data_revision': self.data_draft(sid)['revision'], 'status': 'failed'}
        try:
            project = await self.prepare_data(sid, expected_revision)
            record['project_id'] = project['id']
            preflight = await self.ensure_input_preflight(project)
            result = self.validate(sid, request) if request else self.inspect(sid)
            if not request:
                result.update(valid=preflight['status'] != 'FAIL', rule_preflight=preflight)
            record.update(status='succeeded' if result.get('valid', True) else 'failed', result=result)
        except (ValueError, OSError) as exc:
            record['result'] = {'valid': False, 'error': str(exc)}
        self.store.publish_objects([('check', record)], sid, 'check', check=record)
        return {**record['result'], 'check_id': record['id']}

    def owned_check(self, sid, cid):
        record = self.store.get('check', cid)
        if record['session_id'] != sid:
            raise ValueError('检查记录不属于当前会话')
        return record
