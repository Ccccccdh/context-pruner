"""第一阶段：生命周期闭环、来源映射与选择性恢复测试。"""

import json
import tempfile
import unittest
from pathlib import Path

from agent_demo import KnowledgeBase, ReActAgent, Task
from agent_demo.agent import _parse_output
from agent_demo.history import HistoryManager
from agent_demo.llm import LLMClient
from agent_demo.logger import write_run
from context_pruner import (
    CompressionAction,
    ArchivePolicy,
    BudgetPressure,
    ContextBudget,
    ContextChunk,
    ContextEventKind,
    ContextLifecycleManager,
    ContextPrunerV1,
    ContextStatus,
    ContextType,
    SQLiteArchiveStore,
    InMemoryArchiveStore,
)
from context_pruner.compressors import merge, truncate
from context_pruner.evaluator_v1 import _percentile_scale


class OutputParserTest(unittest.TestCase):
    def test_multiple_json_actions_use_first_complete_object(self):
        parsed = _parse_output(
            '{"action":{"name":"read","args":{"doc_id":"d1"}}}\n'
            '{"action":{"name":"read","args":{"doc_id":"d2"}}}'
        )

        self.assertEqual("d1", parsed["action"]["args"]["doc_id"])

    def test_fenced_json_is_supported(self):
        parsed = _parse_output(
            '```json\n{"action":{"name":"search","args":{"keyword":"Atlas"}}}\n```'
        )

        self.assertEqual("search", parsed["action"]["name"])


class DataAndActionTest(unittest.TestCase):
    def test_event_ids_are_stable_and_provenance_is_preserved(self):
        manager = ContextLifecycleManager(task_state="记住项目代号")
        manager.observe_turn(
            {"role": "user", "content": "项目代号是蓝鲸"},
            kind=ContextEventKind.MODEL_OUTPUT,
        )
        first_id = manager.context_events[0].event_id
        manager.observe_turn({"role": "assistant", "content": "已经记录"})
        self.assertEqual(manager.chunks[0].chunk_id, first_id)
        self.assertEqual(manager.chunks[0].source_event_ids, (first_id,))

    def test_truncate_does_not_mutate_original(self):
        original = ContextChunk(
            "evt_a",
            ContextType.DIALOGUE,
            "这是一段很长的上下文。" * 20,
            source_event_ids=("evt_a",),
        )
        shortened = truncate(original, keep_ratio=0.2)
        self.assertNotEqual(shortened.text, original.text)
        self.assertNotIn("[已裁剪]", original.text)
        self.assertEqual(shortened.source_event_ids, ("evt_a",))

    def test_merge_deduplicates_instead_of_concatenating(self):
        first = ContextChunk(
            "evt_a",
            ContextType.DIALOGUE,
            "完全相同的事实",
            role="assistant",
            source_event_ids=("evt_a",),
        )
        second = ContextChunk(
            "evt_b",
            ContextType.DIALOGUE,
            "完全相同的事实",
            role="assistant",
            source_event_ids=("evt_b",),
        )
        result = merge([first, second])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].text, "完全相同的事实")
        self.assertEqual(result[0].source_event_ids, ("evt_a", "evt_b"))
        self.assertEqual(result[0].action, CompressionAction.MERGE)

    def test_equal_scores_receive_equal_percentiles(self):
        self.assertEqual(_percentile_scale([0.2, 0.2, 0.8]), [0.25, 0.25, 1.0])

    def test_budget_reserves_fixed_prompt_tokens(self):
        managed = ContextBudget(800, 1000, 700).reserve(250)

        self.assertEqual(550, managed.soft_limit_tokens)
        self.assertEqual(750, managed.hard_limit_tokens)
        self.assertEqual(450, managed.target_tokens)


