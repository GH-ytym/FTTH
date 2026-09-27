import asyncio
import pytest
from pydantic import ValidationError
from backend.result_summary import summarize
from backend.review import ReviewResult, structure_feedback
from backend.models import RunInput, MessageInput
from test_review import fixture_run, ReviewModel


def test_warning_and_service_failure_are_different():
    run = {'id': 'run_A', 'created': 1, 'status': 'succeeded', 'kind': 'planning'}
    findings = [{'severity': s, 'description': str(i)} for i, s in enumerate(
        ['warning'] * 4 + ['info'] * 3)]
    text = summarize(run, {'status': 'completed', 'result': {'verdict': 'needs_review', 'findings': findings}})
    assert '审查完成 · 有待复核事项' in text and '0 项错误' in text
    assert '另有 1 项警告' in text and '另有 1 项提醒' in text
    failed = summarize(run, {'status': 'failed', 'error_message': '结构错误',
                            'evidence': {'deterministic_warning': True}})
    assert '规划计算已完成' in failed and '审查暂未完成：结构错误' in failed
    assert '程序校验：存在警告' in failed and 'run_A' in failed


def test_specific_structure_feedback_is_safe():
    try:
        ReviewResult.model_validate({'verdict': 'pass', 'summary': 'ok', 'category': 'SECRET'})
    except ValidationError as exc:
        text = structure_feedback(exc)
    assert 'category: extra_forbidden' in text and 'findings' in text and 'SECRET' not in text


def test_reviewer_never_creates_another_run(tmp_path):
    async def scenario():
        service, sid, _, run, _ = fixture_run(tmp_path)
        service.reviewer.model = ReviewModel()
        review = service.reviewer.enqueue(sid, run['id'], 'test', automatic=True)
        await service.reviewer.execute(review)
        assert service.store.get('review', review['id'])['status'] == 'completed'
        assert len(service.store.all('run')) == 1
        assert not hasattr(service, 'submit_repair')
        await service.close()
    asyncio.run(scenario())
    for model, payload in [(RunInput, {'request_id': 'x'}), (MessageInput, {'request_id': 'x', 'content': 'run'})]:
        with pytest.raises(ValidationError):
            model(**payload, allow_auto_repair=True)