class BudgetPolicyTest(unittest.TestCase):
    def test_hard_pressure_archives_low_value_history_and_keeps_constraint(self):
        manager = ContextLifecycleManager(
            task_state="保留硬约束",
            pruner=ContextPrunerV1(),
        )
        manager.observe_turn(
            {"role": "user", "content": "硬约束：必须保留项目代号 Aurora-17，不得删除。"}
        )
        for index in range(8):
            manager.observe_turn(
                {"role": "assistant", "content": f"普通重复分析 {index}。" * 30}
            )

        snapshot = manager.compress(budget=ContextBudget(180, 240, 160))
        visible = "\n".join(chunk.text for chunk in snapshot.chunks)

        self.assertEqual(BudgetPressure.HARD, snapshot.budget_pressure)
        self.assertLessEqual(snapshot.tokens, 240)
        self.assertIn("Aurora-17", visible)
        self.assertGreater(snapshot.archived_items, 0)


class SQLiteArchiveStoreTest(unittest.TestCase):
    def test_archive_survives_reopen_and_preserves_provenance(self):
        item = ContextChunk(
            "evt_sqlite",
            ContextType.TOOL_INTERACTION,
            "观察：read 结果：目标集群是 cluster-zeta。",
            role="user",
            source_event_ids=("evt_sqlite",),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "archive.sqlite3"
            with SQLiteArchiveStore(path, namespace="session-a") as store:
                entry = store.put(item, turn=3, reason="archive")
                self.assertEqual("arc:evt_sqlite", entry.archive_id)

            with SQLiteArchiveStore(path, namespace="session-a") as reopened:
                found = reopened.search("cluster-zeta", limit=1)
                self.assertEqual(1, len(found))
                self.assertEqual(("evt_sqlite",), found[0].item.source_event_ids)
                self.assertTrue(found[0].item.metadata["archive_uri"].startswith("sqlite://"))

            with SQLiteArchiveStore(path, namespace="session-b") as isolated:
                self.assertEqual(0, len(isolated))

    def test_archive_redacts_secrets_by_default(self):
        item = ContextChunk(
            "evt_secret",
            ContextType.DIALOGUE,
            "api_key=sk-test-secret-12345678 password=hunter2026",
        )
        store = InMemoryArchiveStore()

        entry = store.put(item, turn=1, reason="security-test")

        self.assertNotIn("hunter2026", entry.item.text)
        self.assertNotIn("sk-test-secret", entry.item.text)
        self.assertTrue(entry.item.metadata["redacted"])

    def test_archive_retention_keeps_only_newest_entries(self):
        store = InMemoryArchiveStore(policy=ArchivePolicy(max_entries=2))
        for turn in range(4):
            store.put(
                ContextChunk(f"evt_{turn}", ContextType.DIALOGUE, f"记录 {turn}"),
                turn=turn,
                reason="retention-test",
            )

        self.assertEqual(2, len(store))
        self.assertEqual(
            {"arc:evt_2", "arc:evt_3"},
            {entry.archive_id for entry in store.entries()},
        )


class LifecyclePersistenceTest(unittest.TestCase):
    def test_state_round_trip_can_continue_with_unique_event_ids(self):
        manager = ContextLifecycleManager(
            task_state="记住 Aurora-17",
            pruner=ContextPrunerV1(),
        )
        manager.observe_turn({"role": "user", "content": "项目代号是 Aurora-17。"})
        manager.observe_turn({"role": "assistant", "content": "进行普通分析。" * 30})
        manager.compress()

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "lifecycle-state.json"
            manager.save_state(path)
            restored = ContextLifecycleManager.load_state(
                path,
                pruner=ContextPrunerV1(),
            )

        old_ids = {event.event_id for event in restored.events}
        restored.observe_turn({"role": "assistant", "content": "继续执行。"})
        new_id = restored.events[-1].event_id

        self.assertNotIn(new_id, old_ids)
        self.assertEqual(manager.archive_count, restored.archive_count)
        self.assertEqual(manager.turn_count + 1, restored.turn_count)

    def test_discard_can_purge_archive_and_does_not_reappear(self):
        manager = ContextLifecycleManager(
            task_state="清理过期记录",
            pruner=ContextPrunerV1(),
        )
        manager.observe_turn({"role": "assistant", "content": "过期草稿内容。" * 40})
        manager.compress(budget=ContextBudget(20, 30, 10))
        source_id = manager.context_events[0].event_id

        snapshot = manager.discard([source_id], purge_archive=True, reason="expired")
        manager.observe_turn({"role": "assistant", "content": "新记录。"})

        self.assertFalse(
            any(source_id in chunk.source_event_ids for chunk in snapshot.chunks)
        )
        self.assertFalse(
            any(source_id in chunk.source_event_ids for chunk in manager.snapshot().chunks)
        )
        self.assertEqual(ContextStatus.DISCARDED, manager._status_by_id[source_id])


class SelectiveRecoveryTest(unittest.TestCase):
    def _compressed_manager(self) -> ContextLifecycleManager:
        manager = ContextLifecycleManager(
            task_state="记住关键项目事实",
            recovery_limit=1,
            recovery_ttl_events=2,
        )
        for turn in [
            {"role": "user", "content": "关键事实：项目代号是蓝鲸，预算是 42 万元。"},
            {"role": "assistant", "content": "先检查一些无关背景资料。" * 10},
            {"role": "user", "content": "观察：工具返回普通背景信息。" * 8},
            {"role": "assistant", "content": "继续进行一般分析。" * 10},
        ]:
            manager.observe_turn(turn)
        manager.compress()
        return manager

    def test_compression_archives_original_sources(self):
        manager = self._compressed_manager()
        self.assertGreater(manager.archive_count, 0)
        archived = manager.archive_store.entries()
        self.assertTrue(all(entry.item.status == ContextStatus.ARCHIVED for entry in archived))
        self.assertTrue(all(entry.item.source_event_ids for entry in archived))

    def test_recovery_selects_only_relevant_source(self):
        manager = self._compressed_manager()
        snapshot = manager.recover(
            "missing_fact",
            query="项目代号蓝鲸和预算 42 万元",
            limit=1,
        )
        recovered = [
            item for item in snapshot.chunks if item.status == ContextStatus.RECOVERED
        ]
        self.assertEqual(len(recovered), 1)
        self.assertIn("蓝鲸", recovered[0].text)
        self.assertLess(len(recovered), len(manager.context_events))
        self.assertEqual(manager.recovery_events[-1]["source_event_ids"], list(recovered[0].source_event_ids))

    def test_recovered_context_expires_and_is_compressed_again(self):
        manager = self._compressed_manager()
        manager.recover("missing_fact", query="蓝鲸", limit=1)
        self.assertEqual(manager.snapshot().recovered_items, 1)
        for i in range(3):
            manager.observe_turn({"role": "assistant", "content": f"恢复后的新事件 {i}"})
        snapshot = manager.compress()
        self.assertEqual(snapshot.recovered_items, 0)

    def test_recovery_does_not_hide_other_facts_in_structured_memory(self):
        manager = ContextLifecycleManager(
            task_state="给出版本、负责人和回滚阈值",
            pruner=ContextPrunerV1(),
            recovery_limit=1,
        )
        facts = [
            "发布版本是 v2.4.1。",
            "负责人是 Lin。",
            "错误率超过 2% 时回滚。",
        ]
        for fact in facts:
            manager.observe_turn(
                {"role": "user", "content": f"观察：read 结果：{fact}"}
            )

        manager.compress(budget=ContextBudget(120, 180, 140))
        snapshot = manager.recover("missing_fact", query="负责人 Lin", limit=1)
        visible = "\n".join(chunk.text for chunk in snapshot.chunks)

        self.assertEqual(1, snapshot.recovered_items)
        self.assertTrue(
            any(chunk.metadata.get("structured_memory") for chunk in snapshot.chunks)
        )
        for fact in facts:
            self.assertIn(fact, visible)


class AgentIntegrationTest(unittest.TestCase):
    def test_history_pruner_uses_lifecycle_manager(self):
        history = HistoryManager(
            "system",
            "记住项目代号蓝鲸",
            method="pruner_v1",
        )
        for i in range(4):
            history.add("assistant", f"第 {i} 步分析：" + "背景内容" * 20)
            history.add("user", f"观察：第 {i} 步工具结果")
        history.apply_compression()
        self.assertGreater(history.archive_count, 0)
        self.assertTrue(history.lifecycle_records)
        self.assertTrue(
            any(turn.get("_source_event_ids") for turn in history.turns)
        )
        self.assertTrue(
            all(set(message) == {"role", "content"} for message in history.build_messages())
        )

    def test_react_records_boundaries_and_serializes_lifecycle(self):
        task = Task(
            task_id="stage1",
            scenario="lifecycle",
            question="找出项目代号",
            docs={"d1": "项目代号是蓝鲸"},
            golden_facts=["蓝鲸"],
        )
        client = _ScriptedClient()
        agent = ReActAgent(
            client,
            KnowledgeBase(task.docs),
            max_turns=5,
            method="pruner_v1",
        )
        record = agent.run(task)
        kinds = [event.kind for event in record.runtime_events]
        self.assertIn(ContextEventKind.TASK_STARTED, kinds)
        self.assertIn(ContextEventKind.MODEL_INPUT, kinds)
        self.assertIn(ContextEventKind.TOOL_CALL, kinds)
        self.assertIn(ContextEventKind.TOOL_RESULT, kinds)
        self.assertIn(ContextEventKind.TASK_FINISHED, kinds)
        self.assertGreater(record.archive_count, 0)
        with tempfile.TemporaryDirectory() as directory:
            write_run(Path(directory), record, "r00")
            data = json.loads(
                (Path(directory) / "stage1.r00.summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(data["archive_count"], record.archive_count)
            self.assertTrue(data["lifecycle_records"])

    def test_react_recovers_selectively_after_parse_failure(self):
        task = Task(
            task_id="recovery-loop",
            scenario="recovery",
            question="找出项目代号",
            docs={"d1": "项目代号是蓝鲸"},
            golden_facts=["蓝鲸"],
        )
        record = ReActAgent(
            _RecoveryClient(),
            KnowledgeBase(task.docs),
            max_turns=6,
            method="pruner_v1",
        ).run(task)
        self.assertTrue(record.success)
        self.assertEqual(record.recovery_count, 1)
        self.assertGreater(record.recovered_tokens, 0)
        self.assertTrue(record.recovery_events[0]["source_event_ids"])
        self.assertEqual(record.recovery_events[0]["reason"], "parse_failed")


class _ScriptedClient(LLMClient):
    def __init__(self):
        self.calls = 0

    def reset(self) -> None:
        self.calls = 0

    def complete(self, messages: list[dict], temperature: float = 0.0) -> str:
        self.calls += 1
        if self.calls == 1:
            return '{"thought":"先检索","action":{"name":"search","args":{"keyword":"代号"}}}'
        if self.calls == 2:
            return '{"thought":"读取证据","action":{"name":"read","args":{"doc_id":"d1"}}}'
        return '{"thought":"完成","final_answer":"项目代号是蓝鲸"}'


class _RecoveryClient(LLMClient):
    def __init__(self):
        self.calls = 0

    def reset(self) -> None:
        self.calls = 0

    def complete(self, messages: list[dict], temperature: float = 0.0) -> str:
        self.calls += 1
        if self.calls == 1:
            return '{"thought":"先检索","action":{"name":"search","args":{"keyword":"代号"}}}'
        if self.calls == 2:
            return '{"thought":"读取证据","action":{"name":"read","args":{"doc_id":"d1"}}}'
        if self.calls == 3:
            return "缺少证据：这一次故意输出无法解析的内容"
        return '{"thought":"已恢复证据","final_answer":"项目代号是蓝鲸"}'


if __name__ == "__main__":
    unittest.main()
